"""Explicit synthetic build records; never invokes Docker or relaxes real input approvals."""

import json
from unittest.mock import Mock

import pytest

from aws_app_packager import config
from aws_app_packager.models import ImageArtifact, RuntimeConditions
from aws_app_packager.project_input import assess_project, prepare_build_plan
from aws_app_packager.trusted_samples import TRUSTED_SAMPLES, verify_trusted_sample


@pytest.fixture(params=["spring-http", "docker-http"])
def trusted_record(request, monkeypatch, tmp_path):
    monkeypatch.setenv("RUN_LOCAL_CONTAINER_TESTS", "1")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    name = request.param
    method, marker, source_hash = TRUSTED_SAMPLES[name]
    plan = prepare_build_plan(assess_project(config.ROOT / "samples" / name), "a" * 32,
                              build_approved=True)
    assert plan.source_fingerprint == source_hash
    artifact = ImageArtifact(
        job_id=plan.job_id, project_label=name, build_method=method, source_fingerprint=source_hash,
        local_tag=f"aws-app-packager/{name}:{plan.job_id}", image_id="sha256:" + "b" * 64,
        size_bytes=123, user="10001", manifest_path=plan.manifest_path,
    )
    job = plan.manifest_path.parent
    (job / "image.json").write_text(artifact.model_dump_json(), encoding="utf-8")
    evidence = {
        "job_id": artifact.job_id, "image_id": artifact.image_id, "image_inspect_status": "PASS",
        "method": method, "source_fingerprint": source_hash, "local_tag": artifact.local_tag,
        "returncode": 0, "cancelled": False, "timed_out": False,
        "finalization": {"status": "PASS", "kind": "paketo_tmp_volume_v1",
                         "final_image_id": artifact.image_id},
    }
    (job / "build-result.json").write_text(json.dumps(evidence), encoding="utf-8")
    conditions = RuntimeConditions(container_port=8080, health_path="/health", expected_marker=marker)
    return artifact, conditions, job, evidence


def test_reviewed_source_and_matching_build_records_pass_guard(trusted_record):
    artifact, conditions, _, _ = trusted_record
    verify_trusted_sample(artifact, conditions)


def test_normal_bridge_requires_opt_in_before_reading_input(trusted_record, monkeypatch):
    artifact, conditions, _, _ = trusted_record
    monkeypatch.delenv("RUN_LOCAL_CONTAINER_TESTS")
    read = Mock(side_effect=AssertionError("must not read source without opt-in"))
    monkeypatch.setattr("aws_app_packager.trusted_samples.assess_project", read)
    with pytest.raises(ValueError, match="RUN_LOCAL_CONTAINER_TESTS"):
        verify_trusted_sample(artifact, conditions)
    read.assert_not_called()


@pytest.mark.parametrize("change", ["name", "source", "port", "marker", "path", "environment", "restart"])
def test_only_exact_reviewed_sample_and_conditions_are_authorized(trusted_record, change):
    artifact, conditions, _, _ = trusted_record
    if change == "name":
        artifact.project_label = "external-app"
    elif change == "source":
        artifact.source_fingerprint = "c" * 64
    elif change == "port":
        conditions.container_port = 8081
    elif change == "marker":
        conditions.expected_marker = None
    elif change == "path":
        conditions.health_path = "/other"
    elif change == "environment":
        conditions.environment = {"PORT": "1234"}
    else:
        conditions.restart = False
    with pytest.raises(ValueError):
        verify_trusted_sample(artifact, conditions)


def test_recorded_image_cannot_be_substituted(trusted_record):
    artifact, conditions, _, _ = trusted_record
    artifact.image_id = "sha256:" + "c" * 64
    with pytest.raises(ValueError, match="일치"):
        verify_trusted_sample(artifact, conditions)


def test_failed_build_evidence_does_not_authorize_normal_bridge(trusted_record):
    artifact, conditions, job, evidence = trusted_record
    evidence["returncode"] = 1
    (job / "build-result.json").write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="일치"):
        verify_trusted_sample(artifact, conditions)


def test_contradictory_build_source_does_not_authorize_normal_bridge(trusted_record):
    artifact, conditions, job, evidence = trusted_record
    evidence["source_fingerprint"] = "c" * 64
    (job / "build-result.json").write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="일치"):
        verify_trusted_sample(artifact, conditions)


def test_changed_source_file_set_cannot_use_reviewed_hash(trusted_record):
    artifact, conditions, job, _ = trusted_record
    path = job / "build-context-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"] = {}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="일치"):
        verify_trusted_sample(artifact, conditions)
