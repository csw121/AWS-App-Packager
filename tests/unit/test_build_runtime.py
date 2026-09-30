import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_app_packager import preflight, runtime_check
from aws_app_packager.builders import base, dockerfile, paketo_java
from aws_app_packager.config import PAKETO_BUILDER
from aws_app_packager.models import BuildPlan, ImageArtifact, RuntimeConditions
from aws_app_packager.presets import PRESETS
from aws_app_packager.process_runner import ProcessResult

JOB = "a" * 32
IMAGE = "sha256:" + "1" * 64
CID = "2" * 64
NID = "3" * 64
STATE = {"docker_path": str(Path("docker.exe").absolute()), "endpoint": "npipe:////./pipe/docker_engine",
         "docker_ready": True, "pack_ready": True, "pack_path": "pack.exe",
         "docker_version": "test-version", "pack_version": "test-version"}


@pytest.fixture
def artifact(tmp_path):
    return ImageArtifact(job_id=JOB, project_label="fixture", build_method="dockerfile",
                         source_fingerprint="f" * 64, local_tag=f"aws-app-packager/fixture:{JOB}",
                         image_id=IMAGE, size_bytes=500, user="10001",
                         manifest_path=tmp_path / "manifest.json")


@pytest.fixture
def plan(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    return BuildPlan(job_id=JOB, project_label="fixture", source_root=tmp_path / "source",
                     snapshot=snapshot, method="dockerfile", source_fingerprint="f" * 64,
                     approval_fingerprint="f" * 64, manifest_path=tmp_path / "manifest.json",
                     build_approved=True, builder=PAKETO_BUILDER)


@pytest.fixture
def conditions():
    return RuntimeConditions(container_port=8080, health_path="/health", expected_marker="healthy")


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.resources = {}
        self.image_id = IMAGE
        self.user = "10001"
        self.volumes = []
        self.wrong_owner = False
        self.port_bindings = {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "45678"}]}
        self.port_output = "127.0.0.1:45678\n"
        self.port_returncode = 0
        self.network_internal = True
        self.job_id = JOB
        self.mounts = []

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        command = args[args.index("--config") + 2:] if "--config" in args else args[1:]
        if command[:2] == ["image", "inspect"]:
            return ProcessResult(0, json.dumps({"id": self.image_id, "os": "linux", "architecture": "amd64",
                                               "size": 500, "user": self.user, "volumes": self.volumes,
                                               "rootfs_layers": ["sha256:" + "f" * 64], "onbuild_count": 0}))
        if command[:2] == ["network", "create"]:
            self.resources["network"] = NID
            self.network_internal = "--internal" in command
            self.job_id = next(value.split("=", 1)[1] for value in command
                               if value.startswith(runtime_check.JOB_LABEL + "="))
            return ProcessResult(0, NID)
        if command[:2] == ["container", "create"]:
            self.resources["container"] = CID
            return ProcessResult(0, CID)
        if command[:2] == ["container", "port"]:
            return ProcessResult(self.port_returncode, self.port_output)
        if command[:2] in (["network", "inspect"], ["container", "inspect"]):
            if ".Driver" in command[3]:
                return ProcessResult(0, json.dumps({"driver": "bridge", "internal": self.network_internal}))
            if ".State.Running" in command[3]:
                return ProcessResult(0, "true")
            if ".NetworkSettings.Ports" in command[3]:
                return ProcessResult(0, json.dumps(self.port_bindings))
            if ".Mounts" in command[3]:
                return ProcessResult(0, json.dumps({
                    "mounts": self.mounts, "tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m,mode=1777"},
                }))
            if command[0] not in self.resources:
                return ProcessResult(1, "Error: No such object")
            return ProcessResult(0, json.dumps({"id": self.resources[command[0]], "labels": {
                runtime_check.OWNER_LABEL: "someone-else" if self.wrong_owner else "v0.1",
                runtime_check.JOB_LABEL: self.job_id}}))
        if command[1:2] == ["rm"]:
            self.resources.pop(command[0], None)
        return ProcessResult(0, "")


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    runner = FakeDocker()
    monkeypatch.setattr(runtime_check, "WORK_DIR", tmp_path)
    monkeypatch.setattr(runtime_check, "require_local_docker", lambda _: STATE)
    monkeypatch.setattr(base, "require_local_docker", lambda _: STATE)
    monkeypatch.setattr(preflight, "WORK_DIR", tmp_path)
    return runner


