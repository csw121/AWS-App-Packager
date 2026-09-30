"""Mocked service-boundary regressions; no Docker, build tools, or HTTP execution."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aws_app_packager import service
from aws_app_packager.jobs import JobManager
from aws_app_packager.models import ImageArtifact, ProjectAssessment, RuntimeConditions, fingerprint


@pytest.fixture
def inputs(tmp_path):
    assessment = ProjectAssessment(
        root=tmp_path / "authored-app", label="authored-app", method="dockerfile",
        source_fingerprint="a" * 64, evidence=["reviewed declaration"],
    )
    conditions = RuntimeConditions(
        container_port=8080, health_path="/health", environment={"TZ": "UTC"},
        network_mode="approved_project_bridge",
    )
    artifact = ImageArtifact(
        job_id="a" * 32, project_label=assessment.label, build_method="dockerfile",
        source_fingerprint=assessment.source_fingerprint,
        local_tag="aws-app-packager/authored-app:" + "a" * 32,
        image_id="sha256:" + "b" * 64, size_bytes=123, user="10001",
        manifest_path=tmp_path / "mock-build-context-manifest.json",
    )
    return assessment, conditions, artifact


@pytest.fixture
def queued_manager(monkeypatch):
    manager = Mock()
    manager.start.return_value = "c" * 32
    monkeypatch.setattr(service, "MANAGER", manager)
    return manager


@pytest.mark.parametrize("build,runtime", [(False, False), (False, True), (True, False)])
def test_missing_consent_never_starts_work(inputs, queued_manager, build, runtime):
    assessment, conditions, _ = inputs
    with pytest.raises(ValueError, match="승인"):
        service.start_build(assessment, conditions, build_approved=build, runtime_approved=runtime)
    queued_manager.start.assert_not_called()


def test_build_freezes_approval_inputs_and_records_consent_before_build(
    inputs, queued_manager, monkeypatch,
):
    assessment, conditions, artifact = inputs
    approved_conditions = fingerprint(conditions)
    ordered = Mock()
    plan = SimpleNamespace(job_id="c" * 32)
    ordered.prepare.return_value = plan
    ordered.build.return_value = artifact
    ordered.run.return_value = SimpleNamespace(status="PASS")
    monkeypatch.setattr(service, "prepare_build_plan", ordered.prepare)
    monkeypatch.setattr(service, "record_runtime_approval", ordered.approve)
    monkeypatch.setattr(service, "build_image", ordered.build)
    monkeypatch.setattr(service, "run_check", ordered.run)
    queued_manager.record_artifact.side_effect = ordered.artifact

    job_id = service.start_build(assessment, conditions, build_approved=True, runtime_approved=True)
    # A widget edit while the worker is queued cannot broaden its approval.
    assessment.source_fingerprint = "f" * 64
    assessment.evidence.append("later edit")
    conditions.environment["TZ"] = "Asia/Seoul"
    conditions.health_path = "/later"
    action = queued_manager.start.call_args.args[0]
    result = action(job_id, threading.Event(), Mock())

    captured_assessment = ordered.prepare.call_args.args[0]
    captured_conditions = ordered.approve.call_args.args[1]
    assert captured_assessment.source_fingerprint == artifact.source_fingerprint
    assert captured_assessment.evidence == ["reviewed declaration"]
    assert fingerprint(captured_conditions) == approved_conditions
    assert ordered.approve.call_args.kwargs == {
        "execution_job_id": job_id, "runtime_approved": True,
    }
    assert ordered.run.call_args.kwargs["execution_job_id"] == job_id
    assert ordered.run.call_args.kwargs["runtime_approved"] is True
    assert ordered.run.call_args.args[1] is captured_conditions
    assert [call[0] for call in ordered.mock_calls] == ["prepare", "approve", "build", "artifact", "run"]
    assert result["artifact"] is artifact


def test_failed_approval_persistence_prevents_image_build(inputs, queued_manager, monkeypatch):
    assessment, conditions, _ = inputs
    monkeypatch.setattr(service, "prepare_build_plan", Mock(return_value=object()))
    monkeypatch.setattr(
        service, "record_runtime_approval", Mock(side_effect=OSError("approval disk failure")),
    )
    build, run = Mock(), Mock()
    monkeypatch.setattr(service, "build_image", build)
    monkeypatch.setattr(service, "run_check", run)
    job_id = service.start_build(assessment, conditions, build_approved=True, runtime_approved=True)
    with pytest.raises(OSError, match="approval disk failure"):
        queued_manager.start.call_args.args[0](job_id, threading.Event(), Mock())
    build.assert_not_called()
    run.assert_not_called()
    queued_manager.record_artifact.assert_not_called()


def test_confirmed_image_is_published_before_runtime_exception(inputs, queued_manager, monkeypatch):
    assessment, conditions, artifact = inputs
    monkeypatch.setattr(service, "prepare_build_plan", Mock(return_value=object()))
    monkeypatch.setattr(service, "record_runtime_approval", Mock())
    monkeypatch.setattr(service, "build_image", Mock(return_value=artifact))
    monkeypatch.setattr(service, "run_check", Mock(side_effect=RuntimeError("runtime-only failure")))
    job_id = service.start_build(assessment, conditions, build_approved=True, runtime_approved=True)
    with pytest.raises(RuntimeError, match="runtime-only failure"):
        queued_manager.start.call_args.args[0](job_id, threading.Event(), Mock())
    queued_manager.record_artifact.assert_called_once_with(artifact)


def test_job_retains_confirmed_image_when_later_worker_step_raises(inputs, tmp_path):
    _, _, artifact = inputs
    manager = JobManager(tmp_path / "mock-worker")

    def action(_job_id, _cancel, stage):
        stage("이미지 생성")
        manager.record_artifact(artifact)
        # Even if a later runtime callback fails before updating the stage,
        # the confirmed image must remain visible and must not become build failure.
        raise RuntimeError("after-build callback failure")

    job_id = manager.start(action, kind="build_and_test")
    manager.wait(timeout=5)
    state = manager.snapshot()
    assert state["status"] == "FAILED"
    assert state["outcome"] != "IMAGE_BUILD_FAILED"
    assert state["result"]["artifact"]["image_id"] == artifact.image_id
    assert manager.result()["artifact"] == artifact
    persisted = json.loads((manager.work_dir / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
    assert persisted["result"]["artifact"] == state["result"]["artifact"]


def test_retest_without_approval_does_not_read_or_queue(inputs, queued_manager, monkeypatch):
    assessment, conditions, artifact = inputs
    read = Mock(side_effect=AssertionError("unapproved input read"))
    monkeypatch.setattr(service, "current_fingerprint", read)
    with pytest.raises(ValueError, match="승인"):
        service.start_retest(assessment, artifact, conditions, runtime_approved=False)
    read.assert_not_called()
    queued_manager.start.assert_not_called()


def test_retest_source_change_while_queued_blocks_execution(inputs, queued_manager, monkeypatch):
    assessment, conditions, artifact = inputs
    monkeypatch.setattr(
        service, "current_fingerprint", Mock(side_effect=[artifact.source_fingerprint, "f" * 64]),
    )
    approve, run = Mock(), Mock()
    monkeypatch.setattr(service, "record_runtime_approval", approve)
    monkeypatch.setattr(service, "run_check", run)
    job_id = service.start_retest(assessment, artifact, conditions, runtime_approved=True)
    with pytest.raises(ValueError, match="소스가 변경"):
        queued_manager.start.call_args.args[0](job_id, threading.Event(), Mock())
    approve.assert_not_called()
    run.assert_not_called()


def test_retest_freezes_image_and_conditions_and_uses_new_execution_job(
    inputs, queued_manager, monkeypatch,
):
    assessment, conditions, artifact = inputs
    original_image = artifact.image_id
    approved_conditions = fingerprint(conditions)
    monkeypatch.setattr(service, "current_fingerprint", Mock(return_value=artifact.source_fingerprint))
    approve, run = Mock(), Mock()
    monkeypatch.setattr(service, "record_runtime_approval", approve)
    monkeypatch.setattr(service, "run_check", run)
    job_id = service.start_retest(assessment, artifact, conditions, runtime_approved=True)
    artifact.image_id = "sha256:" + "e" * 64
    artifact.manifest_path = Path("later-mutation")
    conditions.environment["TZ"] = "Asia/Seoul"
    queued_manager.start.call_args.args[0](job_id, threading.Event(), Mock())

    approved_artifact, approved_runtime = approve.call_args.args
    assert approved_artifact.image_id == original_image
    assert approved_artifact.manifest_path != artifact.manifest_path
    assert fingerprint(approved_runtime) == approved_conditions
    assert approved_artifact.job_id != job_id
    assert approve.call_args.kwargs["execution_job_id"] == job_id
    assert run.call_args.kwargs["execution_job_id"] == job_id
    assert run.call_args.args == (approved_artifact, approved_runtime)
