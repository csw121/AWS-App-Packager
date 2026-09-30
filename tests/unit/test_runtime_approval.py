"""Synthetic provenance only: these tests never build or execute an image."""

import json
from unittest.mock import Mock

import pytest

from aws_app_packager import config, runtime_check
from aws_app_packager.models import ImageArtifact, RuntimeConditions, fingerprint
from aws_app_packager.project_input import assess_project, prepare_build_plan
from aws_app_packager.runtime_approval import record_runtime_approval, verify_runtime_approval

BUILD_JOB = "a" * 32
RETEST_JOB = "b" * 32


@pytest.fixture
def approved_source(tmp_path, monkeypatch):
    monkeypatch.delenv("RUN_LOCAL_CONTAINER_TESTS", raising=False)
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(runtime_check, "WORK_DIR", config.WORK_DIR)
    plan = prepare_build_plan(assess_project(config.ROOT / "samples" / "docker-http"), BUILD_JOB,
                              build_approved=True)
    artifact = ImageArtifact(
        job_id=plan.job_id, project_label=plan.project_label, build_method=plan.method,
        source_fingerprint=plan.source_fingerprint, local_tag=f"aws-app-packager/docker-http:{BUILD_JOB}",
        image_id="sha256:" + "f" * 64, size_bytes=123, user="10001", manifest_path=plan.manifest_path,
    )
    job = plan.manifest_path.parent
    (job / "image.json").write_text(artifact.model_dump_json(), encoding="utf-8")
    evidence = {
        "job_id": artifact.job_id, "image_id": artifact.image_id, "image_inspect_status": "PASS",
        "method": artifact.build_method, "source_fingerprint": artifact.source_fingerprint,
        "local_tag": artifact.local_tag, "returncode": 0, "cancelled": False, "timed_out": False,
    }
    (job / "build-result.json").write_text(json.dumps(evidence), encoding="utf-8")
    conditions = RuntimeConditions(container_port=8080, health_path="/health",
                                   network_mode="approved_project_bridge")
    return plan, artifact, conditions


def test_initial_approval_binds_built_image_source_and_conditions(approved_source):
    plan, artifact, conditions = approved_source
    path = record_runtime_approval(plan, conditions, execution_job_id=BUILD_JOB, runtime_approved=True)
    verify_runtime_approval(artifact, conditions, execution_job_id=BUILD_JOB)
    evidence = json.loads(path.read_text())
    assert evidence["runtime_approved"] is True and evidence["image_id"] is None
    assert evidence["conditions_fingerprint"] == fingerprint(conditions)
    assert evidence["build_approval_fingerprint"] == plan.approval_fingerprint


def test_retest_has_fresh_execution_job_approval_and_exact_image(approved_source):
    _, artifact, conditions = approved_source
    path = record_runtime_approval(artifact, conditions, execution_job_id=RETEST_JOB, runtime_approved=True)
    verify_runtime_approval(artifact, conditions, execution_job_id=RETEST_JOB)
    evidence = json.loads(path.read_text())
    assert evidence["build_job_id"] == BUILD_JOB and evidence["execution_job_id"] == RETEST_JOB
    assert evidence["image_id"] == artifact.image_id
    with pytest.raises(FileExistsError):
        record_runtime_approval(artifact, conditions, execution_job_id=RETEST_JOB, runtime_approved=True)


@pytest.mark.parametrize("change", ["port", "restart", "mode", "denied", "invalid_job"])
def test_normal_bridge_approval_rejects_unsafe_or_unapproved_conditions(approved_source, change):
    plan, _, conditions = approved_source
    job, approved = BUILD_JOB, True
    if change == "port":
        conditions.container_port = 8081
    elif change == "restart":
        conditions.restart = False
    elif change == "mode":
        conditions.network_mode = "internal"
    elif change == "denied":
        approved = False
    else:
        job = "../not-owned"
    with pytest.raises(ValueError):
        record_runtime_approval(plan, conditions, execution_job_id=job, runtime_approved=approved)
    assert not (plan.manifest_path.parent / "runtime-approval.json").exists()


@pytest.mark.parametrize("change", [
    "conditions", "source", "image", "manifest", "snapshot", "build_failure", "approval", "job",
])
def test_tampered_evidence_is_blocked_before_any_docker_command(approved_source, change):
    plan, artifact, conditions = approved_source
    path = record_runtime_approval(plan, conditions, execution_job_id=BUILD_JOB, runtime_approved=True)
    job_id = BUILD_JOB
    if change == "conditions":
        conditions.preset = "small"
    elif change == "source":
        artifact.source_fingerprint = "c" * 64
    elif change == "image":
        artifact.image_id = "sha256:" + "d" * 64
    elif change == "manifest":
        manifest = json.loads(plan.manifest_path.read_text())
        manifest["approval_fingerprint"] = "e" * 64
        plan.manifest_path.write_text(json.dumps(manifest))
    elif change == "snapshot":
        (plan.snapshot / "server.py").write_text("changed input")
    elif change == "build_failure":
        build_path = plan.manifest_path.parent / "build-result.json"
        evidence = json.loads(build_path.read_text())
        evidence["returncode"] = 1
        build_path.write_text(json.dumps(evidence))
    elif change == "approval":
        approval = json.loads(path.read_text())
        approval["runtime_approved"] = False
        path.write_text(json.dumps(approval))
    else:
        job_id = RETEST_JOB
    runner = Mock()
    check = runtime_check.run_check(artifact, conditions, runtime_approved=True,
                                     runner=runner, execution_job_id=job_id)
    assert check.status == check.outcome == "BLOCKED"
    assert check.failure_layer == "runtime_authorization"
    assert check.initial_http_passed is None
    runner.run.assert_not_called()


def test_missing_runtime_approval_cannot_be_replaced_by_boolean(approved_source):
    _, artifact, conditions = approved_source
    runner = Mock()
    check = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=runner)
    assert check.status == "BLOCKED" and check.failure_layer == "runtime_authorization"
    runner.run.assert_not_called()


def test_noncanonical_project_uses_explicit_approval_instead_of_sample_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    source = tmp_path / "approved-user-app"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM scratch\nUSER 10001\nEXPOSE 8080\n", encoding="utf-8")
    plan = prepare_build_plan(assess_project(source), BUILD_JOB, build_approved=True)
    conditions = RuntimeConditions(container_port=8080, health_path="/health",
                                   network_mode="approved_project_bridge")
    path = record_runtime_approval(plan, conditions, execution_job_id=BUILD_JOB, runtime_approved=True)
    assert path.is_file() and plan.project_label == "approved-user-app"


def test_initial_approval_cannot_be_reused_for_a_different_retest_job(approved_source):
    plan, artifact, conditions = approved_source
    path = record_runtime_approval(plan, conditions, execution_job_id=BUILD_JOB, runtime_approved=True)
    copied = json.loads(path.read_text())
    copied["execution_job_id"] = RETEST_JOB
    copied.pop("approval_fingerprint")
    copied["approval_fingerprint"] = fingerprint(copied)
    destination = config.WORK_DIR / "jobs" / RETEST_JOB
    destination.mkdir()
    (destination / "runtime-approval.json").write_text(json.dumps(copied), encoding="utf-8")
    with pytest.raises(ValueError, match="실제 이미지 ID"):
        verify_runtime_approval(artifact, conditions, execution_job_id=RETEST_JOB)
