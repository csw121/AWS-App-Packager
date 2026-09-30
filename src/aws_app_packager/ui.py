"""Five focused steps backed by the real application service."""

import json
import re
from pathlib import Path

import streamlit as st

from . import service
from .config import EXPORT_DIR, ROOT
from .jobs import MANAGER
from .models import DeploymentSpec, RuntimeConditions
from .preflight import check_environment
from .presets import PRESETS
from .progress import describe_completion, describe_progress, describe_workflow, format_duration
from .redaction import redact

STEPS = ["1 앱 선택", "2 실행 준비", "3 만들고 시험", "4 실행 크기", "5 결과물"]
LABELS = {"small": "소형", "medium": "중형", "large": "대형"}


def _clear_assessment():
    for key in ("assessment", "artifact", "check", "bundle", "tar_job", "build_approval", "runtime_approval"):
        st.session_state.pop(key, None)


def _clear_bundle():
    st.session_state.pop("bundle", None)
    st.session_state.pop("tar_job", None)


def _reset_runtime_approval():
    st.session_state.runtime_approval = False
    st.session_state.retest_approval = False
    st.session_state.pop("bundle", None)


def _candidate_selected(path):
    _clear_assessment()
    st.session_state.source_path = str(path)


def _go(step):
    st.session_state.step = step
    st.rerun()


def _error(exc):
    st.error(_literal(str(exc)))
    st.caption("입력 또는 환경을 수정한 뒤 다시 시도하세요.")


def _literal(text):
    return re.sub(r"([\\`*{}\[\]()#+!|>_])", r"\\\1", redact(text))


def _conditions():
    assessment = st.session_state.assessment
    environment = json.loads(st.session_state.get("environment_json", "{}") or "{}")
    return RuntimeConditions(
        preset=st.session_state.get("preset", "medium"),
        container_port=int(st.session_state.get("app_port", 0)),
        health_path=st.session_state.get("health_path", ""),
        environment=environment,
        expected_marker=service.sample_marker(assessment.root),
        network_mode="approved_project_bridge",
    )


def _assessment_details(assessment):
    with st.expander("기술 상세 · 확인 근거와 제외 범위"):
        st.write("파일 선언과 실제 실행 관찰은 서로 다른 근거입니다.")
        for message in assessment.evidence + assessment.warnings:
            st.text(message)
        st.write("제외한 파일/폴더")
        for item in assessment.excluded[:100]:
            st.text(item)
        st.caption(
            f"파일 {assessment.file_count}개 · {assessment.total_bytes:,} bytes · "
            "민감값 탐지는 완전하지 않습니다."
        )
        st.code(assessment.source_fingerprint, language=None)


def select_app():
    st.subheader("실행할 앱 하나를 선택하세요")
    st.write("절대경로를 입력하면 읽기 전용으로 실행 단서와 빌드 범위를 확인합니다.")
    left, right = st.columns(2)
    if left.button("Java 검증 샘플 선택", use_container_width=True):
        _clear_assessment()
        st.session_state.source_path = str(ROOT / "samples" / "spring-http")
    if right.button("Dockerfile 검증 샘플 선택", use_container_width=True):
        _clear_assessment()
        st.session_state.source_path = str(ROOT / "samples" / "docker-http")
    source = st.text_input(
        "앱 폴더 절대경로", key="source_path", placeholder=r"D:\my-app", on_change=_clear_assessment
    )
    if st.button("위치와 빌드 범위 확인", type="primary"):
        try:
            assessment = service.assess_project(Path(source))
            st.session_state.assessment = assessment
            for key in (
                "artifact",
                "check",
                "bundle",
                "build_approval",
                "runtime_approval",
                "retest_approval",
            ):
                st.session_state.pop(key, None)
            st.session_state.preset = "medium"
            st.session_state.app_port = (
                assessment.port_candidates[0] if len(assessment.port_candidates) == 1 else 0
            )
            st.session_state.health_path = (
                "/health" if service.sample_marker(assessment.root) else assessment.health_path or ""
            )
            st.session_state.environment_json = "{}"
        except Exception as exc:
            st.session_state.pop("assessment", None)
            _error(exc)
    assessment = st.session_state.get("assessment")
    if assessment:
        route = {
            "paketo_java": "Java 웹앱 빌드 경로를 사용할 수 있습니다.",
            "dockerfile": "기존 Dockerfile을 사용합니다.",
            "unsupported": "현재 자동 준비를 지원하지 않습니다.",
        }[assessment.method]
        st.info(route)
        st.caption("파일을 읽은 결과입니다. 아직 이미지를 만들거나 실행하지 않았습니다.")
        for blocker in assessment.blockers:
            st.error(_literal(blocker))
        if assessment.blockers:
            st.download_button(
                "진단·준비사항만 받기",
                data=service.diagnostic_report(assessment),
                file_name="DIAGNOSTIC_ONLY.json",
                mime="application/json",
            )
        if assessment.candidates:
            candidate = st.selectbox("저장소 안의 앱 후보", assessment.candidates)
            st.button(
                "이 후보의 경로 사용", on_click=_candidate_selected, args=(assessment.root / candidate,)
            )
        _assessment_details(assessment)
        if st.button("실행 준비로", disabled=bool(assessment.blockers) or assessment.method == "unsupported"):
            _go(1)


