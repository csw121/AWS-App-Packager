"""Pure progress presentation from observed job state; no completion predictions."""

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime

MAX_LOG_TAIL = 65_536
SILENCE_THRESHOLD_SECONDS = 60

_PACK_PHASES = {
    "ANALYZING": "pack · 이전 이미지와 빌드 정보 확인",
    "DETECTING": "pack · Java buildpack 확인",
    "RESTORING": "pack · 빌드 캐시 복원",
    "BUILDING": "pack · Java 의존성 준비와 앱 빌드",
    "EXPORTING": "pack · 로컬 Docker 이미지로 내보내기",
}
_MAVEN_DOWNLOAD = "Maven · Java 의존성 다운로드"
_MAVEN_SUCCESS = "앱 컴파일 완료 · 이미지 준비 중"
_DOCKER_INPUT = "Docker · 빌드 입력과 기반 이미지 준비"
_DOCKER_AUTH = "Docker · 이미지 다운로드 인증 확인"
_DOCKER_STEP = "Dockerfile · 빌드 단계 실행"
_DOCKER_EXPORT = "Docker · 로컬 이미지로 내보내기"
_DOCKER_BUILT = "Docker · 이미지 생성 명령 완료 · 식별정보 확인 대기"
_PHASE_LABELS = set(_PACK_PHASES.values()) | {
    _MAVEN_DOWNLOAD, _MAVEN_SUCCESS, _DOCKER_INPUT, _DOCKER_AUTH, _DOCKER_STEP, _DOCKER_EXPORT,
    _DOCKER_BUILT,
}
_PERFORMANCE_NOTE = (
    "containerd 저장소에서 로컬 이미지 내보내기가 느려질 수 있다는 pack 경고가 있습니다. "
    "이 경고만으로 작업 중단을 뜻하지는 않습니다."
)
_SILENCE_MESSAGE = (
    "최근 출력이 없습니다. 로그가 없는 구간일 수 있으며, 출력이 없다는 사실만으로 "
    "작업이 멈췄다고 판단할 수 없습니다."
)
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_PACK_LINE = re.compile(r"===> (ANALYZING|DETECTING|RESTORING|BUILDING|EXPORTING)")
_MAVEN_PREFIX = r"(?:\[builder\]\s+)?"
_MAVEN_DOWNLOAD_LINE = re.compile(
    _MAVEN_PREFIX + r"(?:\[INFO\]\s+)?(?:Downloading|Downloaded) from [A-Za-z0-9_.-]+:\s+\S.*"
)
_MAVEN_SUCCESS_LINE = re.compile(_MAVEN_PREFIX + r"\[INFO\]\s+BUILD SUCCESS")
_DOCKER_LINE = re.compile(
    r"#\d+\s+\[(internal|auth|(?:[A-Za-z0-9_.-]+\s+)?\d+/\d+)\]\s+\S.*"
)
_DOCKER_EXPORT_LINE = re.compile(
    r"#\d+\s+exporting (?:to image|layers)(?:\s+(?:done|\d+(?:\.\d+)?s(?:\s+done)?))?"
)
_DOCKER_CLASSIC_STEP = re.compile(
    r"Step ([1-9]\d{0,5})/([1-9]\d{0,5}) : "
    r"(FROM|RUN|CMD|LABEL|MAINTAINER|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|USER|WORKDIR|ARG|"
    r"ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\s+\S.*"
)
_DOCKER_CLASSIC_BUILT = re.compile(r"Successfully built (?:[0-9a-f]{12}|[0-9a-f]{64})")
_PERFORMANCE_LINE = re.compile(
    r"Warning: Exporting to docker daemon \(building without --publish\) and daemon uses "
    r"containerd storage; performance may be significantly degraded\."
)
_STATUSES = {"IDLE", "RUNNING", "DONE", "FAILED", "CANCELLED", "BLOCKED"}

