"""Progress is derived only from observed logs/time; no process or cloud tools run."""

from datetime import UTC, datetime, timedelta

import pytest

from aws_app_packager.progress import (
    describe_completion,
    describe_progress,
    describe_workflow,
    format_duration,
    infer_performance_note,
    infer_phase,
)

NOW = datetime(2026, 9, 30, 1, 2, 3, tzinfo=UTC)
PERFORMANCE_WARNING = (
    "Warning: Exporting to docker daemon (building without --publish) and daemon uses "
    "containerd storage; performance may be significantly degraded."
)


def ago(seconds):
    return (NOW - timedelta(seconds=seconds)).isoformat()


def running_state(**process_updates):
    process = {
        "started_at": ago(80), "elapsed_seconds": 80, "timeout_seconds": 600,
        "running": True, "output_tail": "===> BUILDING", "last_output_at": ago(3),
        "output_lines": 1, "returncode": None,
    }
    process.update(process_updates)
    return {
        "status": "RUNNING", "stage": "이미지 생성", "started_at": ago(125),
        "stage_started_at": ago(90), "process": process,
    }


@pytest.mark.parametrize(("seconds", "expected"), [
    (0, "0초"), (5.9, "5초"), (60, "1분 00초"), (125, "2분 05초"),
    (3600, "1시간 00분 00초"), (3725, "1시간 02분 05초"),
    (None, "0초"), (-3, "0초"), (float("nan"), "0초"), (float("inf"), "0초"),
    ("invalid", "0초"), ({}, "0초"), (True, "0초"),
])
def test_duration_is_elapsed_text_not_percentage_or_eta(seconds, expected):
    assert format_duration(seconds) == expected


def test_running_timers_use_their_own_start_and_configured_limit():
    view = describe_progress(running_state(), now=NOW)
    assert view["elapsed_seconds"] == 125
    assert view["stage_elapsed_seconds"] == 90
    assert view["silence_seconds"] == 3 and not view["silence_warning"]
    assert view["timeout_remaining_seconds"] == 520
    assert "eta" not in view and "percent" not in view


@pytest.mark.parametrize("status", ["DONE", "FAILED", "CANCELLED"])
def test_terminal_timers_freeze_and_stale_process_flag_cannot_warn(status):
    state = running_state(last_output_at=ago(20))
    state.update(status=status, finished_at=ago(10))
    first = describe_progress(state, now=NOW)
    later = describe_progress(state, now=NOW + timedelta(hours=2))
    assert first == later
    assert first["elapsed_seconds"] == 115 and first["stage_elapsed_seconds"] == 80
    assert first["silence_seconds"] == 10 and not first["silence_warning"]
    assert first["timeout_remaining_seconds"] is None and not first["process_running"]


def test_silence_without_any_output_uses_process_start_and_does_not_claim_hang():
    view = describe_progress(running_state(last_output_at=None, output_tail="", output_lines=0), now=NOW)
    assert view["silence_seconds"] == 80 and view["silence_warning"]
    assert "판단할 수 없습니다" in view["silence_message"]
    assert view["phase_label"] is None and view["status"] == "RUNNING"


@pytest.mark.parametrize(("seconds", "warning"), [(60, False), (61, True)])
def test_silence_warning_threshold_is_strict_and_falls_back_to_reported_elapsed(seconds, warning):
    state = running_state(started_at=None, last_output_at=None, elapsed_seconds=seconds)
    view = describe_progress(state, now=NOW)
    assert view["silence_seconds"] == seconds and view["silence_warning"] is warning


def test_last_output_resets_silence_and_timeout_expiry_is_not_completion():
    view = describe_progress(running_state(last_output_at=ago(1), elapsed_seconds=700), now=NOW)
    assert view["silence_seconds"] == 1 and not view["silence_warning"]
    assert view["timeout_remaining_seconds"] == 0
    assert view["status"] == "RUNNING"


def test_missing_stage_timestamp_uses_only_matching_recorded_stage_history():
    state = running_state()
    state.pop("stage_started_at")
    state["stage_history"] = [
        {"stage": "파일 준비", "started_at": ago(120)},
        {"stage": "이미지 생성", "started_at": ago(88)},
        {"stage": "다른 단계", "started_at": ago(5)},
    ]
    assert describe_progress(state, NOW)["stage_elapsed_seconds"] == 88