def prepare():
    assessment = st.session_state.assessment
    st.subheader("빌드와 실행할 범위를 확인하세요")
    st.write("원본은 바꾸지 않고, 승인한 파일만 도구의 작업 폴더에 복사합니다.")
    st.warning("빌드와 실행은 프로젝트 코드를 실행합니다. 신뢰하는 앱과 전용 개발 환경에서 사용하세요.")
    st.write(
        "빌드 시 공식 이미지·JVM·Maven 의존성을 내려받을 수 있습니다. "
        "빌드 코드도 네트워크를 사용할 수 있습니다."
    )
    if assessment.method == "paketo_java":
        st.caption(
            "Java 이미지는 AWS 임시 저장소 연결을 위해 /tmp volume 메타데이터를 추가합니다. "
            "실행 사용자와 시작 명령을 유지하며, 이 최종 이미지로 시험합니다."
        )
    st.write(
        "로컬 시험: 승인한 작업마다 별도의 일반 bridge 네트워크를 만듭니다. "
        "앱의 외부 통신은 허용되며, 접속 포트는 이 PC의 127.0.0.1에만 연결합니다. "
        "읽기 전용 루트와 임시 /tmp를 사용합니다."
    )
    st.caption(
        "Docker는 악성 코드에 대한 완전한 보안 경계가 아닙니다. 실제 비밀번호·API 키는 입력하지 마세요."
    )
    if st.button("실행 도구 상태 확인"):
        st.session_state.environment_check = check_environment()
    if st.session_state.get("environment_check"):
        with st.expander("Python / Docker / pack 상태", expanded=True):
            st.json({k: v for k, v in st.session_state.environment_check.items() if not k.endswith("_path")})
    _assessment_details(assessment)
    if not st.session_state.get("app_port") or not st.session_state.get("health_path"):
        st.info("앱의 접속 설정을 확인해야 합니다. 선언된 포트는 실제 listener 확인 결과가 아닙니다.")
    st.number_input(
        "컨테이너 내부 포트",
        min_value=0,
        max_value=65535,
        step=1,
        key="app_port",
        on_change=_reset_runtime_approval,
    )
    supported_port = st.session_state.get("app_port") == 8080
    if not supported_port:
        st.error("승인된 로컬 시험은 컨테이너 내부 포트 8080만 지원합니다. 앱의 포트 설정을 확인하세요.")
    st.caption("호스트 접속 포트는 Docker가 빈 임시 포트로 할당하며, 시작·재시작 후 각각 검증합니다.")
    st.text_input(
        "로컬 HTTP 시험 경로", placeholder="/health", key="health_path", on_change=_reset_runtime_approval
    )
    with st.expander("고급 · 비민감 실행 설정"):
        st.caption("PORT, SERVER_PORT, TZ, LANG, BPL_JVM_THREAD_COUNT, BPL_JVM_HEAD_ROOM만 허용합니다.")
        st.text_area("환경설정 JSON", key="environment_json", on_change=_reset_runtime_approval)
    st.checkbox(
        "내가 신뢰하는 프로젝트이며, 이 복사본의 빌드 코드를 실행하는 데 동의합니다", key="build_approval"
    )
    st.checkbox(
        "외부 통신 가능한 작업 전용 네트워크에서 이미지 실행과 127.0.0.1 HTTP 시험·재시작에 동의합니다",
        key="runtime_approval",
    )
    left, right = st.columns(2)
    if left.button("이전 · 앱 선택"):
        _go(0)
    if right.button(
        "만들고 시험하기",
        type="primary",
        disabled=not (
            supported_port and st.session_state.build_approval and st.session_state.runtime_approval
        ),
    ):
        try:
            conditions = _conditions()
            job_id = service.start_build(assessment, conditions, build_approved=True, runtime_approved=True)
            st.session_state.active_job_id = job_id
            for key in ("artifact", "check", "bundle"):
                st.session_state.pop(key, None)
            _go(2)
        except Exception as exc:
            _error(exc)


