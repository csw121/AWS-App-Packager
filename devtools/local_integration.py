"""Opt-in, authored samples only. Records actual outcomes; never runs AWS/Terraform."""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aws_app_packager import config  # noqa: E402
from aws_app_packager.builders import build_image  # noqa: E402
from aws_app_packager.builders.base import BuildError  # noqa: E402
from aws_app_packager.jobs import MANAGER  # noqa: E402
from aws_app_packager.models import DeploymentSpec, ImageArtifact, RuntimeConditions, utc_now  # noqa: E402
from aws_app_packager.preflight import check_environment  # noqa: E402
from aws_app_packager.project_input import assess_project, prepare_build_plan  # noqa: E402
from aws_app_packager.redaction import redact  # noqa: E402
from aws_app_packager.runtime_check import run_check  # noqa: E402
from aws_app_packager.terraform_export import export_bundle  # noqa: E402
from aws_app_packager.trusted_samples import TRUSTED_SAMPLES  # noqa: E402

SAMPLES = {name: reviewed[1] for name, reviewed in TRUSTED_SAMPLES.items()}
PRESETS = ("small", "medium", "large")


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _previous_builds(path: Path | None) -> dict:
    """Resume only recorded builds of the unchanged, authored samples."""
    if path is None:
        return {}
    resolved = path.resolve(strict=True)
    if resolved.parent != (config.WORK_DIR / "integration").resolve() or resolved.stat().st_size > 2_000_000:
        raise ValueError("이 작업공간의 실제 샘플 시험 기록만 재사용할 수 있습니다.")
    report = json.loads(resolved.read_text(encoding="utf-8"))
    if report.get("kind") != "REAL_LOCAL_INTEGRATION":
        raise ValueError("실제 샘플 시험 기록이 아닙니다.")
    artifacts = {}
    for item in report.get("samples", []):
        name = item.get("sample")
        if name not in SAMPLES or not item.get("artifact"):
            continue
        artifact = ImageArtifact.model_validate(item["artifact"])
        if artifact.project_label != name:
            # Renamed-copy tests are rebuilt; they are never canonical resume inputs.
            continue
        assessment = assess_project(config.ROOT / "samples" / name)
        job = config.WORK_DIR / "jobs" / artifact.job_id
        saved = ImageArtifact.model_validate_json((job / "image.json").read_text(encoding="utf-8"))
        evidence = json.loads((job / "build-result.json").read_text(encoding="utf-8"))
        if (assessment.blockers or assessment.source_fingerprint != artifact.source_fingerprint
                or artifact != saved
                or artifact.manifest_path.resolve() != (job / "build-context-manifest.json").resolve()
                or evidence.get("image_inspect_status") != "PASS"
                or evidence.get("image_id") != artifact.image_id):
            raise ValueError("자체 샘플의 소스·생성 이미지·빌드 기록이 일치하지 않습니다.")
        if artifact.build_method == "paketo_java":
            finalized = evidence.get("finalization", {})
            if (finalized.get("status") != "PASS" or finalized.get("kind") != "paketo_tmp_volume_v1"
                    or finalized.get("final_image_id") != artifact.image_id):
                raise ValueError("현재 Java 이미지 후처리 계약으로 다시 빌드해야 합니다.")
        artifacts[name] = artifact
    if not artifacts:
        raise ValueError("재시험할 실제 생성 이미지 기록이 없습니다.")
    return artifacts


def run_suite(*, samples=None, extended=True, reuse_builds_from=None) -> tuple[dict, Path]:
    if os.environ.get("RUN_LOCAL_CONTAINER_TESTS") != "1":
        raise RuntimeError("실제 자체 샘플 통합시험은 RUN_LOCAL_CONTAINER_TESTS=1 승인이 필요합니다.")
    # Share the application's OS lock, including when its Streamlit process is open.
    with MANAGER._acquire_file_lock():
        previous = _previous_builds(reuse_builds_from)
        return _run_suite(samples=samples, extended=extended, previous=previous,
                          previous_report=reuse_builds_from)


def _passed_and_cleaned(check, artifact) -> bool:
    """The next gate requires the complete actual runtime sequence, including cleanup."""
    mappings = {item.phase: item for item in check.port_evidence}
    return (
        check.status == "PASS" and check.cleanup_status == "CLEANED"
        and check.image_id == artifact.image_id and check.job_id == artifact.job_id
        and check.source_fingerprint == artifact.source_fingerprint
        and check.conditions.network_mode == "authored_sample_bridge"
        and check.image_verified and check.container_started
        and check.initial_http_status == 200 and check.restart_http_status == 200
        and check.restart_performed and check.restart_passed
        and check.network_driver == "bridge" and check.network_internal is False
        and check.host_port is not None and check.host_port > 0
        and [item.phase for item in check.port_evidence] == ["start", "restart"]
        and all(item.verified and item.host_port is not None and item.host_port > 0
                for item in mappings.values())
        and mappings["restart"].host_port == check.host_port
    )