@pytest.mark.parametrize("endpoint", ["ssh://example", "tcp://127.0.0.1:2375",
                                      "npipe:////server/pipe/docker_engine", "http://evil"])
def test_remote_endpoint_rejected(endpoint):
    assert not preflight.local_endpoint(endpoint)


def test_local_named_pipe_and_socket():
    assert preflight.local_endpoint("npipe:////./pipe/dockerDesktopLinuxEngine")
    assert preflight.local_endpoint("unix:///var/run/docker.sock")


def test_paketo_descriptor_and_explicit_values(plan, tmp_path):
    plan.method = "paketo_java"
    descriptor = tmp_path / "controlled.toml"
    args = paketo_java.build_arguments(plan, STATE, "owned-tag", descriptor)
    assert args[args.index("--descriptor") + 1] == str(descriptor)
    assert "BP_JVM_VERSION=21" in args
    assert "--publish" not in args
    assert "--trust-builder" in args
    assert "--volume" not in args
    plan.builder = "attacker/custom:latest"
    with pytest.raises(ValueError):
        paketo_java.build_arguments(plan, STATE, "owned-tag", descriptor)


def test_docker_build_arguments_use_single_approved_context(plan):
    args = dockerfile.build_arguments(plan, STATE, "owned-tag")
    assert args[-1] == str(plan.snapshot)
    assert "linux/amd64" in args
    assert not set(args) & {"--push", "--ssh", "--secret", "--allow", "--network"}


@pytest.mark.parametrize("preset", ["small", "medium", "large"])
def test_resources_reuse_common_presets(artifact, conditions, preset):
    conditions.preset = preset
    args = runtime_check.runtime_arguments(artifact, conditions, STATE, NID)
    assert args[args.index("--cpus") + 1] == str(PRESETS[preset].vcpu)
    assert args[args.index("--memory") + 1] == f"{PRESETS[preset].memory_mib}m"
    assert args[args.index("--publish") + 1] == "127.0.0.1::8080"
    assert args[-1] == IMAGE
    assert args[args.index("--pull") + 1] == "never"
    assert "--read-only" in args and "--cap-drop" in args
    assert not set(args) & {"--privileged", "--volume", "--mount", "--device"}


def test_no_runtime_approval_runs_no_command(artifact, conditions):
    runner = Mock()
    result = runtime_check.run_check(artifact, conditions, runner=runner)
    assert result.status == "BLOCKED"
    runner.run.assert_not_called()


@pytest.mark.parametrize("restart", [True, False])
def test_runtime_success_restarts_and_selectively_cleans(
    fake_runtime, artifact, conditions, monkeypatch, restart,
):
    conditions.restart = restart
    stages = []
    probe = Mock(return_value=(True, 200, "HTTP passed"))
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(
        artifact, conditions, runtime_approved=True, runner=fake_runtime, on_stage=stages.append,
    )
    assert result.status == "PASS" and result.restart_passed is restart
    assert result.outcome == "PASS" and result.initial_http_passed is True
    assert result.image_verified and result.container_started
    assert result.restart_performed is restart
    assert result.initial_http_status == 200
    assert result.restart_http_status == (200 if restart else None)
    assert result.host_port == 45678 and result.failure_layer is None
    assert result.network_driver == "bridge" and result.network_internal is True
    assert [e.phase for e in result.port_evidence] == (["start", "restart"] if restart else ["start"])
    assert all(e.verified and e.docker_port_output == "127.0.0.1:45678\n" for e in result.port_evidence)
    assert probe.call_count == (2 if restart else 1)
    assert stages[-2:] == ["웹 응답 시험 통과", "시험 자원 정리"]
    assert stages.count("웹 응답 시험 통과") == 1
    assert result.cleanup_status == "CLEANED"
    assert not fake_runtime.resources
    calls = [args for args, _ in fake_runtime.calls]
    assert any("--internal" in args for args in calls)
    assert not any("prune" in args for args in calls)


def test_tag_tampering_is_stale_without_run(fake_runtime, artifact, conditions):
    fake_runtime.image_id = "sha256:" + "9" * 64
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "STALE"
    assert not any("create" in args for args, _ in fake_runtime.calls)


