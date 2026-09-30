"""Offline export tests. Fake runner tests are NOT container or AWS verification."""

import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from aws_app_packager import config
from aws_app_packager.models import (
    DeploymentSpec,
    ImageArtifact,
    RuntimeCheck,
    RuntimeConditions,
    fingerprint,
)
from aws_app_packager.presets import PRESETS
from aws_app_packager.terraform_export import (
    _owned_output,
    export_bundle,
    file_sha256,
    save_image_tar,
    validate_template_structure,
)


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "exports")
    now = datetime.now(UTC)
    artifact = ImageArtifact(
        job_id="a" * 32, project_label="authored-sample", build_method="dockerfile",
        source_fingerprint="b" * 64, local_tag="aws-app-packager/authored-sample:" + "a" * 32,
        image_id="sha256:" + "c" * 64, size_bytes=1024, user="1000:1000",
        manifest_path=tmp_path / "private-location" / "manifest.json", created_at=now.isoformat(),
    )
    conditions = RuntimeConditions(container_port=8080, health_path="/health")
    check = RuntimeCheck(
        job_id=artifact.job_id, image_id=artifact.image_id, source_fingerprint=artifact.source_fingerprint,
        conditions=conditions, conditions_fingerprint=fingerprint(conditions), status="PASS",
        http_status=200, restart_passed=True, cleanup_status="CLEANED",
        started_at=(now + timedelta(seconds=1)).isoformat(),
        finished_at=(now + timedelta(seconds=2)).isoformat(),
    )
    spec = DeploymentSpec(project_label="authored-sample", container_port=8080, health_path="/health")
    return artifact, check, spec


