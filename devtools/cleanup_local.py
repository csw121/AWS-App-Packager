"""Explicit, selective recovery cleanup for this tool's local jobs only."""

import argparse
import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aws_app_packager import config  # noqa: E402
from aws_app_packager.jobs import BusyError, JobManager  # noqa: E402
from aws_app_packager.runtime_check import cleanup_owned  # noqa: E402

MAX_LISTED_JOBS = 100
MAX_SCANNED_ENTRIES = 20_000
MAX_SCAN_SECONDS = 30


class CleanupError(ValueError):
    """All messages are fixed classifications; user path/content is never echoed."""


def _job_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", value
    ):
        raise CleanupError("작업 ID는 32자리 hex 또는 UUID 형식이어야 합니다.")
    return UUID(value).hex


def _no_links(path: Path) -> None:
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise CleanupError("정리 경로에 링크 또는 junction/reparse point가 있어 중단했습니다.")


def _jobs_root() -> Path:
    work = config.WORK_DIR.absolute()
    _no_links(work)
    root = work / "jobs"
    _no_links(root)
    if root.resolve().parent != work.resolve():
        raise CleanupError("도구 소유 작업 경계를 확인하지 못했습니다.")
    return root


def _job_directory(job_id: str) -> Path:
    root = _jobs_root()
    target = root / job_id
    _no_links(target)
    if not target.is_dir() or target.resolve().parent != root.resolve():
        raise CleanupError("도구 소유 작업 폴더를 찾을 수 없습니다.")
    return target


def _read_record(path: Path, *, max_bytes: int = 32_768) -> dict:
    _no_links(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or info.st_size > max_bytes:
        raise CleanupError("정리 기록의 형식 또는 크기를 확인해야 합니다.")
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise CleanupError("정리 기록 크기 제한에 도달했습니다.")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise CleanupError("정리 기록이 올바른 JSON 객체가 아닙니다.")
    return value


def list_jobs() -> list[dict[str, str]]:
    """Read stored state only; listing never invokes Docker and never prints paths."""
    root = _jobs_root()
    if not root.is_dir():
        return []
    rows = []
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    with os.scandir(root) as entries:
        for count, entry in enumerate(entries):
            if count >= MAX_SCANNED_ENTRIES or time.monotonic() > deadline:
                break
            if not re.fullmatch(r"[a-f0-9]{32}", entry.name):
                continue
            status = "NO_RUNTIME_RECORD"
            try:
                job = _job_directory(entry.name)
                record_path = job / "runtime-resources.json"
                if record_path.exists():
                    record = _read_record(record_path)
                    if record.get("job_id") != entry.name or record.get("owner") != "v0.1":
                        status = "NEEDS_ATTENTION"
                    elif record.get("cleaned") is True:
                        status = "CLEANED_RECORDED"
                    else:
                        status = "CLEANUP_REQUIRED"
            except (ValueError, OSError):
                status = "NEEDS_ATTENTION"
            rows.append({"job_id": entry.name, "status": status})
            if len(rows) == MAX_LISTED_JOBS:
                break
    return sorted(rows, key=lambda item: item["job_id"])


def _validated_snapshot(job: Path, job_id: str) -> Path | None:
    """Resolve every target and reject links before any recursive removal."""
    manifest = _read_record(job / "build-context-manifest.json", max_bytes=2 * 1024 * 1024)
    if manifest.get("job_id") != job_id or not isinstance(manifest.get("files"), dict):
        raise CleanupError("빌드 복사본의 도구 소유 manifest를 확인하지 못했습니다.")
    snapshot = job / "snapshot"
    _no_links(snapshot)
    if not snapshot.exists():
        return None
    expected = config.WORK_DIR.resolve() / "jobs" / job_id / "snapshot"
    resolved = snapshot.resolve(strict=True)
    if not snapshot.is_dir() or resolved != expected or resolved.parent != job.resolve():
        raise CleanupError("삭제 대상이 정확한 작업 snapshot 경계를 벗어났습니다.")
    count = 0
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    pending = [(snapshot, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth > 30:
            raise CleanupError("복사본 정리의 폴더 깊이 제한에 도달했습니다.")
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > MAX_SCANNED_ENTRIES or time.monotonic() > deadline:
                    raise CleanupError("복사본 정리의 항목 수 또는 시간 제한에 도달했습니다.")
                path = Path(entry.path)
                _no_links(path)
                if not path.resolve(strict=True).is_relative_to(resolved):
                    raise CleanupError("복사본 안에 외부 경로 참조가 있어 삭제를 중단했습니다.")
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    pending.append((path, depth + 1))
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise CleanupError("일반 파일 이외의 복사본 항목은 자동 삭제하지 않습니다.")
    return snapshot


def cleanup_job(job_id: str, *, remove_snapshot: bool = False) -> dict[str, str]:
    job_id = _job_id(job_id)
    job = _job_directory(job_id)
    _no_links(config.WORK_DIR / "active.lock")
    # Same OS lock as the UI worker; no cleanup during another managed operation.
    lease = JobManager(work_dir=config.WORK_DIR)._acquire_file_lock()
    try:
        if remove_snapshot:
            _validated_snapshot(job, job_id)
        status = cleanup_owned(job_id)
        result = {"job_id": job_id, "runtime_status": status, "snapshot_status": "NOT_REQUESTED"}
        if not remove_snapshot:
            return result
        if status not in {"CLEANED", "NOT_NEEDED"}:
            result["snapshot_status"] = "PRESERVED"
            return result
        # Recheck after Docker calls, immediately before deleting the exact target.
        snapshot = _validated_snapshot(_job_directory(job_id), job_id)
        if snapshot is None:
            result["snapshot_status"] = "ABSENT"
        else:
            shutil.rmtree(snapshot)
            result["snapshot_status"] = "REMOVED"
        return result
    finally:
        lease.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="도구 소유 로컬 시험 자원만 선택 정리합니다.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--list", action="store_true", help="저장된 작업 ID/정리 상태 최대 100개; Docker 조회 없음"
    )
    action.add_argument("--job", metavar="UUID", help="명시한 작업의 container/network만 정리")
    parser.add_argument("--remove-snapshot", action="store_true", help="정리 성공 뒤 해당 snapshot만 삭제")
    args = parser.parse_args(argv)
    if args.list and args.remove_snapshot:
        parser.error("--remove-snapshot은 --job과 함께 사용해야 합니다.")
    try:
        result = list_jobs() if args.list else cleanup_job(args.job, remove_snapshot=args.remove_snapshot)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result["runtime_status"] not in {"CLEANED", "NOT_NEEDED"}:
            return 2
        return 0
    except BusyError:
        print("BUSY: 실행 중인 도구 작업이 있습니다. 완료 또는 취소 후 다시 시도하세요.")
        return 2
    except CleanupError as error:
        print("BLOCKED: " + str(error))
        return 2
    except (OSError, ValueError, RuntimeError):
        print("BLOCKED: 정리 기록·경로·권한을 확인하지 못했습니다. 삭제를 진행하지 마세요.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