@pytest.mark.parametrize("user", ["", "root", "0", "0:0", "00", "app"])
def test_root_images_blocked(fake_runtime, artifact, conditions, user):
    fake_runtime.user = user
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "BLOCKED"
    assert not fake_runtime.resources


def test_image_volumes_blocked(fake_runtime, artifact, conditions):
    fake_runtime.volumes = ["/data"]
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "BLOCKED"


def test_only_tmp_volume_is_explicitly_overridden_by_tmpfs(fake_runtime, artifact, conditions, monkeypatch):
    fake_runtime.volumes = ["/tmp"]
    monkeypatch.setattr(runtime_check, "probe_http", lambda *a, **k: (True, 200, "passed"))
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "PASS" and result.cleanup_status == "CLEANED"
    fake_runtime.mounts.append({"Type": "volume", "Destination": "/data"})
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "FAIL" and result.cleanup_status == "CLEANED"


@pytest.mark.parametrize("http_status,message", [(404, "not found"), (200, "expected marker missing")])
def test_bad_http_is_failure_and_cleaned(
    fake_runtime, artifact, conditions, monkeypatch, http_status, message,
):
    stages = []
    monkeypatch.setattr(runtime_check, "probe_http", lambda *a, **k: (False, http_status, message))
    result = runtime_check.run_check(
        artifact, conditions, runtime_approved=True, runner=fake_runtime, on_stage=stages.append,
    )
    assert result.status == "FAIL" and result.http_status == http_status
    assert result.outcome == "INITIAL_HTTP_FAILED" and result.initial_http_passed is False
    assert result.cleanup_status == "CLEANED"
    assert "웹 응답 시험 통과" not in stages
    assert stages[-1] == "시험 자원 정리"


@pytest.mark.parametrize("bindings", [{"8080/tcp": []}, {"8080/tcp": None}, {},
                                      {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]}])
def test_unpublished_internal_loopback_is_blocked_and_cleaned(
    fake_runtime, artifact, conditions, monkeypatch, bindings,
):
    stages = []
    fake_runtime.port_bindings = bindings
    probe = Mock()
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(
        artifact, conditions, runtime_approved=True, runner=fake_runtime, on_stage=stages.append,
    )
    assert result.status == "ENVIRONMENT_BLOCKED" and result.http_status is None
    assert result.outcome == "ENVIRONMENT_BLOCKED_PORT_MAPPING" and result.initial_http_passed is None
    assert result.failure_layer == "port_mapping" and not result.port_evidence[0].verified
    assert result.cleanup_status == "CLEANED" and not fake_runtime.resources
    assert "웹 응답 시험 통과" not in stages
    assert stages[-1] == "시험 자원 정리"
    probe.assert_not_called()


def test_non_loopback_mapping_is_failure_and_cleaned(fake_runtime, artifact, conditions, monkeypatch):
    fake_runtime.port_bindings = {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "45678"}]}
    probe = Mock()
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "ENVIRONMENT_BLOCKED" and result.cleanup_status == "CLEANED"
    assert result.outcome == "ENVIRONMENT_BLOCKED_PORT_MAPPING"
    probe.assert_not_called()


def test_authored_sample_bridge_requires_guard_and_has_no_internal_flag(
    fake_runtime, artifact, conditions, monkeypatch,
):
    from aws_app_packager import trusted_samples

    guard = Mock()
    monkeypatch.setattr(trusted_samples, "verify_trusted_sample", guard)
    monkeypatch.setattr(runtime_check, "probe_http", lambda *a, **k: (True, 200, "passed"))
    conditions.network_mode = "authored_sample_bridge"
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    guard.assert_called_once_with(artifact, conditions)
    network_args = next(args for args, _ in fake_runtime.calls if "create" in args and "network" in args)
    assert "--internal" not in network_args and network_args[network_args.index("--driver") + 1] == "bridge"
    create_args = next(args for args, _ in fake_runtime.calls if "create" in args and "container" in args)
    assert create_args[create_args.index("--publish") + 1] == "127.0.0.1::8080"
    assert result.status == "PASS" and result.network_internal is False
    assert "outbound allowed" in result.environment