def _progress_details(state):
    """Show completed milestones prominently; elapsed time is never converted into percent."""
    status = state.get("status", "IDLE")
    workflow = describe_workflow(state)
    if workflow:
        title = {
            "IMAGE_BUILD_FAILED": "이미지 만들기 실패",
            "CONTAINER_START_FAILED": "이미지는 준비됨 · 컨테이너 시작 실패",
            "ENVIRONMENT_BLOCKED_PORT_MAPPING": "이미지는 준비됨 · 로컬 포트 연결 차단",
            "INITIAL_HTTP_FAILED": "컨테이너 시작 완료 · 최초 HTTP 시험 실패",
            "RESTART_HTTP_FAILED": "최초 HTTP 통과 · 재시작 시험 실패",
            "CLEANUP_FAILED": "시험 자원 정리 확인 필요",
            "PASS": "로컬 실행·HTTP·재시작 시험 완료",
            "CANCELLED": "작업 취소됨 · 아래 단계별 결과를 확인하세요",
        }.get(workflow["outcome"], "작업 중" if status == "RUNNING" else "단계별 실행 결과")
        st.write(title)
        if workflow["outcome"] != "NOT_RUN":
            st.caption(f'결과 분류: {workflow["outcome"]}')
    else:
        st.write({
            "RUNNING": "작업 중",
            "DONE": "작업 종료",
            "FAILED": "실패 · 원인 확인 필요",
            "CANCELLED": "취소됨",
            "IDLE": "아직 시작하지 않았습니다",
        }.get(status, status))
    completion = describe_completion(state)
    if completion:
        stopped = status in {"FAILED", "CANCELLED", "BLOCKED"}
        result = state.get("result")
        check = result.get("check") if isinstance(result, dict) else None
        cleaned_after_stop = stopped and isinstance(check, dict) and check.get("cleanup_status") == "CLEANED"
        suffix = " · 중단됨" if stopped else " · 취소 처리 중" if state.get("cancel_requested_at") else ""
        st.subheader(f'단계 진행률 {completion["percent"]}%{suffix}')
        st.progress(
            completion["percent"],
            text=f'{completion["total"]}단계 중 {completion["completed"]}단계 완료',
        )
        st.caption("순서대로 완료한 단계 기준입니다. 소요 시간·다운로드 비율은 아닙니다.")
        active_index = (
            completion["total"] - 1 if state.get("stage") == "시험 자원 정리"
            else completion["completed"]
        )
        if workflow:
            st.caption("진행률은 전체 작업 구간 기준이며, 세부 검사 결과는 아래에 따로 표시합니다.")
        else:
            for index, (column, label) in enumerate(zip(st.columns(completion["total"]),
                                                        completion["labels"], strict=True)):
                if index < completion["completed"]:
                    marker = "✓ 완료"
                elif index == completion["total"] - 1 and cleaned_after_stop:
                    marker = "정리 완료 · 작업 중단"
                elif index == completion["completed"] and stopped:
                    marker = "! 중단"
                elif index == active_index and status == "RUNNING":
                    marker = "● 진행 중"
                else:
                    marker = "○ 대기"
                column.caption(f"{index + 1}. {label}")
                column.write(marker)
        if cleaned_after_stop:
            st.caption("중단 이후의 자원 정리는 별도로 표시합니다. 앞선 시험이 통과한 것은 아닙니다.")
    if workflow:
        markers = {
            "PASS": "✓ 완료", "FAIL": "✗ 실패", "BLOCKED": "✗ 환경 차단",
            "RUNNING": "● 진행 중", "NOT_RUN": "미실행", "UNKNOWN": "확인 기록 없음",
            "CANCELLED": "취소됨",
        }
        table = "| 단계 | 상태 |\n| :--- | :--- |\n" + "\n".join(
            f'| {row["label"]} | {markers[row["status"]]} |' for row in workflow["rows"]
        )
        st.markdown(table)
        if workflow["image_id"]:
            st.caption("실제 생성한 이미지 ID · 로컬 시험 결과와 별도로 보관됩니다")
            st.code(workflow["image_id"], language=None)
    st.info(_literal(state.get("stage", "준비")))
    view = describe_progress(state)
    if state.get("started_at"):
        elapsed, stage_time, output_time = st.columns(3)
        elapsed.metric("전체 경과 시간", format_duration(view["elapsed_seconds"]))
        stage_time.metric("현재 단계 경과", format_duration(view["stage_elapsed_seconds"]))
        process = state.get("process") or {}
        last_output = process.get("last_output_at")
        output_time.metric(
            "현재 명령의 최근 출력",
            f'{format_duration(view["silence_seconds"])} 전' if last_output else "아직 없음",
        )
        if view["phase_label"]:
            st.write(f'도구가 마지막으로 보고한 단계: {view["phase_label"]}')
        if status == "RUNNING" and process.get("running"):
            st.caption("도구 프로세스 실행 중 · 상태와 로그를 1초마다 갱신합니다.")
            remaining = view["timeout_remaining_seconds"]
            if remaining is not None:
                st.caption(
                    f'현재 명령의 제한 시간까지 {format_duration(remaining)} · '
                    "이 값은 완료 예상 시간이 아닙니다. 남은 시간은 도구가 제공하지 않습니다."
                )
        if view["silence_warning"]:
            st.warning(
                f'{format_duration(view["silence_seconds"])} 동안 새 출력이 없습니다. '
                "다운로드·이미지 저장 중일 수 있으며, 출력이 없다는 사실만으로 정지를 판단할 수 없습니다. "
                "계속 기다리거나 현재 작업을 취소할 수 있습니다."
            )
        if view["performance_note"]:
            st.warning(view["performance_note"])
    if state.get("error"):
        st.error(_literal(state["error"]))
        st.caption(_literal(state.get("next_action", "")))
    log = (state.get("logs") if status in {"FAILED", "CANCELLED"} else "") or state.get("live_log") or ""
    if log:
        st.caption("최근 도구 로그 · 비밀값 마스킹 · 최근 16,384자")
        st.code(redact(log, 65_536)[-16_384:], language=None, height=240, wrap_lines=True)
    elif status == "RUNNING":
        st.caption("아직 표시할 도구 로그가 없습니다. 출력이 생기면 여기에 나타납니다.")
    with st.expander("실제 진행 단계와 상세 기록"):
        history = state.get("stage_history") or []
        events = [item["stage"] for item in history] if history else state.get("events", [])
        st.caption("각 단계에 진입한 기록입니다. 성공·실패는 작업 결과에서 확인합니다.")
        for index, event in enumerate(events, 1):
            st.text(f"{index}. {redact(event)}")
        if state.get("logs"):
            st.text(redact(state["logs"], 65_536))