def test_bundle_contains_separate_roots_and_unfilled_external_inputs(evidence):
    artifact, check, spec = evidence
    bundle = export_bundle(artifact, check, spec)
    service = json.loads((bundle.directory / "terraform/service/terraform.tfvars.json").read_text())
    assert all(key not in service for key in ("image_uri", "certificate_arn", "availability_zones"))
    assert service["preset"] == "medium" and service["container_port"] == 8080
    assert service["container_user"] == artifact.user
    assert (bundle.directory / "terraform/registry/main.tf").is_file()
    assert bundle.terraform_cli_status == "NOT_RUN_FOR_THIS_BUNDLE"
    assert bundle.aws_status == "AWS_NOT_TESTED"
    manifest = json.loads((bundle.directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["template_status"] == "STATIC_CHECKED"
    for name, digest in manifest["files"].items():
        assert file_sha256(bundle.directory / name) == digest
    with zipfile.ZipFile(bundle.zip_path) as archive:
        assert all(
            ".." not in Path(name).parts and not Path(name).is_absolute() for name in archive.namelist()
        )
        assert not any(name.endswith((".tar", ".tftest.hcl")) for name in archive.namelist())
        assert sum(item.file_size for item in archive.infolist()) < 2 * 1024 * 1024
    image_data = json.loads((bundle.directory / "image/image.json").read_text(encoding="utf-8"))
    assert "manifest_path" not in image_data
    assert image_data["ecr_manifest_digest"] == "NOT_KNOWN_UNTIL_MANUAL_PUSH"


@pytest.mark.parametrize("preset", ["small", "medium", "large"])
def test_presets_match_test_hcl_and_spec(evidence, preset):
    artifact, check, spec = evidence
    conditions = check.conditions.model_copy(update={"preset": preset})
    check = check.model_copy(update={
        "conditions": conditions, "conditions_fingerprint": fingerprint(conditions),
    })
    spec.preset = preset
    bundle = export_bundle(artifact, check, spec)
    report = (bundle.directory / "AWS_SPEC.md").read_text(encoding="utf-8")
    size = PRESETS[preset]
    assert f"{size.vcpu:g} / {size.memory_mib} / {size.cpu_units}" in report
    hcl = (bundle.directory / "terraform/service/main.tf").read_text()
    assert 'jsondecode(file("${path.module}/presets.json"))' in hcl
    preset_data = json.loads((bundle.directory / "terraform/service/presets.json").read_text())
    assert preset_data[preset] == {"cpu": size.cpu_units, "memory": size.memory_mib, "desired_count": 1}
    values = json.loads((bundle.directory / "terraform/service/terraform.tfvars.json").read_text())
    assert values["preset"] == preset


@pytest.mark.parametrize("change", [
    {"status": "FAIL"}, {"status": "STALE"}, {"status": "NOT_RUN"}, {"http_status": 404},
    {"image_id": "sha256:" + "d" * 64}, {"source_fingerprint": "e" * 64},
    {"job_id": "f" * 32}, {"conditions_fingerprint": "invalid"}, {"finished_at": None},
    {"restart_passed": False},
    {"cleanup_status": "NEEDS_ATTENTION"}, {"cleanup_status": "NOT_NEEDED"},
])
def test_failed_or_stale_evidence_cannot_export(evidence, change):
    artifact, check, spec = evidence
    with pytest.raises(ValueError):
        export_bundle(artifact, check.model_copy(update=change), spec)
    assert not config.EXPORT_DIR.exists()


@pytest.mark.parametrize("change", [
    {"preset": "small"}, {"container_port": 9000}, {"health_path": "/other"},
    {"environment": {"PORT": "8081"}}, {"template_version": "fake"},
])
def test_changed_conditions_require_retest(evidence, change):
    artifact, check, spec = evidence
    with pytest.raises(ValueError):
        export_bundle(artifact, check, spec.model_copy(update=change))


@pytest.mark.parametrize("change", [
    {"exposure_mode": "http_demo"},
    {"allowed_cidrs": ["0.0.0.0/0"]},
    {"allowed_cidrs": ["::/0"]},
    {"allowed_cidrs": ["invalid"]},
    {"availability_zones": ["ap-northeast-2a", "ap-northeast-2a"]},
    {"availability_zones": ["us-east-1a", "us-east-1b"]},
    {"image_uri": "sha256:" + "a" * 64},
    {"image_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/app@sha256:" + "a" * 64},
    {"existing_execution_role_arn": "arn:aws:iam::123456789012:role/Role"},
    {"create_execution_role": False, "existing_execution_role_arn": "invalid"},
    {"certificate_arn": "arn:aws:acm:us-east-1:123456789012:certificate/abc"},
    {"domain_name": "https://example.com"},
])
def test_invalid_external_inputs_are_blocked(evidence, change):
    artifact, check, spec = evidence
    with pytest.raises(ValueError):
        export_bundle(artifact, check, spec.model_copy(update=change))


def test_http_demo_explicit_and_limited(evidence):
    artifact, check, spec = evidence
    spec.exposure_mode = "http_demo"
    spec.allowed_cidrs = ["203.0.113.10/32"]
    spec.create_execution_role = False
    bundle = export_bundle(artifact, check, spec)
    report = (bundle.directory / "README_FIRST.md").read_text(encoding="utf-8")
    assert "HTTP 실습용 / 실서비스 보안 미구현" in report
    assert any("existing_execution_role_arn" in item for item in bundle.external_inputs)


def test_unsafe_user_and_private_logs_not_exported(evidence):
    artifact, check, spec = evidence
    check.logs = r"password=do-not-export C:\Users\Someone\private\source jdbc:mysql://private/db"
    bundle = export_bundle(artifact, check, spec)
    data = (bundle.directory / "local-test-result.json").read_text(encoding="utf-8")
    assert "do-not-export" not in data and "Someone" not in data and "private/db" not in data
    with pytest.raises(ValueError):
        export_bundle(artifact.model_copy(update={"user": "1000`\n![x](https://invalid)"}), check, spec)


def test_template_static_policy_and_no_cloud_execution():
    hashes = validate_template_structure()
    assert len(hashes) == 11
    main = (config.TEMPLATE_DIR / "service/main.tf").read_text()
    assert "container_definitions = jsonencode" in main
    assert 'image                  = var.image_uri' in main
    assert 'name = "app-tmp"' in main
    assert 'dockerSecurityOptions' not in main and 'tmpfs' not in main
    registry = (config.TEMPLATE_DIR / "registry/main.tf").read_text()
    assert '"IMMUTABLE"' in registry and "force_delete         = false" in registry


def test_output_traversal_rejected(evidence):
    with pytest.raises(ValueError):
        _owned_output(config.EXPORT_DIR / ".." / "escape.tar")


def _fake_tar_tools(monkeypatch, content=b"explicit mocked docker image save data", fail=False,
                    on_saved=None):
    from aws_app_packager import preflight
    from aws_app_packager.builders import base

    monkeypatch.setattr(preflight, "require_local_docker", lambda **kwargs: {})
    monkeypatch.setattr(base, "assert_same_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(preflight, "docker_arguments", lambda state, *args: ["docker", *args])
    monkeypatch.setattr(preflight, "tool_environment", lambda state: {})

    class FakeRunner:
        def __init__(self):
            self.calls = []

        def run(self, args, **kwargs):
            self.calls.append((args, kwargs))
            assert args[1:3] == ["image", "save"]
            Path(args[args.index("--output") + 1]).write_bytes(content)
            if on_saved:
                on_saved()
            return SimpleNamespace(
                returncode=1 if fail else 0, output="mocked", timed_out=False, cancelled=False,
            )

    return FakeRunner()


def test_tar_streamed_atomic_with_hash(evidence, monkeypatch):
    artifact, _, _ = evidence
    runner = _fake_tar_tools(monkeypatch)
    destination = config.EXPORT_DIR / "image/app-image.tar"
    stages = []
    cancel = Event()

    def progress_callback(value):
        pass

    result = save_image_tar(
        artifact, destination, runner=runner, cancel=cancel,
        on_progress=progress_callback, on_stage=stages.append,
    )
    assert stages == ["이미지 파일 SHA256 확인", "이미지 파일 저장 확정"]
    assert runner.calls[0][1]["on_progress"] is progress_callback
    assert runner.calls[0][1]["cancel"] is cancel
    assert result["sha256"] == file_sha256(destination)
    assert result["local_image_id"] == artifact.image_id
    assert destination.with_suffix(".tar.json").is_file()
    assert not list(destination.parent.glob("*.tmp"))
    with pytest.raises(ValueError):
        save_image_tar(artifact, destination, runner=runner)


def test_tar_failure_cleans_partial_output(evidence, monkeypatch):
    artifact, _, _ = evidence
    runner = _fake_tar_tools(monkeypatch, fail=True)
    destination = config.EXPORT_DIR / "image/app-image.tar"
    with pytest.raises(RuntimeError):
        save_image_tar(artifact, destination, runner=runner)
    assert not destination.exists() and not list(destination.parent.glob("*.tmp"))


def test_tar_disk_check_before_docker(evidence, monkeypatch):
    artifact, _, _ = evidence
    monkeypatch.setattr(
        "aws_app_packager.terraform_export.shutil.disk_usage", lambda path: SimpleNamespace(free=1),
    )
    with pytest.raises(ValueError, match="디스크"):
        save_image_tar(artifact, config.EXPORT_DIR / "image/app-image.tar")


def test_tar_already_cancelled_does_not_call_docker(evidence, monkeypatch):
    artifact, _, _ = evidence
    runner = _fake_tar_tools(monkeypatch)
    cancel = Event()
    cancel.set()
    with pytest.raises(RuntimeError, match="취소"):
        save_image_tar(artifact, config.EXPORT_DIR / "image/app-image.tar", runner, cancel)
    assert runner.calls == []
    assert not config.EXPORT_DIR.exists()


def test_tar_cancelled_after_successful_subprocess_cleans_only_its_temp(evidence, monkeypatch):
    artifact, _, _ = evidence
    cancel = Event()
    runner = _fake_tar_tools(monkeypatch, on_saved=cancel.set)
    destination = config.EXPORT_DIR / "image/app-image.tar"
    destination.parent.mkdir(parents=True)
    existing = destination.parent / ".another-job.tmp"
    existing.write_bytes(b"unrelated file")
    with pytest.raises(RuntimeError, match="취소"):
        save_image_tar(artifact, destination, runner, cancel)
    assert list(destination.parent.iterdir()) == [existing]
    assert existing.read_bytes() == b"unrelated file"


def test_tar_hash_cancellation_stops_after_current_chunk_and_cleans_temp(evidence, monkeypatch):
    from aws_app_packager import terraform_export

    artifact, _, _ = evidence
    cancel = Event()
    runner = _fake_tar_tools(monkeypatch, content=b"x" * (3 * 1024 * 1024))
    real_sha256 = terraform_export.hashlib.sha256
    chunks = []

    class CancellingHash:
        def __init__(self):
            self.digest = real_sha256()

        def update(self, chunk):
            chunks.append(len(chunk))
            self.digest.update(chunk)
            cancel.set()

        def hexdigest(self):
            pytest.fail("Cancelled hash must not be used as completed evidence")

    monkeypatch.setattr(terraform_export.hashlib, "sha256", CancellingHash)
    destination = config.EXPORT_DIR / "image/app-image.tar"
    with pytest.raises(RuntimeError, match="취소"):
        save_image_tar(artifact, destination, runner, cancel)
    assert chunks == [1024 * 1024]
    assert not list(destination.parent.iterdir())


def test_tar_cancellation_immediately_before_publish_cleans_prepared_pair(evidence, monkeypatch):
    artifact, _, _ = evidence
    cancel = Event()
    runner = _fake_tar_tools(monkeypatch)
    destination = config.EXPORT_DIR / "image/app-image.tar"
    stages = []

    def on_stage(stage):
        stages.append(stage)
        if stage == "이미지 파일 저장 확정":
            assert len(list(destination.parent.glob("*.tmp"))) == 2
            cancel.set()

    with pytest.raises(RuntimeError, match="취소"):
        save_image_tar(artifact, destination, runner, cancel, on_stage=on_stage)
    assert stages == ["이미지 파일 SHA256 확인", "이미지 파일 저장 확정"]
    assert not list(destination.parent.iterdir())


def test_tar_preserves_existing_metadata(evidence, monkeypatch):
    artifact, _, _ = evidence
    runner = _fake_tar_tools(monkeypatch)
    destination = config.EXPORT_DIR / "image/app-image.tar"
    destination.parent.mkdir(parents=True)
    metadata = destination.with_suffix(".tar.json")
    metadata.write_bytes(b"existing metadata")
    with pytest.raises(ValueError, match="덮어쓰지"):
        save_image_tar(artifact, destination, runner)
    assert runner.calls == []
    assert metadata.read_bytes() == b"existing metadata"