def test_authored_sample_guard_failure_does_not_call_docker(fake_runtime, artifact, conditions, monkeypatch):
    from aws_app_packager import trusted_samples

    monkeypatch.setattr(trusted_samples, "verify_trusted_sample",
                        Mock(side_effect=ValueError("not approved")))
    conditions.network_mode = "authored_sample_bridge"
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "BLOCKED" and result.failure_layer == "sample_authorization"
    assert not fake_runtime.calls


def test_approved_project_reuses_normal_bridge_with_execution_owned_cleanup(
    fake_runtime, artifact, conditions, monkeypatch,
):
    from aws_app_packager import runtime_approval

    execution_job = "b" * 32
    guard = Mock()
    monkeypatch.setattr(runtime_approval, "verify_runtime_approval", guard)
    monkeypatch.setattr(runtime_check, "probe_http", lambda *a, **k: (True, 200, "passed"))
    conditions.network_mode = "approved_project_bridge"
    old_record = runtime_check._resource_path(artifact.job_id)
    old_record.write_text('{"previous_build_record": true}', encoding="utf-8")
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime,
                                     execution_job_id=execution_job)
    guard.assert_called_once_with(artifact, conditions, execution_job_id=execution_job)
    assert result.job_id == artifact.job_id and result.execution_job_id == execution_job
    assert result.outcome == "PASS" and result.cleanup_status == "CLEANED"
    assert result.network_internal is False and not fake_runtime.resources
    assert old_record.read_text() == '{"previous_build_record": true}'
    for args, _ in fake_runtime.calls:
        assert "--internal" not in args
        if "create" in args:
            assert f"{runtime_check.JOB_LABEL}={execution_job}" in args
            assert f"{runtime_check.JOB_LABEL}={artifact.job_id}" not in args
    check_path = runtime_check._resource_path(execution_job).parent / "runtime-check.json"
    saved = json.loads(check_path.read_text())
    assert saved["execution_job_id"] == execution_job and saved["initial_http_passed"] is True


@pytest.mark.parametrize("phase", ["create", "start"])
def test_container_failure_has_specific_outcome(fake_runtime, artifact, conditions, monkeypatch, phase):
    original_run = fake_runtime.run

    def run(args, **kwargs):
        if "container" in args and phase in args:
            return ProcessResult(1, "container operation failed")
        return original_run(args, **kwargs)

    fake_runtime.run = run
    probe = Mock()
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    check = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert check.outcome == "CONTAINER_START_FAILED" and check.cleanup_status == "CLEANED"
    assert not check.container_started and not fake_runtime.resources
    probe.assert_not_called()


@pytest.mark.parametrize("output,returncode,expected", [
    ("", 0, "ENVIRONMENT_BLOCKED"), ("No public port", 1, "ENVIRONMENT_BLOCKED"),
    ("0.0.0.0:45678\n", 0, "ENVIRONMENT_BLOCKED"), ("127.0.0.1:45679\n", 0, "ENVIRONMENT_BLOCKED"),
    ("127.0.0.1:45678\n[::]:45678\n", 0, "ENVIRONMENT_BLOCKED"),
])
def test_docker_port_must_independently_confirm_single_inspect_mapping(
    fake_runtime, artifact, conditions, monkeypatch, output, returncode, expected,
):
    fake_runtime.port_output, fake_runtime.port_returncode = output, returncode
    probe = Mock()
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == expected and result.failure_layer == "port_mapping"
    assert result.port_evidence[0].docker_port_output == output
    assert result.port_evidence[0].docker_port_returncode == returncode
    assert result.cleanup_status == "CLEANED" and not result.port_evidence[0].verified
    probe.assert_not_called()


def test_additional_published_port_is_rejected(fake_runtime, artifact, conditions, monkeypatch):
    fake_runtime.port_bindings["9000/tcp"] = [{"HostIp": "0.0.0.0", "HostPort": "45679"}]
    probe = Mock()
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "ENVIRONMENT_BLOCKED" and result.failure_layer == "port_mapping"
    assert result.cleanup_status == "CLEANED"
    probe.assert_not_called()


