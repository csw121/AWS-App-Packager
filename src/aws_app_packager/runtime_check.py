"""Bounded loopback HTTP checks and selective, durable local resource cleanup."""
import http.client
import json
import os
import re
import socket
import stat
import threading
import time
import uuid
from pathlib import Path

from .builders.base import BuildError, assert_same_image
from .config import WORK_DIR
from .models import ImageArtifact, PortMappingEvidence, RuntimeCheck, RuntimeConditions, fingerprint, utc_now
from .preflight import EnvironmentBlocked, docker_arguments, require_local_docker, tool_environment
from .presets import PRESETS
from .process_runner import ProcessRunner
from .redaction import redact

OWNER_LABEL = "io.aws-app-packager.owner"
JOB_LABEL = "io.aws-app-packager.job"
MAX_BODY_BYTES = 65536
MAX_ATTEMPTS = 90


def _resource_path(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("잘못된 작업 식별자입니다.")
    path = WORK_DIR / "jobs" / job_id / "runtime-resources.json"
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if item.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("정리 기록 경로의 링크/junction은 사용할 수 없습니다.")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_record(path: Path, record: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2))
    os.replace(temporary, path)


def _command(runner, state, *args, cancel=None, timeout=20):
    return runner.run(docker_arguments(state, *args), env=tool_environment(state),
                      timeout=timeout, cancel=cancel)


def _identity(runner, state, kind, reference):
    labels = ".Labels" if kind == "network" else ".Config.Labels"
    template = ('{"id":{{json .Id}},"labels":{'
                f'"{OWNER_LABEL}":' + '{{json (index ' + labels + f' "{OWNER_LABEL}")' + '}},'
                f'"{JOB_LABEL}":' + '{{json (index ' + labels + f' "{JOB_LABEL}")' + '}}}}')
    response = _command(runner, state, kind, "inspect", "--format", template, reference)
    if response.returncode:
        if any(word in response.output.lower() for word in ("no such", "not found")):
            return None
        raise RuntimeError("Docker 자원 존재 여부를 확인하지 못했습니다. 정리 확인이 필요합니다.")
    return json.loads(response.output.strip())


def cleanup_owned(job_id: str, *, runner=None, state=None) -> str:
    """No list-wide stop, prune, image removal, or unverified resource deletion."""
    runner = runner or ProcessRunner()
    try:
        path = _resource_path(job_id)
        if not path.exists():
            return "NOT_NEEDED"
        if path.stat().st_size > 32768:
            return "NEEDS_ATTENTION"
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            return "NEEDS_ATTENTION"
        if record.get("job_id") != job_id or record.get("owner") != "v0.1":
            return "NEEDS_ATTENTION"
        if not isinstance(record.get("cleaned", False), bool):
            return "NEEDS_ATTENTION"
        if record.get("cleaned") is True:
            return "CLEANED"
        state = state or require_local_docker(runner)
        # Persisted IDs are used only after rechecking the exact ownership labels.
        for kind in ("container", "network"):
            reference = record.get(f"{kind}_id")
            if reference is not None and not re.fullmatch(r"[a-f0-9]{64}", reference):
                return "NEEDS_ATTENTION"
            # Crash between create and record: recover the deterministic name, then
            # verify actual returned ID + both labels before deleting that ID.
            reference = reference or f"aap-{kind}-{job_id}"
            identity = _identity(runner, state, kind, reference)
            if identity is None:
                continue
            labels = identity.get("labels") or {}
            actual_id = identity.get("id", "")
            if (not re.fullmatch(r"[a-f0-9]{64}", actual_id)
                    or labels.get(OWNER_LABEL) != "v0.1" or labels.get(JOB_LABEL) != job_id
                    or (record.get(f"{kind}_id") and actual_id != record[f"{kind}_id"])):
                return "NEEDS_ATTENTION"
            record[f"{kind}_id"] = actual_id
            _write_record(path, record)
            args = [kind, "rm"] + (["--force"] if kind == "container" else []) + [actual_id]
            if _command(runner, state, *args).returncode:
                return "NEEDS_ATTENTION"
            if _identity(runner, state, kind, actual_id) is not None:
                return "NEEDS_ATTENTION"
        record["cleaned"] = True
        _write_record(path, record)
        return "CLEANED"
    except (OSError, ValueError, TypeError, RuntimeError):
        return "NEEDS_ATTENTION"