# These are completed workflow milestones, not time/download estimates. A stage
# callback reports ENTRY: only known sequential boundaries confirm earlier work.
# Cleanup runs on failure too, so entering it must never advance this count.
_RUNTIME_COMPLETED = {
    "시험 이미지·실행 조건 확인": 0,
    "실행 시작": 1,
    "실제 루프백 포트 매핑 확인": 2,
    "웹 응답 확인": 2,
    "컨테이너 재시작": 2,
    "재시작 후 루프백 매핑 확인": 2,
    "재시작 후 웹 응답 확인": 2,
    "웹 응답 시험 통과": 3,
}
_COMPLETION_PLANS = {
    "build_and_test": (
        ("파일 준비", "이미지 생성", "컨테이너 시작", "웹 응답 시험", "정리·완료 확인"),
        {
            "파일 준비": 0, "실행 도구 확인": 1, "이미지 생성": 1,
            "생성 이미지 식별정보 확인": 1, "이미지 임시 쓰기 영역 메타데이터 준비": 1,
            **{stage: max(2, count + 1) for stage, count in _RUNTIME_COMPLETED.items()},
        },
    ),
    "retest": (
        ("실행 조건 확인", "컨테이너 시작", "웹 응답 시험", "정리·완료 확인"),
        _RUNTIME_COMPLETED,
    ),
    "save_image": (
        ("tar 파일 저장", "SHA256 확인", "저장 확정"),
        {"이미지를 디스크 tar로 저장 중": 0, "이미지 파일 SHA256 확인": 1,
         "이미지 파일 저장 확정": 2},
    ),
}


_WORKFLOW_ROWS = (
    ("image_build", "이미지 만들기"),
    ("container_start", "컨테이너 시작"),
    ("port_mapping", "로컬 포트 연결"),
    ("initial_http", "최초 HTTP 시험"),
    ("restart", "컨테이너 재시작"),
    ("restart_port_mapping", "재시작 후 포트 연결"),
    ("restart_http", "재시작 후 HTTP 시험"),
    ("cleanup", "시험 자원 정리"),
)
_WORKFLOW_STAGES = {
    "파일 준비": "image_build", "실행 도구 확인": "image_build", "이미지 생성": "image_build",
    "생성 이미지 식별정보 확인": "image_build", "이미지 임시 쓰기 영역 메타데이터 준비": "image_build",
    "실행 시작": "container_start", "실제 루프백 포트 매핑 확인": "port_mapping",
    "웹 응답 확인": "initial_http", "컨테이너 재시작": "restart",
    "재시작 후 루프백 매핑 확인": "restart_port_mapping", "재시작 후 웹 응답 확인": "restart_http",
    "시험 자원 정리": "cleanup",
}
_OUTCOMES = {
    "IMAGE_BUILD_FAILED", "CONTAINER_START_FAILED", "ENVIRONMENT_BLOCKED_PORT_MAPPING",
    "INITIAL_HTTP_FAILED", "RESTART_HTTP_FAILED", "PASS", "CANCELLED", "CLEANUP_FAILED",
    "NOT_RUN",
}


