"""Explicit UI stubs for state regressions; no Docker, pack, Terraform or AWS execution."""

from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from aws_app_packager import service
from aws_app_packager.config import ROOT
from aws_app_packager.models import (
    ExportBundle,
    ImageArtifact,
    RuntimeCheck,
    RuntimeConditions,
    fingerprint,
    utc_now,
)
from aws_app_packager.project_input import assess_project


def button(app, label):
    return next(item for item in app.button if item.label == label)


def has_button(app, label):
    return any(item.label == label for item in app.button)


@pytest.fixture
def manager_stub(monkeypatch):
    state = {"status": "IDLE", "stage": "명시적 UI stub", "events": []}
    result = {"value": None}
    context = {}
    monkeypatch.setattr(service.MANAGER, "snapshot", lambda: dict(state))
    monkeypatch.setattr(service.MANAGER, "result", lambda: result["value"])
    monkeypatch.setattr(service.MANAGER, "context", lambda: dict(context))
    cancel = Mock(side_effect=lambda: state.update(status="CANCELLED"))
    monkeypatch.setattr(service.MANAGER, "cancel", cancel)
    return state, result, context, cancel


@pytest.fixture
def app(manager_stub):
    return AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15).run()


def seed_pass(app, step=3):
    assessment = assess_project(ROOT / "samples/docker-http")
    conditions = RuntimeConditions(
        container_port=8080, health_path="/health", expected_marker="packager-python-v1",
        network_mode="approved_project_bridge",
    )
    artifact = ImageArtifact(
        job_id="a" * 32, project_label="docker-http", build_method="dockerfile",
        source_fingerprint=assessment.source_fingerprint,
        local_tag="aws-app-packager/docker-http:" + "a" * 32,
        image_id="sha256:" + "b" * 64, size_bytes=123, user="10001",
        manifest_path=ROOT / ".work/explicit-ui-stub.json",
    )
    check = RuntimeCheck(
        job_id=artifact.job_id, image_id=artifact.image_id,
        source_fingerprint=artifact.source_fingerprint, conditions=conditions,
        conditions_fingerprint=fingerprint(conditions), status="PASS", http_status=200,
        restart_passed=True, cleanup_status="CLEANED", started_at=artifact.created_at,
        finished_at=utc_now(), message="명시적 UI stub 결과 — 실제 컨테이너 검증 아님",
    )
    for key, value in {
        "assessment": assessment, "artifact": artifact, "check": check,
        "app_port": 8080, "health_path": "/health", "preset": "medium",
        "environment_json": "{}", "active_job_id": "1" * 32, "step": step,
    }.items():
        app.session_state[key] = value
    return assessment, artifact, check, conditions


def stub_bundle(tmp_path, name):
    directory = tmp_path / name
    directory.mkdir()
    archive = tmp_path / f"{name}.zip"
    archive.write_bytes(b"explicit UI download stub, not an integration artifact")
    (directory / "AWS_SPEC.md").write_text("AWS_NOT_TESTED\n", encoding="utf-8")
    return ExportBundle(directory=directory, zip_path=archive, files={}, external_inputs=["UI stub"])


@pytest.mark.parametrize("outcome", ["FAILED", "CANCELLED"])
def test_failed_or_cancelled_build_retry_removes_previous_pass(app, manager_stub, monkeypatch, outcome):
    state, result, _, _ = manager_stub
    seed_pass(app, step=1)
    app.run()
    app.checkbox(key="build_approval").check().run()
    app.checkbox(key="runtime_approval").check().run()

    def failed_retry(*args, **kwargs):
        state.update(job_id="2" * 32, kind="build_and_test", status=outcome)
        result["value"] = None
        return state["job_id"]

    monkeypatch.setattr(service, "start_build", failed_retry)
    button(app, "만들고 시험하기").click().run()
    assert not app.exception
    assert "check" not in app.session_state and "artifact" not in app.session_state
    assert not app.success
    assert not has_button(app, "AWS 실행 크기 확인")


def test_cancelled_retest_does_not_display_previous_medium_pass(app, manager_stub, monkeypatch):
    state, result, _, _ = manager_stub
    _, artifact, _, _ = seed_pass(app)
    app.run()
    app.radio(key="preset").set_value("small").run()
    app.checkbox(key="retest_approval").check().run()

    def cancelled_retest(*args, **kwargs):
        assert args[2].preset == "small"
        state.update(job_id="2" * 32, kind="retest", status="CANCELLED")
        result["value"] = None
        return state["job_id"]

    monkeypatch.setattr(service, "start_retest", cancelled_retest)
    button(app, "같은 이미지로 재시험").click().run()
    assert not app.exception
    assert "check" not in app.session_state
    assert app.session_state.artifact.image_id == artifact.image_id
    assert not app.success and not has_button(app, "AWS 실행 크기 확인")