@pytest.mark.parametrize("state", [None, [], {}, {"status": []}, {
    "status": "DONE", "started_at": "broken", "finished_at": {},
    "stage_started_at": "2026-99-99", "stage_history": [None, {}, "invalid"],
    "process": {"last_output_at": "broken", "elapsed_seconds": "NaN", "timeout_seconds": []},
}, {"status": "RUNNING", "process": "legacy"}])
def test_legacy_and_malformed_records_have_safe_defaults(state):
    view = describe_progress(state, now=NOW)
    assert view["elapsed_seconds"] == 0 and view["stage_elapsed_seconds"] == 0
    assert view["silence_seconds"] == 0 and not view["silence_warning"]
    assert view["timeout_remaining_seconds"] is None and view["phase_label"] is None


def test_utc_offsets_naive_legacy_and_future_clock_skew():
    state = running_state()
    state["started_at"] = (NOW - timedelta(seconds=10)).replace(tzinfo=None).isoformat()
    state["stage_started_at"] = (NOW + timedelta(seconds=30)).isoformat()
    assert describe_progress(state, NOW)["elapsed_seconds"] == 10
    assert describe_progress(state, NOW)["stage_elapsed_seconds"] == 0
    state["started_at"] = "2026-09-30T10:02:00+09:00"
    assert describe_progress(state, NOW)["elapsed_seconds"] == 3


@pytest.mark.parametrize(("line", "label"), [
    ("===> ANALYZING", "pack · 이전 이미지와 빌드 정보 확인"),
    ("===> DETECTING", "pack · Java buildpack 확인"),
    ("===> RESTORING", "pack · 빌드 캐시 복원"),
    ("===> BUILDING", "pack · Java 의존성 준비와 앱 빌드"),
    ("===> EXPORTING", "pack · 로컬 Docker 이미지로 내보내기"),
    ("[INFO] Downloading from central: https://repo.example.invalid/item", "Maven · Java 의존성 다운로드"),
    ("Downloading from central: https://repo.example.invalid/item", "Maven · Java 의존성 다운로드"),
    ("[builder] [INFO] BUILD SUCCESS", "앱 컴파일 완료 · 이미지 준비 중"),
    ("#1 [internal] load build definition from Dockerfile", "Docker · 빌드 입력과 기반 이미지 준비"),
    ("#2 [auth] library/python:pull token for registry", "Docker · 이미지 다운로드 인증 확인"),
    ("#4 [2/3] COPY server.py /app", "Dockerfile · 빌드 단계 실행"),
    ("#6 [builder 3/5] RUN arbitrary-command", "Dockerfile · 빌드 단계 실행"),
    ("#9 exporting to image", "Docker · 로컬 이미지로 내보내기"),
    ("#9 exporting layers 0.1s done", "Docker · 로컬 이미지로 내보내기"),
    ("Step 1/5 : FROM python:3.13", "Docker · 빌드 입력과 기반 이미지 준비"),
    ("Step 2/5 : COPY server.py /app/server.py", "Dockerfile · 빌드 단계 실행"),
    ("Step 3/5 : RUN arbitrary-command", "Dockerfile · 빌드 단계 실행"),
    ("Step 5/5 : CMD [\"python\", \"/app/server.py\"]", "Dockerfile · 빌드 단계 실행"),
    ("Successfully built 012345abcdef", "Docker · 이미지 생성 명령 완료 · 식별정보 확인 대기"),
    ("Successfully built " + "a" * 64, "Docker · 이미지 생성 명령 완료 · 식별정보 확인 대기"),
])
def test_recognized_phases_are_fixed_labels_not_raw_log_content(line, label):
    assert infer_phase(line) == label


def test_last_actual_phase_wins_and_maven_success_never_overrides_failed_status():
    output = "===> BUILDING\n[INFO] BUILD SUCCESS\n===> EXPORTING\nERROR: export failed\n"
    assert infer_phase(output) == "pack · 로컬 Docker 이미지로 내보내기"
    state = running_state(output_tail="[INFO] BUILD SUCCESS\nERROR: daemon disconnected")
    state.update(status="FAILED", finished_at=ago(1))
    view = describe_progress(state, NOW)
    assert view["status"] == "FAILED" and not view["process_running"]
    assert view["phase_label"] == "앱 컴파일 완료 · 이미지 준비 중"
    assert "success" not in view and "PASS" not in view.values()


