"""Explicit UI state fixtures; these tests do not build or run containers."""

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from aws_app_packager import service, ui
from aws_app_packager.config import ROOT
from aws_app_packager.models import RuntimeConditions
from aws_app_packager.progress import describe_progress
from aws_app_packager.project_input import assess_project

START = "2026-09-30T00:00:00+00:00"
NOW = datetime(2026, 9, 30, 0, 3, tzinfo=UTC)


@pytest.fixture
def progress_app(monkeypatch):
    state = {
        "job_id": "9" * 32, "kind": "build_and_test", "status": "RUNNING",
        "stage": "이미지 생성", "started_at": START,
        "stage_started_at": "2026-09-30T00:01:00+00:00",
        "stage_history": [{"stage": "파일 준비", "started_at": START},
                          {"stage": "이미지 생성", "started_at": "2026-09-30T00:01:00+00:00"}],
        "process": {
            "started_at": "2026-09-30T00:01:00+00:00", "elapsed_seconds": 120,
            "timeout_seconds": 1800, "running": True,
            "last_output_at": "2026-09-30T00:01:30+00:00",
            "output_tail": "===> BUILDING\n[builder] Downloading JDK\n",
        },
        "live_log": "===> BUILDING\n[builder] Downloading JDK\ntoken=ui-test-secret\n",
    }
    context = {
        "assessment": assess_project(ROOT / "samples/docker-http"),
        "conditions": RuntimeConditions(container_port=8080, health_path="/health"),
    }
    cancel = Mock(side_effect=lambda: state.update(cancel_requested_at=NOW.isoformat()))
    monkeypatch.setattr(service.MANAGER, "snapshot", lambda: deepcopy(state))
    monkeypatch.setattr(service.MANAGER, "result", lambda: None)
    monkeypatch.setattr(service.MANAGER, "context", lambda: context)
    monkeypatch.setattr(service.MANAGER, "cancel", cancel)
    monkeypatch.setattr(ui, "describe_progress", lambda value: describe_progress(value, now=NOW))
    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15).run()
    assert not app.exception
    return app, state, cancel


def test_running_progress_is_observed_and_live_log_is_visible(progress_app):
    app, _, _ = progress_app
    values = {metric.label: metric.value for metric in app.metric}
    assert values == {
        "전체 경과 시간": "3분 00초", "현재 단계 경과": "2분 00초",
        "현재 명령의 최근 출력": "1분 30초 전",
    }
    assert any("Java 의존성 준비와 앱 빌드" in item.value for item in app.markdown)
    assert any("90" in item.value or "1분 30초 동안 새 출력" in item.value for item in app.warning)
    assert any("28분 00초" in item.value and "완료 예상 시간이 아닙니다" in item.value
               for item in app.caption)
    assert any("Downloading JDK" in item.value for item in app.code)
    assert all("ui-test-secret" not in item.value for item in app.code)
    assert app.get("progress")[0].proto.value == 20
    assert any("단계 진행률 20%" in item.value for item in app.subheader)
    assert any("소요 시간·다운로드 비율은 아닙니다" in item.value for item in app.caption)
    assert any("화면 단계 3/5" in item.value for item in app.caption)
    assert not app.success
    assert not any(item.label == "AWS 실행 크기 확인" for item in app.button)


def test_new_output_replaces_silence_warning_on_refresh(progress_app):
    app, state, _ = progress_app
    state["process"].update(last_output_at=NOW.isoformat(), output_tail="===> EXPORTING\n")
    state["live_log"] = "===> EXPORTING\nSaving image layers\n"
    app.run()
    assert not app.exception
    assert not any("새 출력이 없습니다" in item.value for item in app.warning)
    assert any("로컬 Docker 이미지로 내보내기" in item.value for item in app.markdown)
    assert any("Saving image layers" in item.value for item in app.code)
    assert not any("Downloading JDK" in item.value for item in app.code)