@pytest.mark.parametrize("output,returncode,expected", [
    ("not-json", 0, "ENVIRONMENT_BLOCKED"), ("[]", 0, "ENVIRONMENT_BLOCKED"),
    ("inspect unavailable", 1, "ENVIRONMENT_BLOCKED"),
])
def test_malformed_or_unavailable_inspect_is_not_an_http_failure(
    fake_runtime, artifact, conditions, monkeypatch, output, returncode, expected,
):
    original_run = fake_runtime.run

    def run(args, **kwargs):
        if any(".NetworkSettings.Ports" in arg for arg in args):
            return ProcessResult(returncode, output)
        return original_run(args, **kwargs)

    fake_runtime.run = run
    probe = Mock()
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == expected and result.failure_layer == "port_mapping"
    assert result.port_evidence[0].inspect_output == output
    assert result.cleanup_status == "CLEANED" and result.http_status is None
    probe.assert_not_called()


def test_wrong_network_mode_stops_before_container_creation(fake_runtime, artifact, conditions, monkeypatch):
    original_run = fake_runtime.run

    def run(args, **kwargs):
        if any(".Driver" in arg for arg in args):
            return ProcessResult(0, json.dumps({"driver": "bridge", "internal": False}))
        return original_run(args, **kwargs)

    fake_runtime.run = run
    probe = Mock()
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "FAIL" and result.failure_layer == "network_creation"
    assert result.cleanup_status == "CLEANED" and not result.container_started
    assert not any("container" in args and "create" in args for args, _ in fake_runtime.calls)
    probe.assert_not_called()


@pytest.mark.parametrize("changed_port,expected", [(None, "ENVIRONMENT_BLOCKED"), (45679, "PASS")])
def test_restart_rechecks_mapping_before_any_second_http(
    fake_runtime, artifact, conditions, monkeypatch, changed_port, expected,
):
    stages = []
    original_run = fake_runtime.run

    def run(args, **kwargs):
        if "restart" in args:
            fake_runtime.port_bindings = {"8080/tcp": (
                [{"HostIp": "127.0.0.1", "HostPort": str(changed_port)}] if changed_port else []
            )}
            fake_runtime.port_output = f"127.0.0.1:{changed_port}\n" if changed_port else ""
        return original_run(args, **kwargs)

    fake_runtime.run = run
    probe = Mock(return_value=(True, 200, "passed"))
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(
        artifact, conditions, runtime_approved=True, runner=fake_runtime, on_stage=stages.append,
    )
    assert result.status == expected
    assert result.initial_http_status == 200 and result.restart_performed
    assert result.cleanup_status == "CLEANED"
    assert ("웹 응답 시험 통과" in stages) is (expected == "PASS")
    assert stages[-1] == "시험 자원 정리"
    if changed_port:
        assert result.failure_layer is None and result.restart_passed
        assert result.restart_http_status == 200 and result.host_port == changed_port
        assert [e.host_port for e in result.port_evidence] == [45678, changed_port]
        assert all(e.verified for e in result.port_evidence)
        assert [call.args[0] for call in probe.call_args_list] == [45678, changed_port]
    else:
        assert result.failure_layer == "port_mapping" and not result.restart_passed
        assert result.restart_http_status is None
        assert [e.verified for e in result.port_evidence] == [True, False]
        assert probe.call_count == 1


def test_restart_http_failure_never_announces_http_completion(
    fake_runtime, artifact, conditions, monkeypatch,
):
    stages = []
    probe = Mock(side_effect=[(True, 200, "passed"), (False, 200, "expected marker missing")])
    monkeypatch.setattr(runtime_check, "probe_http", probe)
    result = runtime_check.run_check(
        artifact, conditions, runtime_approved=True, runner=fake_runtime, on_stage=stages.append,
    )
    assert result.status == "FAIL" and result.failure_layer == "restart_http"
    assert result.restart_performed and not result.restart_passed
    assert result.initial_http_status == result.restart_http_status == 200
    assert result.cleanup_status == "CLEANED" and not fake_runtime.resources
    assert "웹 응답 시험 통과" not in stages
    assert stages[-1] == "시험 자원 정리"


def test_restart_receives_fresh_http_time_budget(fake_runtime, artifact, conditions, monkeypatch):
    monkeypatch.setattr(runtime_check.time, "monotonic", Mock(side_effect=[100.0, 200.0]))
    http = Mock(return_value=(True, 200, "passed"))
    monkeypatch.setattr(runtime_check, "_http_until_ready", http)
    result = runtime_check.run_check(artifact, conditions, runtime_approved=True, runner=fake_runtime)
    assert result.status == "PASS"
    assert [call.args[-1] for call in http.call_args_list] == [190.0, 290.0]