@st.fragment(run_every=1)
def job_panel():
    state = MANAGER.snapshot()
    status = state["status"]
    _progress_details(state)
    if status == "RUNNING":
        cancelling = bool(state.get("cancel_requested_at"))
        if cancelling:
            st.warning("취소 요청 처리 중 · 프로세스 종료와 소유 자원 정리 결과를 기다리세요.")
        if st.button("현재 작업 취소", disabled=cancelling):
            MANAGER.cancel()
            st.info("취소 요청됨 · 소유 자원 정리 결과를 기다리세요.")
        return
    result = MANAGER.result()
    matching_job = state.get("job_id") == st.session_state.get("active_job_id")
    if result and matching_job:
        for key in ("artifact", "check"):
            if key in result:
                st.session_state[key] = result[key]
    check = st.session_state.get("check")
    if check:
        if check.status == "PASS" and status == "DONE" and matching_job:
            st.success("이 주소의 로컬 HTTP 응답 시험 통과")
            st.caption("업무 기능·DB·데이터 보존·AWS 동작은 검증하지 않았습니다.")
        elif check.status == "PASS":
            st.warning("HTTP 응답은 확인했지만 작업이 완료되지 않았습니다. 정리·작업 오류를 확인하세요.")
        else:
            workflow = describe_workflow(state)
            st.error(f"로컬 실행 시험: {workflow['outcome'] if workflow else check.status}")
        st.text(check.message)
        st.caption(f"시험 자원 정리: {check.cleanup_status} · 빌드 이미지는 보관합니다.")
        if check.port_evidence:
            with st.expander("시작·재시작 후 실제 포트 연결 근거"):
                for evidence in check.port_evidence:
                    label = "시작 후" if evidence.phase == "start" else "재시작 후"
                    st.text(f"{label} · {'검증 완료' if evidence.verified else '매핑 미확인'}")
                    st.code(redact(evidence.docker_port_output) or "docker port 출력 없음", language=None)
        with st.expander("마스킹된 실행 로그 · 이미지 식별정보"):
            st.text(check.logs or "추가 로그 없음")
            st.code(check.image_id, language=None)
            st.json(check.conditions.model_dump())
    if st.button("입력 수정 · 재시도"):
        _go(1)
    if status in {"FAILED", "CANCELLED"}:
        st.download_button(
            "실패 진단 기록 받기",
            data=service.diagnostic_report(st.session_state.assessment, check),
            file_name="DIAGNOSTIC_ONLY.json",
            mime="application/json",
        )
    current = (
        check
        and status == "DONE"
        and matching_job
        and service.check_is_current(st.session_state.artifact, check, _conditions())
    )
    if current and st.button("AWS 실행 크기 확인", type="primary"):
        _go(3)