def test_failed_build_freezes_clock_and_exposes_error(progress_app):
    app, state, _ = progress_app
    state.update(status="FAILED", finished_at="2026-09-30T00:02:00+00:00",
                 error="JDK 다운로드 실패", next_action="다운로드 연결을 확인한 뒤 재시도하세요.")
    state["process"]["running"] = False
    app.run()
    assert not app.exception
    assert any(item.label == "전체 경과 시간" and item.value == "2분 00초" for item in app.metric)
    assert any("JDK 다운로드 실패" in item.value for item in app.error)
    assert any("연결을 확인" in item.value for item in app.caption)
    assert not any("새 출력이 없습니다" in item.value for item in app.warning)
    assert not any("제한 시간까지" in item.value for item in app.caption)
    assert not app.success


def test_later_inspection_failure_is_visible_above_old_build_tail(progress_app):
    app, state, _ = progress_app
    state.update(status="FAILED", finished_at=NOW.isoformat(),
                 error="생성한 이미지의 실제 식별정보를 확인할 수 없습니다.",
                 logs="old build output\n" * 2500 + "final image inspect failure\n")
    app.run()
    assert not app.exception
    assert any("final image inspect failure" in item.value for item in app.code)
    assert not any("Downloading JDK" in item.value for item in app.code)


def test_cancel_request_stays_running_until_worker_finishes(progress_app):
    app, state, cancel = progress_app
    next(item for item in app.button if item.label == "현재 작업 취소").click().run()
    cancel.assert_called_once()
    app.run()
    assert state["status"] == "RUNNING"
    assert next(item for item in app.button if item.label == "현재 작업 취소").disabled
    assert any("취소 요청 처리 중" in item.value for item in app.warning)
    assert not app.success
    assert app.get("progress")[0].proto.value == 20
    assert any("취소 처리 중" in item.value for item in app.subheader)


def test_quiet_initial_process_does_not_claim_a_known_phase(progress_app):
    app, state, _ = progress_app
    state["process"].update(last_output_at=None, output_tail="", started_at=NOW.isoformat(),
                            elapsed_seconds=0)
    state["live_log"] = ""
    app.run()
    assert not app.exception
    assert not any("도구가 마지막으로 보고한 단계" in item.value for item in app.markdown)
    assert any("아직 표시할 도구 로그가 없습니다" in item.value for item in app.caption)
    assert not any("새 출력이 없습니다" in item.value for item in app.warning)


def test_image_save_has_the_same_live_progress_and_cancel(progress_app):
    _, state, cancel = progress_app
    state.update(kind="save_image", stage="이미지를 디스크 tar로 저장 중")
    state["process"]["output_tail"] = ""
    state["live_log"] = ""
    app = AppTest.from_string(
        'from aws_app_packager.ui import _tar_progress\n'
        '_tar_progress({"job_id": "9" * 32, "destination": "not-a-real-tar"})',
        default_timeout=15,
    ).run()
    assert not app.exception
    assert len(app.metric) == 3
    assert app.get("progress")[0].proto.value == 0
    next(item for item in app.button if item.label == "이미지 저장 취소").click().run()
    cancel.assert_called_once()
    app.run()
    assert any("이미지 저장 취소 처리 중" in item.value for item in app.info)
    assert not any("저장 완료" in item.value for item in app.caption)


def test_blocked_mapping_shows_stopped_percentage_even_after_cleanup(progress_app):
    app, state, _ = progress_app
    state.update(status="FAILED", finished_at=NOW.isoformat(), stage="시험 자원 정리")
    state["stage_history"].extend([
        {"stage": "시험 이미지·실행 조건 확인"},
        {"stage": "실제 루프백 포트 매핑 확인"}, {"stage": "시험 자원 정리"},
    ])
    state["result"] = {"check": {
        "status": "ENVIRONMENT_BLOCKED", "outcome": "ENVIRONMENT_BLOCKED_PORT_MAPPING",
        "cleanup_status": "CLEANED", "image_verified": True, "image_id": "sha256:" + "a" * 64,
        "container_started": True, "initial_http_passed": False, "failure_layer": "port_mapping",
        "port_evidence": [{"phase": "start", "verified": False}],
    }}
    app.run()
    assert not app.exception
    assert app.get("progress")[0].proto.value == 60
    assert any("단계 진행률 60% · 중단됨" in item.value for item in app.subheader)
    table = next(item.value for item in app.markdown if "| 단계 | 상태 |" in item.value)
    assert "| 이미지 만들기 | ✓ 완료 |" in table
    assert "| 컨테이너 시작 | ✓ 완료 |" in table
    assert "| 로컬 포트 연결 | ✗ 환경 차단 |" in table
    assert "| 최초 HTTP 시험 | 미실행 |" in table
    assert "| 재시작 후 HTTP 시험 | 미실행 |" in table
    assert "| 시험 자원 정리 | ✓ 완료 |" in table
    assert any("ENVIRONMENT_BLOCKED_PORT_MAPPING" in item.value for item in app.caption)
    assert not any("실패 · 원인 확인 필요" in item.value for item in app.markdown)
    assert any(item.value == "sha256:" + "a" * 64 for item in app.code)
    assert not app.success


