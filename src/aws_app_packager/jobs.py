"""Single-user worker and process lock. Workers never call Streamlit."""

import json
import math
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .config import WORK_DIR
from .models import ImageArtifact, utc_now
from .progress import infer_performance_note, infer_phase
from .redaction import redact

if TYPE_CHECKING:
    from .process_runner import ProcessProgress

LIVE_LOG_LIMIT = 16_384


def _seconds(value: float) -> float:
    number = float(value)
    return max(0.0, min(number, 31_536_000.0)) if math.isfinite(number) else 0.0


def _utc_timestamp(value: str | None) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(UTC).isoformat() if stamp.tzinfo else None
    except ValueError:
        return None


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class BusyError(RuntimeError):
    pass


class JobPersistenceError(RuntimeError):
    status = "PERSISTENCE_FAILED"


class JobManager:
    def __init__(self, work_dir: Path = WORK_DIR):
        self.work_dir = work_dir
        self._mutex = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._state: dict[str, Any] = {"status": "IDLE", "stage": "준비", "events": []}
        self._result: Any = None
        self._context: dict[str, Any] = {}
        self._last_progress_save = float("-inf")

    def _acquire_file_lock(self):
        self.work_dir.mkdir(parents=True, exist_ok=True)
        stream = (self.work_dir / "active.lock").open("a+b")
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise BusyError("다른 작업이 진행 중입니다. 완료 또는 취소 후 다시 시도하세요.") from exc
        return stream

    def start(self, action: Callable, *, kind: str, context: dict | None = None) -> str:
        with self._mutex:
            if self._thread and self._thread.is_alive():
                raise BusyError("한 번에 한 작업만 실행할 수 있습니다.")
            lease = self._acquire_file_lock()
            job_id = uuid4().hex
            self._cancel = threading.Event()
            self._result = None
            self._context = dict(context or {})
            self._last_progress_save = float("-inf")
            now = utc_now()
            self._state = {
                "job_id": job_id,
                "kind": kind,
                "status": "RUNNING",
                "outcome": "NOT_RUN",
                "stage": "준비",
                "started_at": now,
                "stage_started_at": now,
                "updated_at": now,
                "stage_history": [{"stage": "준비", "started_at": now}],
                "process": None,
                "live_log": "",
                "events": [],
            }
            def execute():
                try:
                    result = action(job_id, self._cancel, self.stage)
                    with self._mutex:
                        self._result = result
                        # A failed HTTP test stays a failed job even if a result object exists.
                        check = result.get("check") if isinstance(result, dict) else None
                        outcome = getattr(check, "status", None)
                        cleanup = getattr(check, "cleanup_status", None)
                        persistence_failed = self._state.get("error_kind") == "PERSISTENCE_FAILED"
                        if not persistence_failed:
                            self._state["status"] = (
                                "CANCELLED"
                                if self._cancel.is_set()
                                else "FAILED"
                                if check is not None and (outcome != "PASS" or cleanup != "CLEANED")
                                else "DONE"
                            )
                            if self._cancel.is_set():
                                self._state["outcome"] = "CANCELLED"
                            elif check is not None:
                                self._state["outcome"] = getattr(check, "outcome", None) or outcome
                        if (
                            not persistence_failed and check is not None
                            and outcome == "PASS" and cleanup != "CLEANED"
                        ):
                            self._state["error"] = (
                                "HTTP 응답은 통과했지만 시험 자원 정리가 끝나지 않았습니다."
                            )
                            self._state["error_kind"] = "CLEANUP_INCOMPLETE"
                            self._state["next_action"] = "소유 자원의 정리 상태를 확인한 뒤 다시 시험하세요."
                        if isinstance(result, dict):
                            self._state["result"] = {
                                **self._state.get("result", {}),
                                **{key: item.model_dump(mode="json")
                                   for key, item in result.items()
                                   if hasattr(item, "model_dump")},
                            }
                except Exception as exc:
                    with self._mutex:
                        self._record_error(exc)
                finally:
                    try:
                        with self._mutex:
                            self._state["finished_at"] = utc_now()
                            self._state["updated_at"] = self._state["finished_at"]
                            if self._state.get("process"):
                                self._state["process"]["running"] = False
                            try:
                                self._persist()
                            except JobPersistenceError:
                                # _persist keeps a usable FAILED state in memory.
                                # A failed disk write must not leak the worker lease.
                                pass
                    finally:
                        lease.close()

            try:
                self._persist()
                self._thread = threading.Thread(target=execute, name=f"packager-{job_id}", daemon=True)
                self._thread.start()
            except Exception as exc:
                try:
                    self._thread = None
                    self._record_error(exc)
                    self._state["finished_at"] = utc_now()
                    self._state["updated_at"] = self._state["finished_at"]
                    if not isinstance(exc, JobPersistenceError):
                        try:
                            self._persist()
                        except JobPersistenceError:
                            pass
                finally:
                    lease.close()
                raise RuntimeError(self._state["error"]) from exc
            return job_id

    def _record_error(self, exc: Exception):
        kind = getattr(exc, "status", type(exc).__name__)
        if self._state.get("error_kind") == "PERSISTENCE_FAILED" and kind != "PERSISTENCE_FAILED":
            return  # A later cancellation/cleanup error must not hide a failed journal.
        self._state["updated_at"] = utc_now()
        self._state["status"] = (
            "FAILED" if kind == "PERSISTENCE_FAILED" else "CANCELLED" if self._cancel.is_set() else "FAILED"
        )
        self._state["error"] = redact(str(exc))
        self._state["error_kind"] = kind
        if self._cancel.is_set() and kind != "PERSISTENCE_FAILED":
            self._state["outcome"] = "CANCELLED"
        elif kind == "PERSISTENCE_FAILED":
            self._state["outcome"] = "PERSISTENCE_FAILED"
        elif self._state.get("kind") == "build_and_test" and self._state.get("stage") in {
            "이미지 생성", "생성 이미지 식별정보 확인", "이미지 임시 쓰기 영역 메타데이터 준비",
        } and not self._state.get("result", {}).get("artifact"):
            self._state["outcome"] = "IMAGE_BUILD_FAILED"
        else:
            self._state["outcome"] = "BLOCKED"
        self._state["logs"] = redact(getattr(exc, "logs", ""), 65_536)
        self._state["next_action"] = (
            "작업 폴더의 여유 공간·쓰기 권한과 소유 Docker 자원의 정리 상태를 확인하세요."
            if kind == "PERSISTENCE_FAILED"
            else "기술 상세에서 원인을 확인하고 입력 또는 환경을 수정한 뒤 재시도하세요."
        )

    def _persist(self):
        try:
            self._save()
        except Exception as exc:
            failure = JobPersistenceError("작업 기록을 저장하지 못했습니다: " + redact(str(exc)))
            self._record_error(failure)
            self._cancel.set()
            raise failure from exc

    def _save(self):
        job_id = self._state.get("job_id")
        if job_id:
            atomic_json(self.work_dir / "jobs" / f"{job_id}.json", self._state)

    def record_artifact(self, artifact: ImageArtifact) -> None:
        """Retain a confirmed build even if the later runtime fails or raises."""
        with self._mutex:
            if self._state.get("status") != "RUNNING":
                return
            self._state.setdefault("result", {})["artifact"] = artifact.model_dump(mode="json")
            self._result = {"artifact": artifact}
            self._state["updated_at"] = utc_now()
            self._persist()

    def stage(self, text: str, *_args, **_kwargs):
        with self._mutex:
            if self._state["status"] != "RUNNING":
                return
            message = redact(str(text), 512)
            if message == self._state["stage"]:
                return
            now = utc_now()
            self._state["stage"] = message
            self._state["stage_started_at"] = now
            self._state["updated_at"] = now
            self._state["events"] = (self._state["events"] + [message])[-24:]
            self._state["stage_history"] = (
                self._state["stage_history"] + [{"stage": message, "started_at": now}]
            )[-24:]
            self._state["process"] = None
            self._last_progress_save = float("-inf")
            self._persist()

    def process_progress(self, update: "ProcessProgress") -> None:
        """Keep a live snapshot; persist bounded, masked observations at most once per second."""
        with self._mutex:
            if self._state["status"] != "RUNNING":
                return
            now = utc_now()
            previous = self._state.get("process") or {}
            elapsed = _seconds(update.elapsed_seconds)
            new_command = (
                not previous
                or (update.running and not previous.get("running"))
                or elapsed < previous.get("elapsed_seconds", 0)
            )
            if new_command:
                previous = {}
            tail = redact(update.output_tail, LIVE_LOG_LIMIT)
            phase = infer_phase(tail) or previous.get("phase_label")
            note = infer_performance_note(tail) or previous.get("performance_note")
            process = {
                "started_at": previous.get("started_at") or (
                    datetime.fromisoformat(now) - timedelta(seconds=elapsed)
                ).isoformat(),
                "elapsed_seconds": elapsed,
                "timeout_seconds": _seconds(update.timeout_seconds),
                "output_tail": tail,
                "last_output_at": _utc_timestamp(update.last_output_at),
                "output_lines": max(0, int(update.output_lines)),
                "running": bool(update.running),
                "returncode": int(update.returncode) if update.returncode is not None else None,
                "phase_label": redact(phase, 256) if phase else None,
                "performance_note": redact(note, 512) if note else None,
            }
            self._state["process"] = process
            self._state["updated_at"] = now
            if tail.strip():
                self._state["live_log"] = tail
            tick = time.monotonic()
            transition = (
                new_command or not process["running"]
                or phase != previous.get("phase_label")
                or note != previous.get("performance_note")
            )
            if transition or tick - self._last_progress_save >= 1.0:
                self._persist()
                self._last_progress_save = tick

    def cancel(self):
        with self._mutex:
            try:
                if self._state["status"] == "RUNNING" and not self._state.get("cancel_requested_at"):
                    now = utc_now()
                    self._state["cancel_requested_at"] = now
                    self._state["updated_at"] = now
                    try:
                        self._persist()
                    except JobPersistenceError:
                        pass  # Report the failed journal in memory; cancellation must still work.
            finally:
                self._cancel.set()

    def snapshot(self) -> dict:
        with self._mutex:
            return json.loads(json.dumps(self._state))

    def result(self):
        with self._mutex:
            return self._result

    def context(self):
        with self._mutex:
            return dict(self._context)

    def wait(self, timeout=5):
        if self._thread:
            self._thread.join(timeout)


MANAGER = JobManager()