@st.fragment(run_every=1)
def _tar_progress(tar_job):
    state = MANAGER.snapshot()
    if state.get("job_id") != tar_job["job_id"] or state.get("kind") != "save_image":
        return
    st.text(f"이미지 저장: {state['status']} · {state.get('stage', '')}")
    _progress_details(state)
    if state["status"] == "RUNNING":
        if state.get("cancel_requested_at"):
            st.info("이미지 저장 취소 처리 중 · 임시 파일 정리를 기다리세요.")
        elif st.button("이미지 저장 취소"):
            MANAGER.cancel()
            st.info("이미지 저장 취소를 요청했습니다.")
    if state["status"] == "DONE" and Path(tar_job["destination"]).is_file():
        st.text(tar_job["destination"])
        st.caption("저장 완료. 탐색기에서 이 파일을 복사할 수 있습니다.")
        st.json((MANAGER.result() or {}).get("tar", {}))


def choose_size():
    artifact, check = st.session_state.artifact, st.session_state.check
    st.subheader("AWS 실행 크기를 선택하세요")
    st.caption("중형은 이번 시험의 기본 설정입니다. 성능 측정으로 최적이라고 판정한 값은 아닙니다.")
    st.radio(
        "고정 실행 크기",
        list(PRESETS),
        format_func=lambda v: LABELS[v],
        key="preset",
        horizontal=True,
        on_change=_reset_runtime_approval,
    )
    preset = PRESETS[st.session_state.preset]
    st.write(f"{preset.vcpu:g} vCPU · {preset.memory_mib:,} MiB · 실행 수 1개 · 자동 확장 없음")
    st.caption("동시 사용자 수·고가용성·클라우드 성능을 보장하지 않습니다.")
    conditions = _conditions()
    fresh = service.check_is_current(artifact, check, conditions)
    if not fresh:
        st.warning("시험 결과가 오래됨 — 선택한 크기와 실행 조건으로 재시험해야 합니다.")
        st.caption("재시험도 외부 통신 가능한 작업 전용 네트워크와 127.0.0.1 임시 포트만 사용합니다.")
        st.checkbox("같은 이미지의 새 조건 실행 시험에 동의합니다", key="retest_approval")
        if st.button("같은 이미지로 재시험", disabled=not st.session_state.get("retest_approval")):
            try:
                job_id = service.start_retest(
                    st.session_state.assessment, artifact, conditions, runtime_approved=True
                )
                st.session_state.active_job_id = job_id
                st.session_state.pop("check", None)
                st.session_state.pop("bundle", None)
                _go(2)
            except Exception as exc:
                _error(exc)
    else:
        st.success("선택한 조건과 로컬 HTTP 시험 조건이 일치합니다.")
    if st.button("이전 · 시험 결과"):
        _go(2)
    if st.button("결과물 준비로", type="primary", disabled=not fresh):
        _go(4)