def test_only_completed_runtime_and_cleanup_display_100_percent(progress_app):
    app, state, _ = progress_app
    state.update(status="DONE", finished_at=NOW.isoformat(), stage="시험 자원 정리")
    state["result"] = {"check": {
        "status": "PASS", "outcome": "PASS", "http_status": 200, "cleanup_status": "CLEANED",
        "conditions": {"restart": True}, "restart_passed": True, "restart_performed": True,
        "initial_http_passed": True, "image_verified": True, "image_id": "sha256:" + "a" * 64,
        "container_started": True, "port_evidence": [{"phase": "start", "verified": True},
                                                       {"phase": "restart", "verified": True}],
    }}
    app.run()
    assert not app.exception
    assert app.get("progress")[0].proto.value == 100
    assert any("단계 진행률 100%" in item.value for item in app.subheader)
    table = next(item.value for item in app.markdown if "| 단계 | 상태 |" in item.value)
    assert table.count("✓ 완료") == 8


def test_built_image_id_remains_visible_while_runtime_is_running(progress_app):
    app, state, _ = progress_app
    state.update(stage="실행 시작", result={"artifact": {"image_id": "sha256:" + "b" * 64}})
    app.run()
    assert not app.exception
    assert any(item.value == "sha256:" + "b" * 64 for item in app.code)
    table = next(item.value for item in app.markdown if "| 단계 | 상태 |" in item.value)
    assert "| 이미지 만들기 | ✓ 완료 |" in table
    assert "| 컨테이너 시작 | ● 진행 중 |" in table
    assert "| 최초 HTTP 시험 | 미실행 |" in table


def test_restart_mapping_failure_preserves_initial_http_pass_and_no_second_http(progress_app):
    app, state, _ = progress_app
    state.update(status="FAILED", finished_at=NOW.isoformat(), stage="시험 자원 정리")
    state["result"] = {"check": {
        "status": "ENVIRONMENT_BLOCKED", "outcome": "ENVIRONMENT_BLOCKED_PORT_MAPPING",
        "cleanup_status": "CLEANED", "image_verified": True, "image_id": "sha256:" + "a" * 64,
        "container_started": True, "initial_http_passed": True, "restart_performed": True,
        "failure_layer": "port_mapping", "restart_passed": False,
        "port_evidence": [{"phase": "start", "verified": True},
                          {"phase": "restart", "verified": False}],
    }}
    app.run()
    assert not app.exception
    table = next(item.value for item in app.markdown if "| 단계 | 상태 |" in item.value)
    assert "| 최초 HTTP 시험 | ✓ 완료 |" in table
    assert "| 재시작 후 포트 연결 | ✗ 환경 차단 |" in table
    assert "| 재시작 후 HTTP 시험 | 미실행 |" in table


def test_wrong_http_body_never_displays_completed_http(progress_app):
    app, state, _ = progress_app
    state.update(status="FAILED", finished_at=NOW.isoformat(), stage="시험 자원 정리")
    state["result"] = {"check": {
        "status": "FAIL", "outcome": "INITIAL_HTTP_FAILED", "failure_layer": "initial_http",
        "http_status": 200, "initial_http_passed": False, "image_verified": True,
        "image_id": "sha256:" + "a" * 64, "container_started": True, "cleanup_status": "CLEANED",
        "port_evidence": [{"phase": "start", "verified": True}],
    }}
    app.run()
    assert not app.exception
    table = next(item.value for item in app.markdown if "| 단계 | 상태 |" in item.value)
    assert "| 최초 HTTP 시험 | ✗ 실패 |" in table
    assert "| 재시작 후 HTTP 시험 | 미실행 |" in table
    assert app.get("progress")[0].proto.value < 100