def runtime_arguments(artifact: ImageArtifact, conditions: RuntimeConditions,
                      state: dict, network_id: str, *, execution_job_id: str | None = None) -> list[str]:
    execution_job_id = execution_job_id or artifact.job_id
    if not re.fullmatch(r"[a-f0-9]{32}", execution_job_id):
        raise ValueError("잘못된 실행 작업 식별자입니다.")
    preset = PRESETS[conditions.preset]
    args = docker_arguments(
        state, "container", "create", "--name", f"aap-container-{execution_job_id}",
        "--label", f"{OWNER_LABEL}=v0.1", "--label", f"{JOB_LABEL}={execution_job_id}",
        "--network", network_id, "--publish", f"127.0.0.1::{conditions.container_port}",
        "--cpus", str(preset.vcpu), "--memory", f"{preset.memory_mib}m",
        "--memory-swap", f"{preset.memory_mib}m", "--pids-limit", "256",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
        "--log-driver", "json-file", "--log-opt", "max-size=1m", "--log-opt", "max-file=1",
        "--restart", "no", "--no-healthcheck", "--platform", "linux/amd64", "--pull", "never",
    )
    for name, value in sorted(conditions.environment.items()):
        args.extend(["--env", f"{name}={value}"])
    args.append(artifact.image_id)
    return args