def describe_workflow(state: object) -> dict | None:
    """Per-stage evidence for UI and integration reports; HTTP 200 alone never means PASS."""
    if not isinstance(state, Mapping) or state.get("kind") not in ("build_and_test", "retest"):
        return None
    result = state.get("result")
    result = result if isinstance(result, Mapping) else {}
    artifact = result.get("artifact")
    artifact = artifact if isinstance(artifact, Mapping) else {}
    check = result.get("check")
    check = check if isinstance(check, Mapping) else {}
    conditions = check.get("conditions")
    conditions = conditions if isinstance(conditions, Mapping) else {}
    status = state.get("status")
    layer = check.get("failure_layer")
    outcomes = (state.get("outcome"), check.get("outcome"))
    outcome = next((value for value in outcomes if isinstance(value, str)
                    and value in _OUTCOMES and value != "NOT_RUN"), "NOT_RUN")
    rows = {key: "NOT_RUN" for key, _ in _WORKFLOW_ROWS}
    image_id = artifact.get("image_id")
    if not image_id and check.get("image_verified") is True:
        image_id = check.get("image_id")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        image_id = None
    if image_id:
        rows["image_build"] = "PASS"
    if check.get("container_started") is True:
        rows["container_start"] = "PASS"
    evidence = check.get("port_evidence")
    evidence = evidence if isinstance(evidence, list) else []
    for record in evidence:
        if not isinstance(record, Mapping) or record.get("phase") not in ("start", "restart"):
            continue
        key = "port_mapping" if record["phase"] == "start" else "restart_port_mapping"
        rows[key] = "PASS" if record.get("verified") is True else "BLOCKED"
    # A successful legacy runtime verdict includes body checks. A raw status code does not.
    legacy_pass = "initial_http_passed" not in check and check.get("status") == "PASS"
    if check.get("initial_http_passed") is True or legacy_pass:
        rows["initial_http"] = "PASS"
    if check.get("restart_performed") is True:
        rows["restart"] = "PASS"
    if check.get("restart_passed") is True:
        rows["restart_http"] = "PASS"
    if check.get("cleanup_status") == "CLEANED":
        rows["cleanup"] = "PASS"
    elif check.get("cleanup_status") == "NEEDS_ATTENTION":
        rows["cleanup"] = "FAIL"

    # While the worker is running, entering a subsequent stage proves the preceding
    # operation returned successfully. Cleanup is deliberately excluded from this rule.
    if not check:
        history = state.get("stage_history")
        stages = [entry.get("stage") for entry in history if isinstance(entry, Mapping)] \
            if isinstance(history, list) else []
        stages.append(state.get("stage"))
        confirmed = {
            "시험 이미지·실행 조건 확인": ("image_build",),
            "실제 루프백 포트 매핑 확인": ("image_build", "container_start"),
            "웹 응답 확인": ("image_build", "container_start", "port_mapping"),
            "컨테이너 재시작": ("image_build", "container_start", "port_mapping", "initial_http"),
            "재시작 후 루프백 매핑 확인": (
                "image_build", "container_start", "port_mapping", "initial_http", "restart"),
            "재시작 후 웹 응답 확인": (
                "image_build", "container_start", "port_mapping", "initial_http", "restart",
                "restart_port_mapping"),
        }
        for stage in stages:
            if isinstance(stage, str):
                for key in confirmed.get(stage, ()):
                    rows[key] = "PASS"
    current_stage = state.get("stage")
    active = _WORKFLOW_STAGES.get(current_stage) if isinstance(current_stage, str) else None
    if status == "RUNNING" and active and rows[active] == "NOT_RUN":
        rows[active] = "RUNNING"

    failure_row = {
        "network_creation": "container_start", "container_creation": "container_start",
        "container_start": "container_start", "mount_safety": "container_start",
        "docker_preflight": "container_start", "image_identity": "container_start",
        "runtime_conditions": "container_start", "runtime_authorization": "container_start",
        "sample_authorization": "container_start", "initial_http": "initial_http",
        "container_restart": "restart", "restart_http": "restart_http", "cleanup": "cleanup",
    }.get(layer) if isinstance(layer, str) else None
    if layer == "port_mapping" or outcome == "ENVIRONMENT_BLOCKED_PORT_MAPPING":
        after_restart = check.get("restart_performed") is True or any(
            isinstance(record, Mapping) and record.get("phase") == "restart" for record in evidence
        )
        failure_row = "restart_port_mapping" if after_restart else "port_mapping"
        outcome = "ENVIRONMENT_BLOCKED_PORT_MAPPING"
    elif outcome == "NOT_RUN":
        if layer == "initial_http":
            outcome = "INITIAL_HTTP_FAILED"
        elif layer in ("container_restart", "restart_http"):
            outcome = "RESTART_HTTP_FAILED"
        elif layer == "cleanup":
            outcome = "CLEANUP_FAILED"
        elif failure_row == "container_start":
            outcome = "CONTAINER_START_FAILED"
        elif check.get("status") == "PASS":
            outcome = "PASS"
        elif status == "FAILED" and active == "image_build" and not image_id:
            outcome = "IMAGE_BUILD_FAILED"
    if outcome == "IMAGE_BUILD_FAILED":
        failure_row = "image_build"
    elif outcome == "CONTAINER_START_FAILED" and not failure_row:
        failure_row = "container_start"
    elif outcome == "INITIAL_HTTP_FAILED":
        failure_row = "initial_http"
    elif outcome == "RESTART_HTTP_FAILED" and not failure_row:
        failure_row = "restart_http"
    if status == "CANCELLED" or check.get("status") == "CANCELLED":
        outcome = "CANCELLED"
        if failure_row and rows[failure_row] != "PASS":
            rows[failure_row] = "CANCELLED"
        elif active and rows[active] != "PASS":
            rows[active] = "CANCELLED"
    elif failure_row:
        rows[failure_row] = "BLOCKED" if outcome == "ENVIRONMENT_BLOCKED_PORT_MAPPING" else "FAIL"
    if conditions.get("restart") is False:
        for key in ("restart", "restart_port_mapping", "restart_http"):
            rows[key] = "NOT_RUN"
    # A persisted PASS with missing new evidence remains visibly incomplete.
    if outcome == "PASS" and (status != "DONE" or check.get("status") != "PASS"
                              or check.get("cleanup_status") != "CLEANED"):
        outcome = "NOT_RUN"
    return {"outcome": outcome, "image_id": image_id,
            "rows": [{"key": key, "label": label, "status": rows[key]}
                     for key, label in _WORKFLOW_ROWS]}


