from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from aws_app_packager import service
from aws_app_packager.config import ROOT
from aws_app_packager.models import ImageArtifact, RuntimeCheck, RuntimeConditions, fingerprint, utc_now
from aws_app_packager.project_input import assess_project


def button(app, label):
    return next(item for item in app.button if item.label == label)


@pytest.fixture
def app(monkeypatch):
    # UI tests must not run real subprocesses; production service has no fake mode.
    state = {"status": "IDLE", "stage": "준비", "events": []}
    monkeypatch.setattr(service.MANAGER, "snapshot", lambda: state)
    monkeypatch.setattr(service.MANAGER, "result", lambda: None)
    return AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15).run()


def choose_sample(app):
    button(app, "Dockerfile 검증 샘플 선택").click().run()
    button(app, "위치와 빌드 범위 확인").click().run()
    button(app, "실행 준비로").click().run()
    assert not app.exception
    return app


def test_initial_and_approval_gates(app):
    assert not app.exception
    choose_sample(app)
    assert button(app, "만들고 시험하기").disabled
    app.checkbox(key="build_approval").check().run()
    assert button(app, "만들고 시험하기").disabled
    app.checkbox(key="runtime_approval").check().run()
    assert not button(app, "만들고 시험하기").disabled
    app.number_input(key="app_port").set_value(8081).run()
    assert not app.checkbox(key="runtime_approval").value
    assert button(app, "만들고 시험하기").disabled
    app.checkbox(key="runtime_approval").check().run()
    assert button(app, "만들고 시험하기").disabled
    assert any("8080만 지원" in item.value for item in app.error)
    button(app, "이전 · 앱 선택").click().run()
    assert app.session_state.step == 0


def test_read_only_assessment_and_each_missing_approval_never_start_build(app, monkeypatch):
    start = Mock()
    monkeypatch.setattr(service, "start_build", start)
    choose_sample(app)
    start.assert_not_called()
    assert any("외부 통신은 허용" in item.value for item in app.markdown)
    assert any("127.0.0.1" in item.label for item in app.checkbox)
    app.checkbox(key="runtime_approval").check().run()
    assert button(app, "만들고 시험하기").disabled
    start.assert_not_called()
    app.checkbox(key="runtime_approval").uncheck().run()
    app.checkbox(key="build_approval").check().run()
    assert button(app, "만들고 시험하기").disabled
    start.assert_not_called()


def test_input_change_clears_old_assessment(app):
    choose_sample(app)
    button(app, "이전 · 앱 선택").click().run()
    app.text_input(key="source_path").set_value(str(ROOT / "samples")).run()
    assert "assessment" not in app.session_state


def test_failure_and_retry_never_show_pass(app, monkeypatch):
    choose_sample(app)
    monkeypatch.setattr(
        service.MANAGER,
        "snapshot",
        lambda: {
            "status": "FAILED",
            "stage": "이미지 생성",
            "events": ["파일 준비", "이미지 생성"],
            "error": "테스트용 명시적 빌드 실패",
            "next_action": "환경 확인",
        },
    )
    app.session_state.step = 2
    app.run()
    assert not app.success
    assert not app.exception
    button(app, "입력 수정 · 재시도").click().run()
    assert app.session_state.step == 1


def seed_result(app):
    conditions = RuntimeConditions(
        container_port=8080, health_path="/health", expected_marker="packager-python-v1",
        network_mode="approved_project_bridge",
    )
    artifact = ImageArtifact(
        job_id="a" * 32,
        project_label="docker-http",
        build_method="dockerfile",
        source_fingerprint="unit-test-only",
        local_tag="aws-app-packager/docker-http:" + "a" * 32,
        image_id="sha256:" + "b" * 64,
        size_bytes=123,
        user="10001",
        manifest_path=ROOT / ".work" / "fake.json",
    )
    check = RuntimeCheck(
        job_id=artifact.job_id,
        image_id=artifact.image_id,
        source_fingerprint=artifact.source_fingerprint,
        conditions=conditions,
        conditions_fingerprint=fingerprint(conditions),
        status="PASS",
        http_status=200,
        cleanup_status="CLEANED",
        restart_passed=True,
        started_at=artifact.created_at,
        finished_at=utc_now(),
    )
    app.session_state.assessment = assess_project(ROOT / "samples" / "docker-http")
    app.session_state.artifact = artifact
    app.session_state.check = check
    app.session_state.app_port = 8080
    app.session_state.health_path = "/health"
    app.session_state.preset = "medium"
    app.session_state.environment_json = "{}"
    app.session_state.step = 3