def probe_http(port: int, conditions: RuntimeConditions, *, timeout=2.0) -> tuple[bool, int | None, str]:
    """http.client never inherits proxies or follows redirects. Never return body."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    timer = None
    try:
        deadline = time.monotonic() + timeout
        connection.request("GET", conditions.health_path,
                           headers={"User-Agent": "AWS-App-Packager/0.1", "Connection": "close"})
        transport = connection.sock

        def stop_response():
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(max(0.01, deadline - time.monotonic()), stop_response)
        timer.daemon = True
        timer.start()
        response = connection.getresponse()
        size = response.getheader("Content-Length")
        if size and (not size.isdecimal() or int(size) > MAX_BODY_BYTES):
            return False, response.status, "HTTP 응답 크기가 허용 범위를 넘었습니다."
        expected_bytes = int(size) if size else None
        # read1 prevents a slow stream from resetting the timeout indefinitely.
        chunks = []
        received = 0
        while received <= MAX_BODY_BYTES:
            # read1 closes a Connection: close response when its declared body
            # is complete. Never touch that socket again after successful EOF.
            if response.isclosed() or (expected_bytes is not None and received == expected_bytes):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, response.status, "HTTP 응답 읽기 제한 시간이 지났습니다."
            transport.settimeout(remaining)
            chunk = response.read1(min(8192, MAX_BODY_BYTES + 1 - received))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
        body = b"".join(chunks)
        if len(body) > MAX_BODY_BYTES:
            return False, response.status, "HTTP 응답 크기가 허용 범위를 넘었습니다."
        if expected_bytes is not None and len(body) != expected_bytes:
            return False, response.status, "HTTP 응답 본문이 지정된 길이만큼 도착하지 않았습니다."
        if response.status != conditions.expected_status:
            return False, response.status, f"지정 경로의 HTTP 상태가 {response.status}입니다."
        if conditions.expected_marker and conditions.expected_marker.encode() not in body:
            return False, response.status, "샘플의 예상 건강 확인 응답이 아닙니다."
        return True, response.status, "지정 경로의 HTTP 응답 시험을 통과했습니다."
    except (OSError, http.client.HTTPException):
        return False, None, "루프백 HTTP 응답을 기다리는 중입니다."
    finally:
        if timer:
            timer.cancel()
        connection.close()


def _http_until_ready(runner, state, container_id, port, conditions, cancel, deadline):
    status = None
    message = "지정 경로의 HTTP 응답을 확인하지 못했습니다."
    for attempt in range(MAX_ATTEMPTS):
        if cancel and cancel.is_set():
            return False, status, "CANCELLED"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if attempt % 3 == 0:
            running = _command(runner, state, "container", "inspect", "--format",
                               '{{json .State.Running}}', container_id)
            if running.returncode or running.output.strip() != "true":
                return False, status, "시험 컨테이너가 종료되었습니다. 시작 로그를 확인하세요."
        passed, status, message = probe_http(port, conditions, timeout=min(2, remaining))
        if passed:
            return True, status, message
        if status is not None:
            # A concrete bad response is actionable; don't repeatedly hit an app.
            return False, status, message
        if cancel:
            cancel.wait(min(1, max(0, deadline - time.monotonic())))
        else:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    return False, status, message + " 시험 제한 시간이 지났습니다."


def _verify_port_mapping(runner, state, container_id, conditions, result, phase):
    """Inspect actual bindings, then independently confirm Docker's port report."""
    result.failure_layer = "port_mapping"
    key = f"{conditions.container_port}/tcp"
    inspected = _command(runner, state, "container", "inspect", "--format",
                         '{{json .NetworkSettings.Ports}}', container_id)
    reported = _command(runner, state, "container", "port", container_id, key)
    evidence = PortMappingEvidence(
        phase=phase, inspect_output=redact(inspected.output, 16384),
        inspect_returncode=inspected.returncode,
        docker_port_output=redact(reported.output, 4096), docker_port_returncode=reported.returncode,
    )
    result.port_evidence.append(evidence)
    if inspected.returncode:
        raise EnvironmentBlocked("Docker inspect에서 실제 호스트 포트 매핑을 확인하지 못했습니다.")
    try:
        ports = json.loads(inspected.output)
    except ValueError as exc:
        raise EnvironmentBlocked("Docker inspect의 포트 매핑 응답을 읽을 수 없습니다.") from exc
    if not isinstance(ports, dict):
        raise EnvironmentBlocked("Docker inspect의 포트 매핑 형식이 올바르지 않습니다.")
    if any(bindings for port, bindings in ports.items() if port != key):
        raise EnvironmentBlocked("지정한 시험 포트 외 추가 호스트 포트 매핑이 있어 시험을 중단했습니다.")
    bindings = ports.get(key)
    if not bindings:
        raise EnvironmentBlocked(
            "Docker inspect에 실제 호스트 포트 매핑이 없습니다. HTTP 요청 전 환경 계층에서 차단됐습니다."
        )
    if (not isinstance(bindings, list) or len(bindings) != 1 or not isinstance(bindings[0], dict)
            or bindings[0].get("HostIp") != "127.0.0.1"):
        raise EnvironmentBlocked("시험 포트가 127.0.0.1 한 곳에만 공개되지 않아 시험을 중단했습니다.")
    port_text = bindings[0].get("HostPort")
    if not port_text:
        raise EnvironmentBlocked(
            "Docker가 호스트 포트를 할당하지 않았습니다. HTTP 시험을 시작하지 않았습니다."
        )
    if not isinstance(port_text, str) or not port_text.isdecimal() or not 1 <= int(port_text) <= 65535:
        raise EnvironmentBlocked("Docker가 잘못된 시험 포트를 반환했습니다.")
    host_port = int(port_text)
    evidence.host_port = host_port
    if reported.returncode or not reported.output.strip():
        raise EnvironmentBlocked("docker port에서 실제 루프백 포트 매핑을 확인하지 못했습니다.")
    if reported.output.strip().splitlines() != [f"127.0.0.1:{host_port}"]:
        raise EnvironmentBlocked("docker inspect와 docker port의 루프백 매핑이 일치하지 않습니다.")
    # Docker may allocate a new ephemeral host port on restart. Both mappings
    # stay in evidence; use the newly verified loopback port for the same path.
    evidence.verified = True
    result.host_port = host_port
    return host_port