@pytest.mark.parametrize("output", [
    "User says ===> EXPORTING", "===> EXPORTING <script>alert(1)</script>",
    "application BUILD SUCCESS", "[INFO] BUILD SUCCESS user-supplied", "#1 [<b>text</b>] RUN x",
    "<img src=x onerror=alert(1)>", None,
    "User says Step 1/5 : FROM python:3.13", "Step 6/5 : RUN command",
    "Step 0/5 : COPY input /app", "Step 1/5 : <script>arbitrary</script>",
    "User says Successfully built 012345abcdef", "Successfully built invalid-image-id",
    "Successfully built 012345abcdef PASS", "Successfully built <script>alert(1)</script>",
])
def test_unknown_or_unanchored_lines_do_not_create_a_phase(output):
    assert infer_phase(output) is None


def test_retained_known_phase_survives_tail_scroll_but_arbitrary_label_is_rejected():
    label = infer_phase("===> EXPORTING")
    state = running_state(phase_label=label, output_tail="streaming layer bytes\n")
    assert describe_progress(state, NOW)["phase_label"] == label
    state["process"].update(phase_label="<script>untrusted</script>", output_tail="===> BUILDING")
    assert describe_progress(state, NOW)["phase_label"] == infer_phase("===> BUILDING")


@pytest.mark.parametrize("status", ["RUNNING", "FAILED"])
def test_classic_docker_built_phase_never_marks_job_success(status):
    output = (
        "Step 1/3 : FROM python:3.13\n"
        "Step 2/3 : COPY server.py /app/server.py\n"
        "Step 3/3 : CMD [\"python\", \"/app/server.py\"]\n"
        "Successfully built 012345abcdef\n"
        "ERROR: image identity could not be verified\n"
    )
    label = "Docker · 이미지 생성 명령 완료 · 식별정보 확인 대기"
    assert infer_phase(output) == label
    state = running_state(output_tail=output)
    state.update(status=status)
    view = describe_progress(state, NOW)
    assert view["status"] == status and view["phase_label"] == label
    assert "success" not in view and "PASS" not in view.values()
    state["process"].update(phase_label=label, output_tail="later diagnostic output")
    assert describe_progress(state, NOW)["phase_label"] == label


def test_ansi_coloring_does_not_hide_actual_phase():
    assert infer_phase("\x1b[32m===> BUILDING\x1b[0m") == infer_phase("===> BUILDING")


def test_specific_containerd_warning_is_annotated_and_retained_without_settings_changes():
    note = infer_performance_note(PERFORMANCE_WARNING)
    assert note and "containerd" in note and "작업 중단을 뜻하지는 않습니다" in note
    assert infer_performance_note("Someone says containerd is slow") is None
    state = running_state(performance_note=note, output_tail="still exporting")
    assert describe_progress(state, NOW)["performance_note"] == note
    state["process"].update(performance_note="<b>make system changes</b>", output_tail="unknown warning")
    assert describe_progress(state, NOW)["performance_note"] is None


@pytest.mark.parametrize(("kind", "stage", "percent"), [
    ("build_and_test", "파일 준비", 0),
    ("build_and_test", "이미지 생성", 20),
    ("build_and_test", "생성 이미지 식별정보 확인", 20),
    ("build_and_test", "이미지 임시 쓰기 영역 메타데이터 준비", 20),
    ("build_and_test", "시험 이미지·실행 조건 확인", 40),
    ("build_and_test", "실행 시작", 40),
    ("build_and_test", "실제 루프백 포트 매핑 확인", 60),
    ("build_and_test", "재시작 후 웹 응답 확인", 60),
    ("build_and_test", "웹 응답 시험 통과", 80),
    ("retest", "실행 시작", 25),
    ("retest", "웹 응답 확인", 50),
    ("retest", "웹 응답 시험 통과", 75),
    ("save_image", "이미지를 디스크 tar로 저장 중", 0),
    ("save_image", "이미지 파일 SHA256 확인", 33),
    ("save_image", "이미지 파일 저장 확정", 66),
])
def test_percentage_counts_confirmed_milestones_not_current_stage_entry(kind, stage, percent):
    state = {"kind": kind, "status": "RUNNING", "stage": stage}
    assert describe_completion(state)["percent"] == percent
    # Even hours of elapsed time and optimistic log text cannot advance it.
    state.update(elapsed_seconds=999999, process={"output_tail": "[INFO] BUILD SUCCESS\n===> EXPORTING"})
    assert describe_completion(state)["percent"] == percent


