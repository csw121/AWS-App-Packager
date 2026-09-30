"""Bind an explicit UI runtime approval to its source, image, conditions and job."""

import json
import re
from pathlib import Path

from . import config
from .models import BuildPlan, ImageArtifact, RuntimeConditions, fingerprint, utc_now
from .project_input import _check_ancestors, _scan, verify_plan


def _read_record(path: Path) -> dict:
    _check_ancestors(path)
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("제한된 크기의 도구 소유 승인·빌드 기록이 필요합니다.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("승인·빌드 기록 형식이 잘못되었습니다.")
    return value


def _job_directory(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("잘못된 실행 작업 식별자입니다.")
    path = config.WORK_DIR / "jobs" / job_id
    _check_creation_path(path)
    return path


def _check_creation_path(path: Path) -> None:
    """Check every existing ancestor, including an existing or dangling link leaf."""
    candidate = path
    while True:
        try:
            candidate.lstat()
        except FileNotFoundError:
            candidate = candidate.parent
        else:
            break
    _check_ancestors(candidate)


def verify_image_provenance(artifact: ImageArtifact, *, verify_snapshot=False) -> dict:
    """Read only owned build evidence; never execute code during verification."""
    job = _job_directory(artifact.job_id)
    manifest_path = job / "build-context-manifest.json"
    _check_ancestors(artifact.manifest_path)
    if artifact.manifest_path.resolve() != manifest_path.resolve():
        raise ValueError("현재 작업공간의 도구 소유 빌드 기록만 사용할 수 있습니다.")
    saved = ImageArtifact.model_validate(_read_record(job / "image.json"))
    evidence = _read_record(job / "build-result.json")
    manifest = _read_record(manifest_path)
    if (saved != artifact or manifest.get("job_id") != artifact.job_id
            or manifest.get("source_fingerprint") != artifact.source_fingerprint
            or fingerprint({"files": manifest.get("files"), "excluded": manifest.get("excluded")})
            != artifact.source_fingerprint or evidence.get("job_id") != artifact.job_id
            or evidence.get("source_fingerprint") != artifact.source_fingerprint
            or evidence.get("method") != artifact.build_method
            or evidence.get("local_tag") != artifact.local_tag
            or evidence.get("image_id") != artifact.image_id
            or evidence.get("image_inspect_status") != "PASS" or evidence.get("returncode") != 0
            or evidence.get("cancelled") is not False or evidence.get("timed_out") is not False):
        raise ValueError("생성 이미지·소스·빌드 성공 기록이 일치하지 않습니다.")
    if artifact.build_method == "paketo_java":
        final = evidence.get("finalization", {})
        if (final.get("status") != "PASS" or final.get("kind") != "paketo_tmp_volume_v1"
                or final.get("final_image_id") != artifact.image_id):
            raise ValueError("Java 최종 이미지의 임시 쓰기 영역 준비 기록이 필요합니다.")
    if verify_snapshot:
        snapshot = job / "snapshot"
        _check_ancestors(snapshot)
        scanned = _scan(snapshot, apply_exclusions=False)
        if scanned.blockers or scanned.files != manifest.get("files"):
            raise ValueError("승인한 빌드 복사본이 변경되었습니다. 새로 승인·빌드하세요.")
    return manifest


def _check_conditions(conditions: RuntimeConditions) -> None:
    if (conditions.network_mode != "approved_project_bridge" or conditions.container_port != 8080
            or not conditions.restart):
        raise ValueError("승인된 프로젝트 시험은 포트 8080과 재시작 확인을 사용하는 일반 bridge여야 합니다.")


def record_runtime_approval(subject: BuildPlan | ImageArtifact, conditions: RuntimeConditions,
                            *, execution_job_id: str, runtime_approved: bool) -> Path:
    """Called only after explicit build/runtime approval, before build or retest."""
    if not runtime_approved:
        raise ValueError("이미지와 실행조건에 대한 명시적인 로컬 실행 승인이 필요합니다.")
    _check_conditions(conditions)
    job = _job_directory(execution_job_id)
    if isinstance(subject, BuildPlan):
        if execution_job_id != subject.job_id:
            raise ValueError("새 이미지의 빌드 작업과 최초 실행 작업이 일치해야 합니다.")
        verify_plan(subject)
        manifest = _read_record(subject.manifest_path)
        image_id = None
    else:
        manifest = verify_image_provenance(subject, verify_snapshot=True)
        image_id = subject.image_id
    build_approval = manifest.get("approval_fingerprint")
    if not isinstance(build_approval, str) or not re.fullmatch(r"[a-f0-9]{64}", build_approval):
        raise ValueError("빌드 승인의 지문을 확인할 수 없습니다.")
    record = {
        "schema_version": "0.1.0", "execution_job_id": execution_job_id,
        "build_job_id": subject.job_id, "source_fingerprint": subject.source_fingerprint,
        "conditions_fingerprint": fingerprint(conditions), "manifest_fingerprint": fingerprint(manifest),
        "build_approval_fingerprint": build_approval, "image_id": image_id,
        "runtime_approved": True, "approved_at": utc_now(),
    }
    record["approval_fingerprint"] = fingerprint(record)
    job.mkdir(parents=True, exist_ok=True)
    _check_ancestors(job)
    path = job / "runtime-approval.json"
    _check_creation_path(path)
    # A new execution job needs a new approval; never silently replace an old one.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
    return path


def verify_runtime_approval(artifact: ImageArtifact, conditions: RuntimeConditions,
                            *, execution_job_id: str) -> None:
    _check_conditions(conditions)
    job = _job_directory(execution_job_id)
    approval = _read_record(job / "runtime-approval.json")
    recorded_fingerprint = approval.pop("approval_fingerprint", None)
    if recorded_fingerprint != fingerprint(approval) or approval.get("runtime_approved") is not True:
        raise ValueError("로컬 실행 승인 기록의 지문이 일치하지 않습니다.")
    manifest = verify_image_provenance(artifact, verify_snapshot=True)
    expected = {
        "execution_job_id": execution_job_id, "build_job_id": artifact.job_id,
        "source_fingerprint": artifact.source_fingerprint,
        "conditions_fingerprint": fingerprint(conditions), "manifest_fingerprint": fingerprint(manifest),
        "build_approval_fingerprint": manifest.get("approval_fingerprint"),
    }
    if any(approval.get(key) != value for key, value in expected.items()):
        raise ValueError("승인한 소스·빌드·실행 작업·실행조건과 현재 요청이 일치하지 않습니다.")
    approved_image = approval.get("image_id")
    if (approved_image is None and execution_job_id != artifact.job_id
            or approved_image is not None and approved_image != artifact.image_id):
        raise ValueError("재시험을 승인한 실제 이미지 ID와 현재 이미지가 일치하지 않습니다.")