def export_results():
    st.subheader("시험한 이미지의 AWS 설계 파일")
    st.info("Terraform 파일 생성만 수행합니다. 실제 AWS 적용은 미실시입니다.")
    st.caption("ECR 준비 → 사용자가 같은 이미지 업로드 → manifest digest 확인 → 서비스 적용")
    region = st.text_input("AWS 리전", value="ap-northeast-2", on_change=_clear_bundle)
    existing_role = st.checkbox(
        "학교에서 승인한 기존 execution role 사용 (생성하지 않음)", on_change=_clear_bundle
    )
    mode = st.selectbox(
        "공개 방식",
        ["https_existing_certificate", "http_demo"],
        format_func=lambda x: (
            "HTTPS · 기존 ACM 인증서와 도메인 필요" if x.startswith("https") else "HTTP · 학교 실습용"
        ),
        on_change=_clear_bundle,
    )
    cidrs = st.text_input(
        "접속을 허용할 공개 IPv4 CIDR (쉼표 구분)",
        placeholder="사용자의 실제 공개 IP/32",
        on_change=_clear_bundle,
    )
    if mode == "http_demo":
        st.warning(
            "HTTP 실습용 / 실서비스 보안 미구현. 로그인·실제 데이터·API 비밀키 전송에 사용하지 마세요."
        )
    st.caption(
        "이미지 URI@manifest digest, 두 AZ, 인증서·도메인 또는 기존 execution role 등은 "
        "생성 안내서에 따라 별도로 입력합니다."
    )
    if st.button("Terraform · 사양표 · 안내서 생성", type="primary"):
        try:
            conditions = _conditions()
            label = st.session_state.artifact.project_label[:24].strip("-")
            if not label or not label[0].isalpha():
                label = "app-" + label[:20]
            spec = DeploymentSpec(
                project_label=label,
                preset=conditions.preset,
                container_port=conditions.container_port,
                health_path=conditions.health_path,
                environment=conditions.environment,
                region=region,
                create_execution_role=not existing_role,
                exposure_mode=mode,
                allowed_cidrs=[c.strip() for c in cidrs.split(",") if c.strip()],
            )
            st.session_state.bundle = service.export_current(
                st.session_state.assessment,
                st.session_state.artifact,
                st.session_state.check,
                conditions,
                spec,
            )
            st.session_state.pop("tar_job", None)
        except Exception as exc:
            _error(exc)
    bundle = st.session_state.get("bundle")
    if bundle:
        st.success("AWS 설계 파일 준비 완료 — 실제 AWS 미검증")
        st.text(str(bundle.directory))
        st.download_button(
            "Terraform · 안내서 ZIP 받기",
            data=bundle.zip_path.read_bytes(),
            file_name=bundle.zip_path.name,
            mime="application/zip",
        )
        spec_file = bundle.directory / "AWS_SPEC.md"
        if spec_file.exists():
            st.download_button("AWS 사양표 받기", data=spec_file.read_bytes(), file_name="AWS_SPEC.md")
        with st.expander("내보내기 검증 상태와 남은 입력"):
            st.json(
                {
                    "이미지": bundle.image_status,
                    "로컬 시험": bundle.local_status,
                    "템플릿": bundle.template_status,
                    "이 묶음의 Terraform CLI": bundle.terraform_cli_status,
                    "AWS": bundle.aws_status,
                    "남은 입력": bundle.external_inputs,
                }
            )
        st.warning(
            "이미지 tar에는 앱 코드·설정이 포함될 수 있습니다. "
            "큰 파일은 디스크에 저장하며 메모리 다운로드하지 않습니다."
        )
        if st.button("실제 이미지 tar 저장"):
            try:
                destination = bundle.directory / "image" / "app-image.tar"
                job_id = service.start_tar(st.session_state.artifact, destination)
                st.session_state.tar_job = {"job_id": job_id, "destination": str(destination)}
            except Exception as exc:
                _error(exc)
        if st.session_state.get("tar_job"):
            _tar_progress(st.session_state.tar_job)
    if st.button("이전 · 실행 크기"):
        _go(3)