def test_cleanup_must_confirm_resources_absent_before_cleaned(fake_runtime):
    path = runtime_check._resource_path(JOB)
    path.write_text(json.dumps({"owner": "v0.1", "job_id": JOB, "container_id": CID}), encoding="utf-8")
    fake_runtime.resources["container"] = CID
    original_run = fake_runtime.run

    def run(args, **kwargs):
        if "rm" in args:
            return ProcessResult(0, "")
        return original_run(args, **kwargs)

    fake_runtime.run = run
    assert runtime_check.cleanup_owned(JOB, runner=fake_runtime, state=STATE) == "NEEDS_ATTENTION"
    assert fake_runtime.resources["container"] == CID


def test_cleanup_failure_cannot_leave_pass_status(fake_runtime, artifact, conditions, monkeypatch):
    stages = []
    monkeypatch.setattr(runtime_check, "probe_http", lambda *a, **k: (True, 200, "passed"))
    monkeypatch.setattr(runtime_check, "cleanup_owned", Mock(side_effect=["NOT_NEEDED", "NEEDS_ATTENTION"]))
    result = runtime_check.run_check(
        artifact, conditions, runtime_approved=True, runner=fake_runtime, on_stage=stages.append,
    )
    assert result.initial_http_status == result.restart_http_status == 200
    assert result.status == "FAIL" and result.failure_layer == "cleanup"
    assert result.cleanup_status == "NEEDS_ATTENTION"
    assert stages[-2:] == ["웹 응답 시험 통과", "시험 자원 정리"]


def test_cancelled_before_creation(fake_runtime, artifact, conditions):
    cancel = threading.Event()
    cancel.set()
    result = runtime_check.run_check(artifact, conditions, cancel=cancel,
                                     runtime_approved=True, runner=fake_runtime)
    assert result.status == "CANCELLED" and not fake_runtime.resources


def test_cleanup_checks_labels_and_corrupt_records(fake_runtime, tmp_path):
    path = runtime_check._resource_path(JOB)
    path.write_text(json.dumps({"owner": "v0.1", "job_id": JOB, "container_id": CID}), encoding="utf-8")
    fake_runtime.resources["container"] = CID
    fake_runtime.wrong_owner = True
    assert runtime_check.cleanup_owned(JOB, runner=fake_runtime, state=STATE) == "NEEDS_ATTENTION"
    assert fake_runtime.resources["container"] == CID
    path.write_text("invalid-json", encoding="utf-8")
    assert runtime_check.cleanup_owned(JOB, runner=fake_runtime, state=STATE) == "NEEDS_ATTENTION"
    assert runtime_check.cleanup_owned("../../someone") == "NEEDS_ATTENTION"


def test_cleanup_does_not_claim_success_on_daemon_failure(fake_runtime):
    path = runtime_check._resource_path(JOB)
    path.write_text(json.dumps({"owner": "v0.1", "job_id": JOB, "container_id": CID}), encoding="utf-8")
    failed = Mock()
    failed.run.return_value = ProcessResult(1, "Access denied to daemon")
    assert runtime_check.cleanup_owned(JOB, runner=failed, state=STATE) == "NEEDS_ATTENTION"


@pytest.mark.parametrize("response", [
    ProcessResult(1, "failed"), ProcessResult(-1, "timeout", timed_out=True),
    ProcessResult(-1, "cancel", cancelled=True),
])
def test_build_failure_cannot_become_artifact(plan, monkeypatch, response):
    monkeypatch.setattr(base, "verify_plan", lambda _: None)
    monkeypatch.setattr(base, "require_local_docker", lambda _: STATE)
    runner = Mock()
    runner.run.return_value = response
    with pytest.raises(base.BuildError) as caught:
        base.build_image(plan, runner=runner)
    assert caught.value.logs == response.output


def test_build_inspects_actual_image(plan, monkeypatch):
    monkeypatch.setattr(base, "verify_plan", lambda _: None)
    monkeypatch.setattr(base, "require_local_docker", lambda _: STATE)
    runner, progress, stage = FakeDocker(), Mock(), Mock()
    artifact = base.build_image(plan, runner=runner, on_progress=progress, on_stage=stage)
    assert artifact.image_id == IMAGE and artifact.status == "BUILT"
    assert runner.calls[0][1]["on_progress"] is progress
    assert [call.args[0] for call in stage.call_args_list] == [
        "실행 도구 확인", "이미지 생성", "생성 이미지 식별정보 확인",
    ]
    assert "--progress" not in runner.calls[0][0]  # Legacy Docker needs no buildx-only flags.