def _aggregate_status(statuses) -> str:
    values = set(statuses)
    if values == {"PASS"}:
        return "PASS"
    # Preserve an environment block as its own outcome, never turn it into HTTP FAIL.
    if "ENVIRONMENT_BLOCKED" in values and values <= {"PASS", "ENVIRONMENT_BLOCKED", "NOT_RUN"}:
        return "ENVIRONMENT_BLOCKED"
    if "BLOCKED" in values and values <= {"PASS", "BLOCKED", "NOT_RUN"}:
        return "BLOCKED"
    if "CANCELLED" in values and values <= {"PASS", "CANCELLED", "NOT_RUN"}:
        return "CANCELLED"
    return "FAIL"


def _run_suite(*, samples=None, extended=True, previous=None, previous_report=None) -> tuple[dict, Path]:
    selected = list(SAMPLES if samples is None else samples)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(SAMPLES):
        raise ValueError("자체 작성한 spring-http/docker-http 샘플만 중복 없이 시험할 수 있습니다.")
    report = {
        "kind": "REAL_LOCAL_INTEGRATION", "started_at": utc_now(), "aws_status": "AWS_NOT_TESTED",
        "environment": check_environment(include_dev_tools=True), "samples": [],
        "scope": "BOTH_AUTHORED_SAMPLES" if set(selected) == set(SAMPLES) else "SELECTED_SAMPLES_ONLY",
        "network_profile": "authored_sample_bridge",
        "network_description": "user-defined normal bridge; publish only 127.0.0.1:<ephemeral>:8080/tcp",
        "initial_gate": "NOT_RUN",
        "extended": {"status": "NOT_RUN", "size_matrix": [], "bundles": []}, "status": "RUNNING",
    }
    if previous_report:
        report["build_evidence_report"] = str(previous_report.resolve())
    path = config.WORK_DIR / "integration" / f"result-{uuid.uuid4().hex}.json"
    state = report["environment"]
    _write(path, report)
    if not state["docker_ready"]:
        report["status"] = "ENVIRONMENT_BLOCKED"
        report["samples"] = [
            {"sample": name, "status": "ENVIRONMENT_BLOCKED", "failure_layer": "docker_preflight",
             "reason": state["blockers"]} for name in selected
        ]
        report["finished_at"] = utc_now()
        _write(path, report)
        return report, path

    def stage(item, name):
        item["stage"] = name
        print(f"{item['sample']} / {item.get('preset', 'medium')}: {name}", flush=True)
        _write(path, report)

    def test_image(item, artifact, preset):
        conditions = RuntimeConditions(
            preset=preset, container_port=8080, health_path="/health",
            expected_marker=SAMPLES[item["sample"]],
            timeout_seconds=120, restart=True, network_mode="authored_sample_bridge",
        )
        item.update(status="RUNNING", image_id=artifact.image_id, preset=preset)
        _write(path, report)
        check = run_check(artifact, conditions, runtime_approved=True,
                          on_stage=lambda name: stage(item, name))
        item["check"] = check.model_dump(mode="json")
        item["status"] = check.status
        item["failure_layer"] = check.failure_layer
        if check.status == "PASS" and not _passed_and_cleaned(check, artifact):
            item.update(status="FAIL", failure_layer="integration_evidence",
                        reason="PASS 표시와 전체 이미지·포트·HTTP·재시작·정리 증거가 일치하지 않습니다.")
        _write(path, report)
        return check

    def fail(item, exc, layer):
        item.update(status=getattr(exc, "status", "FAIL"), failure_layer=layer, reason=redact(str(exc)))
        if isinstance(exc, BuildError):
            item["logs"] = exc.logs
        _write(path, report)

    artifacts = {}
    for name in selected:
        item = {"sample": name, "variant": "canonical", "preset": "medium", "status": "NOT_RUN"}
        report["samples"].append(item)
        previous_artifact = (previous or {}).get(name)
        if name == "spring-http" and not state["pack_ready"] and previous_artifact is None:
            item.update(status="ENVIRONMENT_BLOCKED", failure_layer="build_preflight",
                        reason="새 Java 이미지 빌드에 필요한 pack CLI가 준비되지 않았습니다.")
            _write(path, report)
            continue
        try:
            stage(item, "자체 샘플 소스 확인")
            assessment = assess_project(config.ROOT / "samples" / name)
            if assessment.blockers:
                item.update(status="BLOCKED", failure_layer="source_approval", reason=assessment.blockers)
                _write(path, report)
                continue
            method, _, source_hash = TRUSTED_SAMPLES[name]
            if assessment.method != method or assessment.source_fingerprint != source_hash:
                item.update(status="BLOCKED", failure_layer="source_approval",
                            reason="검토한 자체 샘플 원본이 변경되어 빌드·실행하지 않습니다.")
                _write(path, report)
                continue
            if previous_artifact is None:
                plan = prepare_build_plan(assessment, uuid.uuid4().hex, build_approved=True)
                artifact = build_image(plan, on_stage=lambda name, item=item: stage(item, name))
                item["build_action"] = "BUILT_THIS_RUN"
            else:
                artifact = previous_artifact
                item["build_action"] = "RETEST_PREVIOUS_REAL_BUILD"
            item["artifact"] = artifact.model_dump(mode="json")
            artifacts[name] = artifact
            test_image(item, artifact, "medium")
        except Exception as exc:
            fail(item, exc, "runtime" if "artifact" in item else "build")

    # Both canonical samples must complete HTTP, restart and CLEANED before any
    # size retest or export. A subset/basic run cannot claim this extended gate.
    initial_passed = set(selected) == set(SAMPLES) and all(
        item["status"] == "PASS" for item in report["samples"]
    )
    report["initial_gate"] = "PASS" if initial_passed else "NOT_PASSED"
    if not extended:
        report["extended"]["reason"] = "--basic: 기본 중형 시험만 수행; 사양 재시험·내보내기 안 함"
    elif not initial_passed:
        report["extended"]["reason"] = "두 자체 샘플 모두 기본 시험 PASS 및 CLEANED가 확인되어야 진행 가능"
    else:
        report["extended"]["status"] = "RUNNING"
        _write(path, report)
        size_checks = []
        for name in SAMPLES:
            artifact = artifacts[name]
            for preset in PRESETS:
                item = {"sample": name, "preset": preset, "image_id": artifact.image_id, "status": "NOT_RUN"}
                report["extended"]["size_matrix"].append(item)
                try:
                    check = test_image(item, artifact, preset)
                    if item["status"] == "PASS":
                        size_checks.append((name, artifact, check))
                except Exception as exc:
                    fail(item, exc, "runtime")
        matrix = report["extended"]["size_matrix"]
        report["extended"]["matrix_gate"] = _aggregate_status(item["status"] for item in matrix)
        _write(path, report)
        if report["extended"]["matrix_gate"] == "PASS" and len(size_checks) == len(SAMPLES) * len(PRESETS):
            # Generate only after all six same-image trials pass and clean up.
            for name, artifact, check in size_checks:
                item = {"sample": name, "preset": check.conditions.preset,
                        "image_id": artifact.image_id, "status": "RUNNING"}
                report["extended"]["bundles"].append(item)
                _write(path, report)
                try:
                    spec = DeploymentSpec(
                        project_label=artifact.project_label, preset=check.conditions.preset,
                        container_port=check.conditions.container_port,
                        health_path=check.conditions.health_path,
                        environment=check.conditions.environment,
                    )
                    bundle = export_bundle(artifact, check, spec)
                    item.update(status="PASS", directory=str(bundle.directory), zip_path=str(bundle.zip_path),
                                local_status=bundle.local_status, aws_status=bundle.aws_status,
                                conditions_fingerprint=check.conditions_fingerprint)
                    _write(path, report)
                except Exception as exc:
                    fail(item, exc, "offline_export")
        else:
            report["extended"]["export_reason"] = "모든 사양의 PASS 및 CLEANED가 확인되지 않아 생성 안 함"
        report["extended"]["status"] = _aggregate_status(
            item["status"] for item in matrix + report["extended"]["bundles"]
        )
    outcomes = [item["status"] for item in report["samples"]]
    if report["extended"]["status"] != "NOT_RUN":
        outcomes.append(report["extended"]["status"])
    report["status"] = _aggregate_status(outcomes)
    report["finished_at"] = utc_now()
    _write(path, report)
    return report, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", choices=tuple(SAMPLES), action="append")
    parser.add_argument("--basic", action="store_true",
                        help="Only initial medium HTTP/restart/cleanup checks; no size matrix or exports")
    parser.add_argument("--reuse-builds-from", type=Path,
                        help="Retest unchanged authored samples built in a previous workspace report")
    args = parser.parse_args()
    report, path = run_suite(samples=args.sample, extended=not args.basic,
                            reuse_builds_from=args.reuse_builds_from)
    print(json.dumps({"status": report["status"], "report": str(path)}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