@pytest.mark.parametrize("status", ["RUNNING", "FAILED", "CANCELLED", "BLOCKED"])
def test_cleanup_after_port_mapping_failure_never_implies_http_pass(status):
    state = {
        "kind": "build_and_test", "status": status, "stage": "시험 자원 정리",
        "stage_history": [{"stage": "시험 이미지·실행 조건 확인"},
                          {"stage": "실제 루프백 포트 매핑 확인"}, {"stage": "시험 자원 정리"}],
        "result": {"check": {"status": "ENVIRONMENT_BLOCKED", "cleanup_status": "CLEANED"}},
    }
    view = describe_completion(state)
    assert view["percent"] == 60 and view["completed"] == 3 and not view["finished"]


def test_success_needs_done_http_result_required_restart_and_cleanup():
    state = {
        "kind": "build_and_test", "status": "RUNNING", "stage": "시험 자원 정리",
        "stage_history": [{"stage": "웹 응답 시험 통과"}, {"stage": "시험 자원 정리"}],
        "result": {"check": {"status": "PASS", "http_status": 200, "cleanup_status": "CLEANED",
                             "conditions": {"restart": True}, "restart_passed": True}},
    }
    assert describe_completion(state)["percent"] == 80
    state["status"] = "DONE"
    assert describe_completion(state)["percent"] == 100
    state["result"]["check"]["restart_passed"] = False
    assert describe_completion(state)["percent"] == 80
    state["result"]["check"]["conditions"]["restart"] = False
    assert describe_completion(state)["percent"] == 100
    state["result"]["check"]["cleanup_status"] = "NEEDS_ATTENTION"
    assert describe_completion(state)["percent"] == 80
    state["result"]["check"]["cleanup_status"] = "CLEANED"
    state["result"]["check"]["status"] = "FAIL"  # HTTP 200 with a rejected body is not success.
    assert describe_completion(state)["percent"] == 80
    state["result"]["check"]["status"] = "PASS"
    state["cancel_requested_at"] = ago(1)
    assert describe_completion(state)["percent"] == 80


def test_tar_only_reaches_100_after_successful_worker_completion():
    state = {"kind": "save_image", "stage": "이미지 파일 저장 확정", "status": "RUNNING"}
    assert describe_completion(state)["percent"] == 66
    state["status"] = "FAILED"
    assert describe_completion(state)["percent"] == 66
    state["status"] = "DONE"
    # Plain dict tar metadata is not persisted in state.result by JobManager.
    assert describe_completion(state)["percent"] == 100


@pytest.mark.parametrize("state", [None, [], {}, {"kind": []}, {"kind": "unknown"}])
def test_unknown_job_has_no_invented_percentage(state):
    assert describe_completion(state) is None


def test_malformed_legacy_history_and_done_without_runtime_evidence_are_conservative():
    state = {"kind": "build_and_test", "status": "DONE", "stage": [],
             "stage_history": [None, [], {"stage": []}, {"stage": "시험 자원 정리"}],
             "result": {"check": []}}
    assert describe_completion(state)["percent"] == 0


def workflow_state(**updates):
    check = {
        "status": "PASS", "outcome": "PASS", "image_verified": True, "container_started": True,
        "image_id": "sha256:" + "a" * 64, "initial_http_passed": True, "initial_http_status": 200,
        "http_status": 200, "restart_performed": True, "restart_passed": True,
        "restart_http_status": 200, "cleanup_status": "CLEANED", "conditions": {"restart": True},
        "port_evidence": [{"phase": "start", "verified": True},
                          {"phase": "restart", "verified": True}],
    }
    check.update(updates)
    return {"kind": "build_and_test", "status": "DONE", "stage": "시험 자원 정리",
            "result": {"check": check}}


def row_statuses(view):
    return {row["key"]: row["status"] for row in view["rows"]}


def test_workflow_success_requires_separate_observed_results_for_each_row():
    state = workflow_state()
    view = describe_workflow(state)
    assert view["outcome"] == "PASS"
    assert view["image_id"] == "sha256:" + "a" * 64
    assert set(row_statuses(view).values()) == {"PASS"}


@pytest.mark.parametrize("restart", [False, True])
def test_port_mapping_block_never_becomes_image_or_http_failure(restart):
    evidence = [{"phase": "start", "verified": True}] if restart else []
    evidence.append({"phase": "restart" if restart else "start", "verified": False})
    state = workflow_state(
        status="ENVIRONMENT_BLOCKED", outcome="ENVIRONMENT_BLOCKED_PORT_MAPPING",
        failure_layer="port_mapping", port_evidence=evidence, initial_http_passed=restart,
        initial_http_status=200 if restart else None, http_status=200 if restart else None,
        restart_performed=restart, restart_passed=False, restart_http_status=None,
    )
    state["status"] = "FAILED"
    view = describe_workflow(state)
    rows = row_statuses(view)
    assert view["outcome"] == "ENVIRONMENT_BLOCKED_PORT_MAPPING"
    assert rows["image_build"] == rows["container_start"] == rows["cleanup"] == "PASS"
    assert rows["restart_port_mapping" if restart else "port_mapping"] == "BLOCKED"
    assert rows["initial_http"] == ("PASS" if restart else "NOT_RUN")
    assert rows["restart_http"] == "NOT_RUN"