def test_inspect_optional_config_keys_and_failed_inspect_keeps_build_evidence(plan, monkeypatch):
    monkeypatch.setattr(base, "verify_plan", lambda _: None)
    monkeypatch.setattr(base, "require_local_docker", lambda _: STATE)

    class InspectFailure(FakeDocker):
        def run(self, args, **kwargs):
            if "inspect" in args:
                template = args[args.index("--format") + 1]
                assert '.Config.User' not in template and '.Config.Volumes' not in template
                assert 'index .Config "Volumes"' in template
                return ProcessResult(1, "inspection failed")
            return ProcessResult(0, "build completed")

    with pytest.raises(base.BuildError) as caught:
        base.build_image(plan, runner=InspectFailure())
    assert "build completed" in caught.value.logs and "inspection failed" in caught.value.logs
    assert (plan.manifest_path.parent / "build.log").read_text() == "build completed"
    assert json.loads((plan.manifest_path.parent / "build-result.json").read_text())["returncode"] == 0
    assert not (plan.manifest_path.parent / "image.json").exists()


def test_paketo_finalizer_is_metadata_only_and_preserves_layers(plan, monkeypatch):
    source = {"id": IMAGE, "onbuild_count": 0, "volumes": [], "user": "1002:1000",
              "rootfs_layers": ["sha256:" + "f" * 64]}
    final = source | {"id": "sha256:" + "9" * 64, "volumes": ["/tmp"]}
    inspected = Mock(side_effect=[source, final, source])
    monkeypatch.setattr(base, "inspect_image", inspected)
    runner = Mock()
    runner.run.return_value = ProcessResult(0, "metadata image created")
    progress = Mock()
    actual, evidence, _ = paketo_java.finalize_image(
        plan, STATE, "intermediate", source, "final", runner, on_progress=progress,
    )
    dockerfile = (plan.manifest_path.parent / "image-finalization/Dockerfile").read_text()
    assert dockerfile == f'FROM {IMAGE}\nVOLUME ["/tmp"]\n'
    assert not any(word in dockerfile for word in ("RUN", "USER", "ENTRYPOINT", "CMD", "COPY", "ENV"))
    args = runner.run.call_args.args[0]
    assert args[args.index("--network") + 1] == "none" and "--pull=false" in args
    assert runner.run.call_args.kwargs["on_progress"] is progress
    assert "--progress" not in args
    assert actual == final and evidence["final_image_id"] == final["id"]
    assert evidence["intermediate_image_id"] == IMAGE and evidence["kind"] == "paketo_tmp_volume_v1"


@pytest.mark.parametrize("changes", [{"onbuild_count": 1}, {"volumes": ["/data"]}, {"user": "0:0"}])
def test_paketo_finalizer_rejects_code_hooks_extra_volumes_or_root(plan, changes):
    source = {"id": IMAGE, "onbuild_count": 0, "volumes": [], "user": "1002:1000"} | changes
    runner = Mock()
    with pytest.raises(base.BuildError):
        paketo_java.finalize_image(plan, STATE, "intermediate", source, "final", runner)
    runner.run.assert_not_called()


@pytest.mark.parametrize("status,body,header,expected", [
    (200, b"healthy", None, True), (302, b"healthy", None, False), (404, b"missing", None, False),
    (200, b"wrong", None, False), (200, b"healthy", "99999999", False),
    (200, b"x" * 65537, None, False),
], ids=["ok", "redirect", "missing", "marker", "content-length", "oversized"])
def test_http_bounded_no_redirects_or_body_in_result(monkeypatch, conditions, status, body, header, expected):
    connection = Mock()
    response = Mock(status=status)
    response.isclosed.return_value = False
    response.getheader.return_value = header
    response.read1.side_effect = [body, b""]
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(runtime_check.http.client, "HTTPConnection", factory)
    actual, http_status, message = runtime_check.probe_http(12345, conditions)
    assert actual == expected and http_status == status
    assert "healthy" not in message and "missing" not in message
    factory.assert_called_once_with("127.0.0.1", 12345, timeout=2.0)
    assert connection.request.call_count == 1