def _outcome(result: RuntimeCheck) -> str:
    if result.status in {"PASS", "CANCELLED", "STALE", "NOT_RUN"}:
        return result.status
    if result.failure_layer == "port_mapping":
        return "ENVIRONMENT_BLOCKED_PORT_MAPPING"
    if result.failure_layer == "cleanup" or result.cleanup_status == "NEEDS_ATTENTION":
        return "CLEANUP_FAILED"
    if result.failure_layer == "initial_http":
        return "INITIAL_HTTP_FAILED"
    if result.failure_layer in {"container_restart", "restart_http"}:
        return "RESTART_HTTP_FAILED"
    if result.failure_layer in {"network_creation", "container_creation", "container_start", "mount_safety"}:
        return "CONTAINER_START_FAILED"
    return "BLOCKED"


def run_check(artifact: ImageArtifact, conditions: RuntimeConditions, cancel=None,
              on_stage=None, *, runtime_approved=False, runner=None,
              execution_job_id: str | None = None) -> RuntimeCheck:
    execution_job_id = execution_job_id or artifact.job_id
    runner = runner or ProcessRunner()
    result = RuntimeCheck(job_id=artifact.job_id, execution_job_id=execution_job_id,
                          image_id=artifact.image_id,
                          source_fingerprint=artifact.source_fingerprint,
                          conditions=conditions, conditions_fingerprint=fingerprint(conditions),
                          status="BLOCKED", outcome="BLOCKED")
    if not runtime_approved:
        result.message = "이 이미지와 실행조건에 대한 로컬 실행 승인이 필요합니다."
        return result
    state = None
    container_id = None
    record_path = None
    try:
        if on_stage:
            on_stage("시험 이미지·실행 조건 확인")
        if conditions.network_mode == "approved_project_bridge":
            from .runtime_approval import verify_runtime_approval

            result.failure_layer = "runtime_authorization"
            try:
                verify_runtime_approval(artifact, conditions, execution_job_id=execution_job_id)
            except (ValueError, OSError, EnvironmentBlocked) as exc:
                result.message = redact(str(exc))
                return result
            result.environment = (
                "local Docker, Linux/amd64; explicitly approved project normal bridge (outbound allowed); "
                "127.0.0.1-only published HTTP"
            )
        if conditions.network_mode == "authored_sample_bridge":
            from .trusted_samples import verify_trusted_sample

            result.failure_layer = "sample_authorization"
            try:
                verify_trusted_sample(artifact, conditions)
            except (ValueError, OSError, EnvironmentBlocked) as exc:
                result.message = redact(str(exc))
                return result
            result.environment = (
                "local Docker, Linux/amd64; authored-sample normal bridge (outbound allowed); "
                "127.0.0.1-only published HTTP"
            )
        result.failure_layer = "docker_preflight"
        state = require_local_docker(runner)
        result.failure_layer = "image_identity"
        inspected = assert_same_image(artifact, runner, state)
        result.image_verified = True
        result.failure_layer = "runtime_conditions"
        user = (inspected["user"] or "").split(":", 1)[0]
        if not user.isdecimal() or int(user) == 0:
            result.message = (
                "이미지 USER의 non-root UID를 확인할 수 없습니다. "
                "0이 아닌 숫자 USER로 준비하세요."
            )
            return result
        if inspected["volumes"] not in ([], ["/tmp"]):
            result.message = (
                "이미지가 /tmp 외 VOLUME을 요구합니다. "
                "이 버전은 명시적 tmpfs로 대체하는 /tmp 임시 쓰기만 허용합니다."
            )
            return result
        if cancel and cancel.is_set():
            result.status = "CANCELLED"
            return result
        cleanup = cleanup_owned(execution_job_id, runner=runner, state=state)
        if cleanup == "NEEDS_ATTENTION":
            result.message = "이전 시험 자원의 소유권 또는 정리를 확인해야 합니다."
            result.cleanup_status = cleanup
            return result
        if on_stage:
            on_stage("실행 시작")
        result.failure_layer = "network_creation"
        record_path = _resource_path(execution_job_id)
        record = {"owner": "v0.1", "job_id": execution_job_id, "build_job_id": artifact.job_id,
                  "image_id": artifact.image_id,
                  "cleaned": False, "network_id": None, "container_id": None}
        _write_record(record_path, record)
        network_args = ["network", "create", "--driver", "bridge"]
        if conditions.network_mode == "internal":
            network_args.append("--internal")
        network_args.extend(["--label", f"{OWNER_LABEL}=v0.1", "--label", f"{JOB_LABEL}={execution_job_id}",
                             f"aap-network-{execution_job_id}"])
        created = _command(runner, state, *network_args, cancel=cancel)
        network_id = created.output.strip()
        if created.returncode or not re.fullmatch(r"[a-f0-9]{64}", network_id):
            raise BuildError("전용 시험 네트워크를 만들 수 없습니다.", logs=created.output)
        record["network_id"] = network_id
        _write_record(record_path, record)
        checked_network = _command(runner, state, "network", "inspect", "--format",
                                   '{"driver":{{json .Driver}},"internal":{{json .Internal}}}', network_id)
        network_config = json.loads(checked_network.output) if checked_network.returncode == 0 else {}
        if not isinstance(network_config, dict):
            raise BuildError("Docker가 시험 네트워크 확인 정보를 올바르게 반환하지 않았습니다.")
        result.network_driver = network_config.get("driver")
        result.network_internal = network_config.get("internal")
        if (result.network_driver != "bridge"
                or network_config.get("internal") is not (conditions.network_mode == "internal")):
            raise BuildError("시험 네트워크의 bridge 드라이버·내부망 조건이 요청과 다릅니다.")
        result.failure_layer = "container_creation"
        created = runner.run(runtime_arguments(artifact, conditions, state, network_id,
                                               execution_job_id=execution_job_id),
                             env=tool_environment(state), timeout=30, cancel=cancel)
        container_id = created.output.strip()
        if created.returncode or not re.fullmatch(r"[a-f0-9]{64}", container_id):
            container_id = None
            raise BuildError("제한된 조건으로 컨테이너를 만들지 못했습니다.", logs=created.output)
        record["container_id"] = container_id
        _write_record(record_path, record)
        result.failure_layer = "container_start"
        started = _command(runner, state, "container", "start", container_id, cancel=cancel)
        if started.returncode:
            raise BuildError("컨테이너 시작에 실패했습니다.", logs=started.output)
        result.container_started = True
        result.failure_layer = "mount_safety"
        mounted = _command(
            runner, state, "container", "inspect", "--format",
            '{"mounts":{{json .Mounts}},"tmpfs":{{json .HostConfig.Tmpfs}}}', container_id
        )
        mount_config = json.loads(mounted.output) if mounted.returncode == 0 else {}
        if not isinstance(mount_config, dict):
            raise BuildError("Docker가 시험 저장소 확인 정보를 올바르게 반환하지 않았습니다.")
        mounts = mount_config.get("mounts")
        # Engine 29 reports CLI --tmpfs in HostConfig.Tmpfs, not .Mounts.
        # Other mount entries (including any anonymous image volume) are forbidden.
        valid_mounts = isinstance(mounts, list) and (
            not mounts or (len(mounts) == 1 and
                           (mounts[0].get("Type"), mounts[0].get("Destination")) == ("tmpfs", "/tmp"))
        )
        if not valid_mounts or mount_config.get("tmpfs") != {
            "/tmp": "rw,noexec,nosuid,size=64m,mode=1777"
        }:
            raise BuildError("시험 컨테이너에 /tmp tmpfs 외 암묵적 저장소가 생겨 시험을 중단했습니다.")
        if on_stage:
            on_stage("실제 루프백 포트 매핑 확인")
        host_port = _verify_port_mapping(runner, state, container_id, conditions, result, "start")
        if on_stage:
            on_stage("웹 응답 확인")
        deadline = time.monotonic() + conditions.timeout_seconds
        result.failure_layer = "initial_http"
        passed, status, message = _http_until_ready(runner, state, container_id, host_port,
                                                   conditions, cancel, deadline)
        result.http_status = status
        result.initial_http_status = status
        result.initial_http_passed = passed
        result.message = message
        if message == "CANCELLED":
            result.status = "CANCELLED"
        elif not passed:
            result.status = "FAIL"
        elif conditions.restart:
            if on_stage:
                on_stage("컨테이너 재시작")
            result.failure_layer = "container_restart"
            restarted = _command(runner, state, "container", "restart", "--time", "5", container_id,
                                 cancel=cancel)
            if restarted.returncode:
                raise BuildError("컨테이너 재시작에 실패했습니다.", logs=restarted.output)
            result.restart_performed = True
            if on_stage:
                on_stage("재시작 후 루프백 매핑 확인")
            host_port = _verify_port_mapping(runner, state, container_id, conditions, result, "restart")
            if on_stage:
                on_stage("재시작 후 웹 응답 확인")
            result.failure_layer = "restart_http"
            deadline = time.monotonic() + conditions.timeout_seconds
            passed, status, message = _http_until_ready(runner, state, container_id, host_port,
                                                       conditions, cancel, deadline)
            result.http_status = status
            result.restart_http_status = status
            result.message = message
            result.restart_passed = passed
            result.status = "CANCELLED" if message == "CANCELLED" else ("PASS" if passed else "FAIL")
        else:
            result.status = "PASS"
        if result.status == "PASS":
            result.failure_layer = None
            if on_stage:
                on_stage("웹 응답 시험 통과")
    except EnvironmentBlocked as exc:
        result.status = "ENVIRONMENT_BLOCKED" if result.failure_layer == "port_mapping" else "BLOCKED"
        result.message = redact(str(exc))
    except (BuildError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        result.status = "CANCELLED" if cancel and cancel.is_set() else "FAIL"
        result.message = redact(str(exc))
        if isinstance(exc, BuildError):
            result.logs = exc.logs
            if exc.status == "STALE":
                result.status = "STALE"
    finally:
        if record_path and state and on_stage:
            try:
                on_stage("시험 자원 정리")
            except Exception:
                # Status storage/display problems must never skip owned cleanup.
                pass
        if container_id and state:
            try:
                logs = _command(runner, state, "container", "logs", "--tail", "100", container_id)
                result.logs = (result.logs + "\n" + logs.output)[-32768:]
            except (RuntimeError, OSError, ValueError):
                result.logs = (result.logs + "\n컨테이너 종료 로그를 읽지 못했습니다.")[-32768:]
        if record_path and state:
            result.cleanup_status = cleanup_owned(execution_job_id, runner=runner, state=state)
            if result.cleanup_status != "CLEANED" and result.status == "PASS":
                result.status = "FAIL"
                result.failure_layer = "cleanup"
                result.message = "HTTP 시험은 통과했으나 시험 자원 정리를 확인하지 못했습니다."
        result.finished_at = utc_now()
        result.outcome = _outcome(result)
        path = _resource_path(execution_job_id).parent / "runtime-check.json"
        _write_record(path, result.model_dump(mode="json"))
    return result