def test_result_of_another_job_is_not_adopted(app, manager_stub):
    state, result, _, _ = manager_stub
    _, artifact, check, _ = seed_pass(app, step=2)
    another_image = artifact.model_copy(update={"image_id": "sha256:" + "c" * 64})
    state.update(job_id="2" * 32, status="DONE", kind="build_and_test")
    result["value"] = {"artifact": another_image, "check": check}
    app.run()
    assert not app.exception
    assert app.session_state.artifact.image_id == artifact.image_id
    assert not app.success and not has_button(app, "AWS 실행 크기 확인")


def test_regenerating_bundle_clears_previous_tar_completion(app, manager_stub, monkeypatch, tmp_path):
    state, result, _, _ = manager_stub
    seed_pass(app, step=4)
    previous = stub_bundle(tmp_path, "previous")
    replacement = stub_bundle(tmp_path, "replacement")
    old_tar = previous.directory / "image/app-image.tar"
    old_tar.parent.mkdir()
    old_tar.write_bytes(b"explicit UI tar stub, not docker image save")
    state.update(job_id="3" * 32, status="DONE", kind="save_image")
    result["value"] = {"tar": {"filename": "app-image.tar", "size_bytes": old_tar.stat().st_size}}
    app.session_state.bundle = previous
    app.session_state.tar_job = {"job_id": state["job_id"], "destination": str(old_tar)}
    app.run()
    assert any("저장 완료" in item.value for item in app.caption)
    monkeypatch.setattr(service, "export_current", Mock(return_value=replacement))
    button(app, "Terraform · 사양표 · 안내서 생성").click().run()
    assert not app.exception
    assert app.session_state.bundle.directory == replacement.directory
    assert "tar_job" not in app.session_state
    assert not (replacement.directory / "image/app-image.tar").exists()
    assert not any("저장 완료" in item.value for item in app.caption)
    assert not any("이미지 저장: DONE" in item.value for item in app.text)


def test_other_job_done_cannot_claim_tar_saved(app, manager_stub, tmp_path):
    state, _, _, _ = manager_stub
    seed_pass(app, step=4)
    bundle = stub_bundle(tmp_path, "new-bundle")
    app.session_state.bundle = bundle
    app.session_state.tar_job = {
        "job_id": "3" * 32, "destination": str(bundle.directory / "image/app-image.tar"),
    }
    state.update(job_id="4" * 32, status="DONE", kind="build_and_test")
    app.run()
    assert not app.exception
    assert not any("이미지 저장: DONE" in item.value for item in app.text)
    assert not any("저장 완료" in item.value for item in app.caption)


@pytest.mark.parametrize("field", ["region", "cidrs", "mode", "existing_role"])
def test_export_input_change_invalidates_previous_bundle_and_tar(app, tmp_path, field):
    seed_pass(app, step=4)
    bundle = stub_bundle(tmp_path, "bundle")
    app.session_state.bundle = bundle
    app.session_state.tar_job = {
        "job_id": "3" * 32, "destination": str(bundle.directory / "image/app-image.tar"),
    }
    app.run()
    assert "bundle" in app.session_state
    if field == "region":
        next(item for item in app.text_input if item.label == "AWS 리전").set_value("us-east-1").run()
    elif field == "cidrs":
        next(item for item in app.text_input if "IPv4 CIDR" in item.label).set_value("203.0.113.10/32").run()
    elif field == "mode":
        next(item for item in app.selectbox if item.label == "공개 방식").set_value("http_demo").run()
    else:
        next(item for item in app.checkbox if "기존 execution role" in item.label).check().run()
    assert not app.exception
    assert "bundle" not in app.session_state and "tar_job" not in app.session_state
    assert not any("AWS 설계 파일 준비 완료" in item.value for item in app.success)


def test_fresh_browser_session_recovers_running_context_and_can_cancel(manager_stub):
    state, _, context, cancel = manager_stub
    state.update(job_id="5" * 32, status="RUNNING", kind="build_and_test")
    assessment = assess_project(ROOT / "samples/docker-http")
    conditions = RuntimeConditions(container_port=8080, health_path="/health")
    context.update(assessment=assessment, conditions=conditions)
    fresh = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15).run()
    assert not fresh.exception
    assert fresh.session_state.step == 2
    assert fresh.session_state.active_job_id == state["job_id"]
    assert fresh.session_state.assessment.root == assessment.root
    button(fresh, "현재 작업 취소").click().run()
    cancel.assert_called_once()
    assert not fresh.exception
    assert not fresh.success