def describe_completion(state: object) -> dict | None:
    """Completed sequential milestones; never infer success from logs or cleanup entry."""
    if not isinstance(state, Mapping):
        return None
    kind = state.get("kind")
    if not isinstance(kind, str) or kind not in _COMPLETION_PLANS:
        return None
    labels, boundaries = _COMPLETION_PLANS[kind]
    history = state.get("stage_history")
    stages = (
        [item.get("stage") for item in history[-24:] if isinstance(item, Mapping)]
        if isinstance(history, list) else []
    )
    stages.append(state.get("stage"))
    completed = max((boundaries.get(stage, 0) for stage in stages if isinstance(stage, str)), default=0)
    result = state.get("result")
    check = result.get("check") if isinstance(result, Mapping) else None
    check = check if isinstance(check, Mapping) else {}
    conditions = check.get("conditions")
    conditions = conditions if isinstance(conditions, Mapping) else {}
    finished = state.get("status") == "DONE" and not state.get("cancel_requested_at")
    if kind != "save_image":
        finished = (
            finished and check.get("status") == "PASS" and check.get("http_status") == 200
            and check.get("initial_http_passed") is not False
            and check.get("cleanup_status") == "CLEANED"
            and (conditions.get("restart") is False or check.get("restart_passed") is True)
        )
    if finished:
        completed = len(labels)
    return {
        "percent": completed * 100 // len(labels),
        "completed": completed,
        "total": len(labels),
        "labels": labels,
        "finished": bool(finished),
    }


def _log_lines(output: object) -> list[str]:
    if not isinstance(output, str):
        return []
    tail = output[-MAX_LOG_TAIL:]
    if len(output) > MAX_LOG_TAIL and output[-MAX_LOG_TAIL - 1] not in "\r\n":
        tail = tail.partition("\n")[2]  # Do not invent an anchored line by cutting its prefix.
    return [line.strip() for line in _ANSI.sub("", tail).splitlines()]


def infer_phase(output: str) -> str | None:
    """Return the last recognized log phase, never a job success or untrusted log text."""
    phase = None
    for line in _log_lines(output):
        if match := _PACK_LINE.fullmatch(line):
            phase = _PACK_PHASES[match[1]]
        elif _MAVEN_DOWNLOAD_LINE.fullmatch(line):
            phase = _MAVEN_DOWNLOAD
        elif _MAVEN_SUCCESS_LINE.fullmatch(line):
            phase = _MAVEN_SUCCESS
        elif match := _DOCKER_LINE.fullmatch(line):
            phase = {
                "internal": _DOCKER_INPUT, "auth": _DOCKER_AUTH,
            }.get(match[1], _DOCKER_STEP)
        elif _DOCKER_EXPORT_LINE.fullmatch(line):
            phase = _DOCKER_EXPORT
        elif match := _DOCKER_CLASSIC_STEP.fullmatch(line):
            if int(match[1]) <= int(match[2]):
                phase = _DOCKER_INPUT if match[3] == "FROM" else _DOCKER_STEP
        elif _DOCKER_CLASSIC_BUILT.fullmatch(line):
            phase = _DOCKER_BUILT
    return phase