def test_size_change_requires_retest(app, monkeypatch):
    seed_result(app)
    app.run()
    assert not button(app, "결과물 준비로").disabled
    app.radio(key="preset").set_value("small").run()
    assert button(app, "결과물 준비로").disabled
    assert button(app, "같은 이미지로 재시험").disabled
    app.checkbox(key="retest_approval").check().run()
    call = Mock()
    monkeypatch.setattr(service, "start_retest", call)
    button(app, "같은 이미지로 재시험").click().run()
    assert call.call_args.args[2].preset == "small"
    assert call.call_args.kwargs["runtime_approved"] is True
    assert app.session_state.step == 2


def test_export_is_real_service_gated_and_shows_aws_not_tested(app, monkeypatch):
    seed_result(app)
    app.run()
    button(app, "결과물 준비로").click().run()
    export = Mock(side_effect=ValueError("소스 변경으로 시험 결과가 오래되었습니다."))
    monkeypatch.setattr(service, "export_current", export)
    button(app, "Terraform · 사양표 · 안내서 생성").click().run()
    assert export.called
    assert any("소스 변경" in item.value for item in app.error)
    assert not app.success
    assert not app.exception


def test_full_wizard_with_explicit_ui_stub_and_real_offline_export(app, monkeypatch, tmp_path):
    from aws_app_packager import config
    from aws_app_packager.terraform_export import export_bundle

    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "exports")
    choose_sample(app)
    outputs = {}

    def approved_stub(assessment, conditions, **approval):
        assert approval == {"build_approved": True, "runtime_approved": True}
        assert conditions.network_mode == "approved_project_bridge"
        artifact = ImageArtifact(
            job_id="d" * 32,
            project_label="docker-http",
            build_method="dockerfile",
            source_fingerprint=assessment.source_fingerprint,
            local_tag="aws-app-packager/docker-http:" + "d" * 32,
            image_id="sha256:" + "e" * 64,
            size_bytes=123,
            user="10001",
            manifest_path=tmp_path / "manifest",
        )
        check = RuntimeCheck(
            job_id=artifact.job_id,
            image_id=artifact.image_id,
            source_fingerprint=artifact.source_fingerprint,
            conditions=conditions,
            conditions_fingerprint=fingerprint(conditions),
            status="PASS",
            http_status=200,
            restart_passed=True,
            cleanup_status="CLEANED",
            started_at=artifact.created_at,
            finished_at=utc_now(),
        )
        outputs.update(artifact=artifact, check=check)

    monkeypatch.setattr(service, "start_build", approved_stub)
    app.checkbox(key="build_approval").check().run()
    app.checkbox(key="runtime_approval").check().run()
    monkeypatch.setattr(service.MANAGER, "snapshot", lambda: {"status": "DONE", "stage": "웹 응답 확인"})
    monkeypatch.setattr(service.MANAGER, "result", lambda: outputs)
    button(app, "만들고 시험하기").click().run()
    assert not app.exception
    button(app, "AWS 실행 크기 확인").click().run()
    assert not button(app, "결과물 준비로").disabled
    assert app.session_state.app_port == 8080
    assert app.session_state.health_path == "/health"
    button(app, "결과물 준비로").click().run()
    monkeypatch.setattr(service, "assert_same_image", lambda _: None)
    button(app, "Terraform · 사양표 · 안내서 생성").click().run()
    assert not app.exception
    assert not app.error, [item.value for item in app.error]
    assert app.session_state.bundle.aws_status == "AWS_NOT_TESTED"
    assert app.session_state.bundle.zip_path.is_file()
    assert any("AWS 설계 파일 준비 완료" in item.value for item in app.success)
    # This output is under a pytest temp directory, never a real integration artifact.
    assert callable(export_bundle)
