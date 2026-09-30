"""Application orchestration; UI cannot invoke Docker directly."""

import json
from pathlib import Path

from .builders import build_image
from .builders.base import assert_same_image
from .config import ROOT
from .jobs import MANAGER
from .models import ImageArtifact, RuntimeCheck, RuntimeConditions, fingerprint
from .project_input import assess_project, current_fingerprint, prepare_build_plan
from .runtime_approval import record_runtime_approval
from .runtime_check import run_check
from .terraform_export import export_bundle, save_image_tar


def sample_marker(root: Path) -> str | None:
    markers = {"spring-http": "packager-spring-v1", "docker-http": "packager-python-v1"}
    for name, marker in markers.items():
        if root.resolve() == (ROOT / "samples" / name).resolve():
            return marker
    return None


def start_build(assessment, conditions, *, build_approved: bool, runtime_approved: bool):
    if not build_approved or not runtime_approved:
        raise ValueError("빌드와 로컬 실행 시험 승인을 각각 확인하세요.")
    if assessment.blockers or assessment.method == "unsupported":
        raise ValueError("입력의 차단사항을 해결해야 합니다.")
    # A widget edit during a running job must not change what was approved.
    assessment = assessment.model_copy(deep=True)
    conditions = conditions.model_copy(deep=True)

    def action(job_id, cancel, stage):
        stage("파일 준비")
        plan = prepare_build_plan(assessment, job_id, build_approved=True)
        if conditions.network_mode == "approved_project_bridge":
            record_runtime_approval(plan, conditions, execution_job_id=job_id, runtime_approved=True)
        artifact = build_image(plan, cancel=cancel, on_stage=stage, on_progress=MANAGER.process_progress)
        MANAGER.record_artifact(artifact)
        check = run_check(artifact, conditions, cancel=cancel, on_stage=stage, runtime_approved=True,
                          execution_job_id=job_id)
        return {"artifact": artifact, "check": check, "plan": plan}

    return MANAGER.start(
        action, kind="build_and_test", context={"assessment": assessment, "conditions": conditions}
    )


def start_retest(assessment, artifact, conditions, *, runtime_approved):
    if not runtime_approved:
        raise ValueError("새 조건의 로컬 실행 시험을 승인하세요.")
    if current_fingerprint(assessment.root) != artifact.source_fingerprint:
        raise ValueError("소스가 변경되었습니다. 앱 확인과 빌드 승인을 다시 진행하세요.")
    assessment = assessment.model_copy(deep=True)
    artifact = artifact.model_copy(deep=True)
    conditions = conditions.model_copy(deep=True)

    def action(job_id, cancel, stage):
        if current_fingerprint(assessment.root) != artifact.source_fingerprint:
            raise ValueError("승인 후 소스가 변경되었습니다. 다시 확인·승인하세요.")
        if conditions.network_mode == "approved_project_bridge":
            record_runtime_approval(artifact, conditions, execution_job_id=job_id, runtime_approved=True)
        MANAGER.record_artifact(artifact)
        check = run_check(artifact, conditions, cancel=cancel, on_stage=stage, runtime_approved=True,
                          execution_job_id=job_id)
        return {"artifact": artifact, "check": check}

    return MANAGER.start(
        action,
        kind="retest",
        context={"assessment": assessment, "conditions": conditions, "artifact": artifact},
    )


def check_is_current(artifact: ImageArtifact, check: RuntimeCheck, conditions: RuntimeConditions) -> bool:
    return (
        check.status == "PASS"
        and check.job_id == artifact.job_id
        and check.http_status == 200
        and bool(check.finished_at)
        and check.cleanup_status == "CLEANED"
        and (not conditions.restart or check.restart_passed)
        and artifact.image_id == check.image_id
        and artifact.source_fingerprint == check.source_fingerprint
        and check.conditions_fingerprint == fingerprint(check.conditions)
        and check.conditions_fingerprint == fingerprint(conditions)
    )


def export_current(assessment, artifact, check, conditions, spec):
    if not check_is_current(artifact, check, conditions):
        raise ValueError("선택 조건에서 통과한 최신 HTTP 시험이 필요합니다.")
    if current_fingerprint(assessment.root) != artifact.source_fingerprint:
        raise ValueError("소스 변경으로 시험 결과가 오래되었습니다. 다시 빌드하세요.")
    assert_same_image(artifact)
    return export_bundle(artifact, check, spec)


def start_tar(artifact, destination):
    def action(_job_id, cancel, stage):
        stage("이미지를 디스크 tar로 저장 중")
        result = save_image_tar(
            artifact, destination, cancel=cancel, on_progress=MANAGER.process_progress, on_stage=stage,
        )
        return {"tar": result}

    return MANAGER.start(action, kind="save_image")


def diagnostic_report(assessment, check=None) -> bytes:
    """Diagnostic-only output never claims a tested deployment bundle."""
    from .redaction import redact

    state = MANAGER.snapshot()
    report = {
        "kind": "DIAGNOSTIC_ONLY",
        "aws_status": "AWS_NOT_TESTED",
        "source_fingerprint": assessment.source_fingerprint,
        "method": assessment.method,
        "blockers": assessment.blockers,
        "warnings": assessment.warnings,
        "environment_names_only": assessment.environment_names,
        "build_stage": state.get("stage", "NOT_RUN"),
        "error": state.get("error", ""),
        "runtime_status": check.status if check else "NOT_RUN",
        "runtime_message": check.message if check else "",
    }

    def sanitized(value):
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [sanitized(item) for item in value]
        if isinstance(value, dict):
            return {key: sanitized(item) for key, item in value.items()}
        return value

    return json.dumps(sanitized(report), ensure_ascii=False, indent=2).encode("utf-8")


__all__ = ["assess_project", "start_build", "start_retest", "export_current", "start_tar"]