def main():
    st.set_page_config(page_title="AWS App Packager", page_icon="📦", layout="centered")
    # Keep input values when their widgets are absent on another wizard step.
    for key in ("source_path", "app_port", "health_path", "environment_json", "preset"):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    st.title("내 앱의 AWS 실행 준비")
    st.write(
        "앱을 실행 가능한 컨테이너로 만들고 AWS 환경 설계 파일을 준비합니다. "
        "실제 AWS 배포와 비용 검토는 사용자가 별도로 진행합니다."
    )
    st.session_state.setdefault("step", 0)
    st.caption("AWS App Packager v0.1 · 로컬 단일 앱 · Linux / amd64")
    step = st.session_state.step
    state = MANAGER.snapshot()
    if state["status"] == "RUNNING" and "assessment" not in st.session_state:
        context = MANAGER.context()
        if context.get("assessment") and context.get("conditions"):
            st.session_state.assessment = context["assessment"]
            conditions = context["conditions"]
            st.session_state.app_port = conditions.container_port
            st.session_state.health_path = conditions.health_path
            st.session_state.preset = conditions.preset
            st.session_state.environment_json = json.dumps(conditions.environment)
            st.session_state.active_job_id = state.get("job_id")
            if context.get("artifact"):
                st.session_state.artifact = context["artifact"]
            st.session_state.step = step = 2
    if state["status"] == "RUNNING" and step != 2 and state.get("kind") != "save_image":
        st.warning("다른 화면에서 시작한 작업이 진행 중입니다.")
        if st.button("진행 중인 작업 보기"):
            _go(2)
        return
    if step > 0 and "assessment" not in st.session_state:
        _go(0)
    if step > 2 and not all(key in st.session_state for key in ("artifact", "check")):
        _go(2)
    st.caption(f"화면 단계 {step + 1}/5 · {STEPS[step][2:]}")
    try:
        [select_app, prepare, job_panel, choose_size, export_results][step]()
    except Exception as exc:
        _error(exc)
    with st.sidebar:
        st.subheader("지원 범위")
        st.write("Java 21 · Spring Boot · Maven 단일 모듈\n\n기존 Dockerfile · 단일 HTTP 앱")
        st.caption("DB·영구 저장소 등 외부 실행 환경은 별도 준비가 필요합니다.")
        st.caption("작업 복사본과 이미지는 보관됩니다. 정리 절차는 README를 확인하세요.")
        st.caption(f"결과물: {EXPORT_DIR.name}/")