def infer_performance_note(output: str) -> str | None:
    """Recognize only the observed pack/containerd warning, not arbitrary slowdown claims."""
    observed = any(_PERFORMANCE_LINE.fullmatch(line) for line in _log_lines(output))
    return _PERFORMANCE_NOTE if observed else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def format_duration(seconds: object) -> str:
    """Korean elapsed duration, rounded down; malformed legacy values are harmless."""
    total = int(_number(seconds) or 0)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}시간 {minutes:02d}분 {seconds:02d}초"
    if minutes:
        return f"{minutes}분 {seconds:02d}초"
    return f"{seconds}초"


def _timestamp(value: object) -> datetime | None:
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str) and len(value) <= 80:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        # Old records without an offset used UTC, as do current persisted timestamps.
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _elapsed(start: datetime | None, end: datetime | None) -> int:
    return max(0, int((end - start).total_seconds())) if start is not None and end is not None else 0


def describe_progress(state: object, now: object = None) -> dict:
    """Describe observed elapsed time and configured limits; timeout remaining is not an ETA."""
    state = state if isinstance(state, Mapping) else {}
    process = state.get("process")
    process = process if isinstance(process, Mapping) else {}
    raw_status = state.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status in _STATUSES else "UNKNOWN"
    running = status == "RUNNING" and process.get("running") is True
    current_time = _timestamp(now) or datetime.now(UTC)
    finished = _timestamp(state.get("finished_at"))
    # Completed records without a finish timestamp have unknown timing, represented by zero.
    endpoint = current_time if status == "RUNNING" else finished
    started = _timestamp(state.get("started_at"))
    stage_started = _timestamp(state.get("stage_started_at"))
    history = state.get("stage_history")
    if stage_started is None and isinstance(history, list):
        for item in reversed(history[-100:]):
            if isinstance(item, Mapping) and item.get("stage") == state.get("stage"):
                stage_started = _timestamp(item.get("started_at"))
                if stage_started is not None:
                    break

    process_started = _timestamp(process.get("started_at"))
    process_elapsed = _number(process.get("elapsed_seconds")) or 0
    if running and process_started is not None:
        process_elapsed = max(process_elapsed, _elapsed(process_started, current_time))
    last_output = _timestamp(process.get("last_output_at"))
    if last_output is not None:
        silence = _elapsed(last_output, endpoint)
    elif process_started is not None and endpoint is not None:
        silence = _elapsed(process_started, endpoint)
    else:
        silence = int(process_elapsed)
    silence_warning = running and silence > SILENCE_THRESHOLD_SECONDS
    timeout = _number(process.get("timeout_seconds"))
    remaining = max(0, timeout - process_elapsed) if running and timeout is not None and timeout > 0 else None

    retained_phase = process.get("phase_label")
    phase = (
        retained_phase if isinstance(retained_phase, str) and retained_phase in _PHASE_LABELS
        else infer_phase(process.get("output_tail", ""))
    )
    retained_note = process.get("performance_note")
    note = (
        _PERFORMANCE_NOTE if retained_note == _PERFORMANCE_NOTE
        else infer_performance_note(process.get("output_tail", ""))
    )
    return {
        "status": status,
        "process_running": running,
        "elapsed_seconds": _elapsed(started, endpoint),
        "stage_elapsed_seconds": _elapsed(stage_started, endpoint),
        "silence_seconds": silence,
        "silence_warning": silence_warning,
        "silence_message": _SILENCE_MESSAGE if silence_warning else None,
        "phase_label": phase,
        "timeout_remaining_seconds": remaining,
        "performance_note": note,
    }
