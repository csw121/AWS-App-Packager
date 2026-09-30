"""Developer resume guards: synthetic records only, no real container calls."""

import json
from types import SimpleNamespace

import pytest

from aws_app_packager import config
from aws_app_packager.models import ImageArtifact, RuntimeCheck, fingerprint
from devtools import local_integration
from devtools.local_integration import _previous_builds


def test_resume_rejects_external_or_nonreal_records(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / ".work")
    outside = tmp_path / "external.json"
    outside.write_text('{"kind":"REAL_LOCAL_INTEGRATION"}', encoding="utf-8")
    with pytest.raises(ValueError, match="작업공간"):
        _previous_builds(outside)
    directory = config.WORK_DIR / "integration"
    directory.mkdir(parents=True)
    record = directory / "result.json"
    record.write_text('{"kind":"MOCK"}', encoding="utf-8")
    with pytest.raises(ValueError, match="실제 샘플"):
        _previous_builds(record)


def test_resume_without_actual_built_artifact_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / ".work")
    directory = config.WORK_DIR / "integration"
    directory.mkdir(parents=True)
    record = directory / "result.json"
    record.write_text(json.dumps({
        "kind": "REAL_LOCAL_INTEGRATION", "samples": [{"sample": "docker-http", "status": "BLOCKED"}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="생성 이미지 기록"):
        _previous_builds(record)


@pytest.fixture
def mocked_suite(tmp_path, monkeypatch):
    """Synthetic gate tests only: no build, Docker, HTTP, AWS, or real bundle exports."""
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / ".work")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "exports")
    state = {"docker_ready": True, "pack_ready": True, "blockers": []}
    monkeypatch.setattr(local_integration, "check_environment", lambda **_: state)
    monkeypatch.setattr(local_integration, "assess_project", lambda path: SimpleNamespace(
        blockers=[], method=local_integration.TRUSTED_SAMPLES[path.name][0],
        source_fingerprint=local_integration.TRUSTED_SAMPLES[path.name][2],
    ))
    artifacts = {
        name: ImageArtifact(
            job_id=digit * 32, project_label=name, build_method="paketo_java" if index == 0 else "dockerfile",
            source_fingerprint=digit * 64, local_tag=f"aws-app-packager/{name}:{digit * 32}",
            image_id="sha256:" + digit * 64, size_bytes=42, user="10001",
            manifest_path=tmp_path / "synthetic-manifest.json",
        )
        for index, (name, digit) in enumerate(zip(local_integration.SAMPLES, ("1", "2"), strict=True))
    }
    events = []
    overrides = {}
    persisted = []
    original_write = local_integration._write

    def write(path, value):
        persisted.append(json.loads(json.dumps(value)))
        original_write(path, value)

    def check(artifact, conditions, *, runtime_approved, on_stage):
        assert runtime_approved
        assert conditions.network_mode == "authored_sample_bridge"
        assert conditions.container_port == 8080 and conditions.health_path == "/health"
        assert conditions.expected_marker == local_integration.SAMPLES[artifact.project_label]
        assert conditions.restart
        index = len([event for event in events if event[0] == "check"])
        events.append(("check", artifact.project_label, conditions.preset, artifact.image_id))
        on_stage("합성 시험 관찰 — 실제 Docker 실행 아님")
        values = {
            "job_id": artifact.job_id, "image_id": artifact.image_id,
            "source_fingerprint": artifact.source_fingerprint, "conditions": conditions,
            "conditions_fingerprint": fingerprint(conditions), "status": "PASS",
            "finished_at": "2026-10-01T00:00:00+00:00", "http_status": 200,
            "image_verified": True, "container_started": True,
            "initial_http_status": 200, "restart_http_status": 200,
            "restart_performed": True, "restart_passed": True, "cleanup_status": "CLEANED",
            "host_port": 45678, "network_driver": "bridge", "network_internal": False,
            "port_evidence": [
                {"phase": phase, "inspect_output": '{"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"45678"}]}',
                 "inspect_returncode": 0, "docker_port_output": "127.0.0.1:45678\n",
                 "docker_port_returncode": 0, "host_port": 45678, "verified": True}
                for phase in ("start", "restart")
            ],
        }
        values.update(overrides.get(index, {}))
        return RuntimeCheck(**values)

    def export(artifact, check, spec):
        assert len([event for event in events if event[0] == "check"]) == 8
        assert spec.preset == check.conditions.preset
        assert artifact.image_id == check.image_id
        events.append(("export", artifact.project_label, spec.preset, artifact.image_id))
        return SimpleNamespace(
            directory=tmp_path / f"synthetic-{artifact.project_label}-{spec.preset}",
            zip_path=tmp_path / f"synthetic-{artifact.project_label}-{spec.preset}.zip",
            local_status="PASS", aws_status="AWS_NOT_TESTED",
        )

    def forbidden_build(*_args, **_kwargs):
        pytest.fail("A synthetic gate test must not build images.")

    monkeypatch.setattr(local_integration, "_write", write)
    monkeypatch.setattr(local_integration, "build_image", forbidden_build)
    monkeypatch.setattr(local_integration, "run_check", check)
    monkeypatch.setattr(local_integration, "export_bundle", export)
    return SimpleNamespace(artifacts=artifacts, events=events, overrides=overrides,
                           persisted=persisted, environment=state)


def test_both_initial_checks_then_same_image_size_matrix_then_exports(mocked_suite):
    report, path = local_integration._run_suite(previous=mocked_suite.artifacts)
    checks = [event for event in mocked_suite.events if event[0] == "check"]
    exports = [event for event in mocked_suite.events if event[0] == "export"]
    assert [(event[1], event[2]) for event in checks] == [
        ("spring-http", "medium"), ("docker-http", "medium"),
        ("spring-http", "small"), ("spring-http", "medium"), ("spring-http", "large"),
        ("docker-http", "small"), ("docker-http", "medium"), ("docker-http", "large"),
    ]
    assert len(exports) == 6 and mocked_suite.events == checks + exports
    for event in checks + exports:
        assert event[3] == mocked_suite.artifacts[event[1]].image_id
    assert report["initial_gate"] == report["extended"]["matrix_gate"] == report["status"] == "PASS"
    assert report["aws_status"] == "AWS_NOT_TESTED"
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert any(value["status"] == "RUNNING" and value["samples"]
               and value["samples"][0].get("stage") == "합성 시험 관찰 — 실제 Docker 실행 아님"
               for value in mocked_suite.persisted)
    assert any(len(value["samples"]) == 1 and value["samples"][0].get("check")
               for value in mocked_suite.persisted)


@pytest.mark.parametrize("final_port", [45679, 45678])
def test_restart_may_reassign_verified_loopback_port_and_must_record_final_port(mocked_suite, final_port):
    mocked_suite.overrides[0] = {
        "host_port": final_port,
        "port_evidence": [
            {"phase": phase, "inspect_output": json.dumps({
                "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}],
            }), "inspect_returncode": 0, "docker_port_output": f"127.0.0.1:{port}\n",
             "docker_port_returncode": 0, "host_port": port, "verified": True}
            for phase, port in (("start", 45678), ("restart", 45679))
        ],
    }
    report, _ = local_integration._run_suite(previous=mocked_suite.artifacts)
    if final_port == 45679:
        assert report["status"] == report["initial_gate"] == "PASS"
        assert len(report["extended"]["bundles"]) == 6
    else:
        assert report["status"] == "FAIL"
        assert report["samples"][0]["failure_layer"] == "integration_evidence"
        assert report["extended"]["bundles"] == []


@pytest.mark.parametrize(("changes", "expected_status"), [
    ({"status": "FAIL", "initial_http_status": 404, "failure_layer": "http"}, "FAIL"),
    ({"status": "ENVIRONMENT_BLOCKED", "port_evidence": [], "failure_layer": "port_mapping"},
     "ENVIRONMENT_BLOCKED"),
    ({"cleanup_status": "NEEDS_ATTENTION"}, "FAIL"),
    ({"restart_http_status": None}, "FAIL"),
    ({"network_internal": True}, "FAIL"),
    ({"image_verified": False}, "FAIL"),
    ({"port_evidence": []}, "FAIL"),
])
def test_incomplete_initial_sequence_prevents_all_retests_and_exports(mocked_suite, changes, expected_status):
    mocked_suite.overrides[0] = changes
    report, _ = local_integration._run_suite(previous=mocked_suite.artifacts)
    assert report["status"] == expected_status
    assert report["initial_gate"] == "NOT_PASSED"
    assert report["extended"]["status"] == "NOT_RUN"
    assert len(mocked_suite.events) == 2
    assert report["extended"]["size_matrix"] == report["extended"]["bundles"] == []


def test_matrix_failure_prevents_every_success_bundle(mocked_suite):
    mocked_suite.overrides[3] = {
        "status": "FAIL", "restart_http_status": 503, "failure_layer": "restart_http",
    }
    report, _ = local_integration._run_suite(previous=mocked_suite.artifacts)
    assert report["initial_gate"] == "PASS"
    assert report["extended"]["matrix_gate"] == report["status"] == "FAIL"
    assert len(mocked_suite.events) == 8 and all(event[0] == "check" for event in mocked_suite.events)
    assert report["extended"]["bundles"] == []


def test_basic_checks_only_never_export(mocked_suite):
    report, _ = local_integration._run_suite(previous=mocked_suite.artifacts, extended=False)
    assert report["status"] == report["initial_gate"] == "PASS"
    assert report["extended"]["status"] == "NOT_RUN"
    assert len(mocked_suite.events) == 2
    assert report["extended"]["bundles"] == []


def test_selected_sample_cannot_claim_both_sample_gate(mocked_suite):
    report, _ = local_integration._run_suite(samples=["docker-http"], previous=mocked_suite.artifacts)
    assert report["status"] == "PASS" and report["scope"] == "SELECTED_SAMPLES_ONLY"
    assert report["initial_gate"] == "NOT_PASSED"
    assert report["extended"]["status"] == "NOT_RUN"
    assert len(mocked_suite.events) == 1


def test_unavailable_daemon_is_environment_blocked_without_runtime(mocked_suite):
    mocked_suite.environment.update(docker_ready=False, blockers=["synthetic unavailable daemon"])
    report, path = local_integration._run_suite(previous=mocked_suite.artifacts)
    assert report["status"] == "ENVIRONMENT_BLOCKED"
    assert all(item["failure_layer"] == "docker_preflight" for item in report["samples"])
    assert not mocked_suite.events
    assert path.is_file()


def test_retesting_actual_java_image_does_not_require_pack(mocked_suite):
    mocked_suite.environment["pack_ready"] = False
    report, _ = local_integration._run_suite(previous=mocked_suite.artifacts, extended=False)
    assert report["status"] == "PASS"
    assert len(mocked_suite.events) == 2


@pytest.mark.parametrize("samples", [[], ["external"], ["docker-http", "docker-http"]])
def test_invalid_or_duplicate_sample_selection_is_rejected(mocked_suite, samples):
    with pytest.raises(ValueError, match="자체 작성"):
        local_integration._run_suite(samples=samples, previous=mocked_suite.artifacts)
    assert not mocked_suite.events


def test_real_suite_requires_explicit_opt_in_before_any_work(monkeypatch):
    monkeypatch.delenv("RUN_LOCAL_CONTAINER_TESTS", raising=False)
    with pytest.raises(RuntimeError, match="RUN_LOCAL_CONTAINER_TESTS=1"):
        local_integration.run_suite()


def test_modified_canonical_sample_is_rejected_before_build_or_runtime(mocked_suite, monkeypatch):
    monkeypatch.setattr(local_integration, "assess_project", lambda _: SimpleNamespace(
        blockers=[], method="dockerfile", source_fingerprint="changed-unreviewed-source",
    ))
    report, _ = local_integration._run_suite(samples=["docker-http"])
    assert report["status"] == "BLOCKED"
    assert report["samples"][0]["failure_layer"] == "source_approval"
    assert not mocked_suite.events