def test_initial_http_200_with_wrong_body_is_failed_not_passed():
    state = workflow_state(
        status="FAIL", outcome="INITIAL_HTTP_FAILED", failure_layer="initial_http",
        initial_http_passed=False, initial_http_status=200, restart_performed=False,
        restart_passed=False, restart_http_status=None,
        port_evidence=[{"phase": "start", "verified": True}],
    )
    state["status"] = "FAILED"
    rows = row_statuses(describe_workflow(state))
    assert rows["initial_http"] == "FAIL"
    assert rows["port_mapping"] == rows["cleanup"] == "PASS"
    assert rows["restart"] == rows["restart_port_mapping"] == rows["restart_http"] == "NOT_RUN"
    assert describe_completion(state)["percent"] < 100


@pytest.mark.parametrize(("layer", "failed"), [
    ("container_restart", "restart"), ("restart_http", "restart_http"),
])
def test_restart_command_and_restart_http_failures_remain_distinct(layer, failed):
    state = workflow_state(
        status="FAIL", outcome="RESTART_HTTP_FAILED", failure_layer=layer,
        restart_performed=layer == "restart_http", restart_passed=False,
        port_evidence=[{"phase": "start", "verified": True}],
    )
    state["status"] = "FAILED"
    rows = row_statuses(describe_workflow(state))
    assert rows["initial_http"] == rows["cleanup"] == "PASS"
    assert rows[failed] == "FAIL"
    if layer == "container_restart":
        assert rows["restart_port_mapping"] == rows["restart_http"] == "NOT_RUN"


def test_build_failure_cannot_claim_runtime_was_attempted():
    state = {"kind": "build_and_test", "status": "FAILED", "stage": "이미지 생성",
             "outcome": "IMAGE_BUILD_FAILED", "logs": "Successfully built " + "a" * 64}
    view = describe_workflow(state)
    assert view["image_id"] is None and view["outcome"] == "IMAGE_BUILD_FAILED"
    rows = row_statuses(view)
    assert rows.pop("image_build") == "FAIL"
    assert set(rows.values()) == {"NOT_RUN"}


def test_live_image_id_is_visible_before_runtime_finishes():
    state = {"kind": "build_and_test", "status": "RUNNING", "stage": "실행 시작",
             "result": {"artifact": {"image_id": "sha256:" + "b" * 64}}}
    view = describe_workflow(state)
    assert view["image_id"] == "sha256:" + "b" * 64
    assert row_statuses(view)["image_build"] == "PASS"
    assert row_statuses(view)["container_start"] == "RUNNING"


def test_legacy_failure_layer_fallback_retains_exact_blocked_layer():
    state = workflow_state(status="ENVIRONMENT_BLOCKED", failure_layer="port_mapping",
                           initial_http_passed=False, restart_performed=False, restart_passed=False,
                           port_evidence=[{"phase": "start", "verified": False}])
    state["result"]["check"].pop("outcome")
    state["status"] = "FAILED"
    assert describe_workflow(state)["outcome"] == "ENVIRONMENT_BLOCKED_PORT_MAPPING"
    assert row_statuses(describe_workflow(state))["initial_http"] == "NOT_RUN"


def test_live_cleanup_entry_never_marks_unattempted_http_as_success():
    state = {"kind": "build_and_test", "status": "RUNNING", "stage": "시험 자원 정리",
             "stage_history": [{"stage": "실제 루프백 포트 매핑 확인"}, {"stage": "시험 자원 정리"}]}
    rows = row_statuses(describe_workflow(state))
    assert rows["container_start"] == "PASS"
    assert rows["port_mapping"] == rows["initial_http"] == rows["restart_http"] == "NOT_RUN"
    assert rows["cleanup"] == "RUNNING"


@pytest.mark.parametrize("state", [None, {}, [], {"kind": []}, {"kind": "save_image"}])
def test_unrelated_or_malformed_state_has_no_invented_workflow(state):
    assert describe_workflow(state) is None
