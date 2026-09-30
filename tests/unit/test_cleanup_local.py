import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from aws_app_packager import config
from aws_app_packager.jobs import BusyError, JobManager

SPEC = importlib.util.spec_from_file_location(
    "cleanup_local_test_module", Path(__file__).resolve().parents[2] / "devtools" / "cleanup_local.py"
)
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


@pytest.fixture
def job(tmp_path, monkeypatch):
    work = tmp_path / "tool-work"
    monkeypatch.setattr(config, "WORK_DIR", work)
    job_id = uuid4().hex
    path = work / "jobs" / job_id
    snapshot = path / "snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "source.txt").write_text("authored test input")
    (path / "build-context-manifest.json").write_text(json.dumps({"job_id": job_id, "files": {}}))
    # Every test uses an explicit stub; real Docker is never invoked by this suite.
    monkeypatch.setattr(cleanup, "cleanup_owned", lambda job_id: "CLEANED")
    return job_id, path


def test_cleanup_deletes_only_requested_snapshot_after_success(job, monkeypatch):
    job_id, path = job
    (path / "image.json").write_text("retain image metadata")
    unrelated = path.parent / uuid4().hex
    unrelated.mkdir()
    (unrelated / "source.txt").write_text("preserve other jobs")
    calls = []

    def cleaned(value):
        assert (path / "snapshot" / "source.txt").exists()
        calls.append(value)
        return "CLEANED"

    monkeypatch.setattr(cleanup, "cleanup_owned", cleaned)
    result = cleanup.cleanup_job(job_id, remove_snapshot=True)
    assert calls == [job_id]
    assert result["snapshot_status"] == "REMOVED"
    assert not (path / "snapshot").exists()
    assert (path / "image.json").exists()
    assert (path / "build-context-manifest.json").exists()
    assert (unrelated / "source.txt").exists()


def test_cleanup_without_snapshot_flag_preserves_source(job):
    job_id, path = job
    assert cleanup.cleanup_job(job_id)["snapshot_status"] == "NOT_REQUESTED"
    assert (path / "snapshot" / "source.txt").exists()


def test_failed_resource_cleanup_preserves_snapshot(job, monkeypatch):
    job_id, path = job
    monkeypatch.setattr(cleanup, "cleanup_owned", lambda job_id: "NEEDS_ATTENTION")
    result = cleanup.cleanup_job(job_id, remove_snapshot=True)
    assert result["snapshot_status"] == "PRESERVED"
    assert (path / "snapshot" / "source.txt").exists()


def test_cleanup_refuses_active_managed_job(job):
    job_id, path = job
    lease = JobManager(work_dir=config.WORK_DIR)._acquire_file_lock()
    try:
        with pytest.raises(BusyError):
            cleanup.cleanup_job(job_id, remove_snapshot=True)
    finally:
        lease.close()
    assert (path / "snapshot" / "source.txt").exists()


@pytest.mark.parametrize("value", ["../other", "", "a" * 31, "a" * 33, "../../exports", "/"])
def test_invalid_job_identifier_never_calls_cleanup(job, monkeypatch, value):
    monkeypatch.setattr(cleanup, "cleanup_owned", lambda _: pytest.fail("must not invoke Docker"))
    with pytest.raises(cleanup.CleanupError):
        cleanup.cleanup_job(value, remove_snapshot=True)


def test_unrelated_directory_and_manifest_mismatch_are_rejected(job):
    job_id, path = job
    with pytest.raises(cleanup.CleanupError):
        cleanup.cleanup_job(uuid4().hex)
    (path / "build-context-manifest.json").write_text(json.dumps({"job_id": uuid4().hex, "files": {}}))
    with pytest.raises(cleanup.CleanupError):
        cleanup.cleanup_job(job_id, remove_snapshot=True)
    assert (path / "snapshot" / "source.txt").exists()


def test_reparse_candidate_blocks_before_cleanup(job, monkeypatch):
    job_id, path = job
    original = cleanup._no_links

    def reject_source(candidate):
        if candidate.name == "source.txt":
            raise cleanup.CleanupError("synthetic reparse fixture")
        return original(candidate)

    monkeypatch.setattr(cleanup, "_no_links", reject_source)
    monkeypatch.setattr(cleanup, "cleanup_owned", lambda _: pytest.fail("must not invoke Docker"))
    with pytest.raises(cleanup.CleanupError):
        cleanup.cleanup_job(job_id, remove_snapshot=True)
    assert (path / "snapshot" / "source.txt").exists()


def test_hardlink_preserves_snapshot_and_external_original(job, tmp_path):
    job_id, path = job
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve")
    os.link(outside, path / "snapshot" / "linked.txt")
    with pytest.raises(cleanup.CleanupError):
        cleanup.cleanup_job(job_id, remove_snapshot=True)
    assert outside.read_text() == "preserve"
    assert (path / "snapshot" / "source.txt").exists()


def test_snapshot_is_rechecked_after_resource_cleanup(job, tmp_path, monkeypatch):
    job_id, path = job
    outside = tmp_path / "outside-after-cleanup.txt"
    outside.write_text("preserve after check")

    def changed_snapshot(_job_id):
        os.link(outside, path / "snapshot" / "injected-link.txt")
        return "CLEANED"

    monkeypatch.setattr(cleanup, "cleanup_owned", changed_snapshot)
    with pytest.raises(cleanup.CleanupError):
        cleanup.cleanup_job(job_id, remove_snapshot=True)
    assert outside.read_text() == "preserve after check"
    assert (path / "snapshot" / "source.txt").exists()


def test_repeated_snapshot_cleanup_is_safe(job):
    job_id, path = job
    assert cleanup.cleanup_job(job_id, remove_snapshot=True)["snapshot_status"] == "REMOVED"
    assert cleanup.cleanup_job(job_id, remove_snapshot=True)["snapshot_status"] == "ABSENT"
    assert (path / "build-context-manifest.json").exists()


def test_listing_is_bounded_and_exposes_only_job_ids_and_fixed_statuses(job, monkeypatch, capsys):
    job_id, path = job
    (path / "runtime-resources.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "owner": "v0.1",
                "cleaned": True,
                "password": "must-not-be-shown",
                "path": "private-source-location",
            }
        )
    )
    monkeypatch.setattr(cleanup, "cleanup_owned", lambda _: pytest.fail("listing must not invoke Docker"))
    assert cleanup.main(["--list"]) == 0
    output = capsys.readouterr().out
    assert "must-not-be-shown" not in output and "private-source-location" not in output
    assert json.loads(output) == [{"job_id": job_id, "status": "CLEANED_RECORDED"}]
    monkeypatch.setattr(cleanup, "MAX_LISTED_JOBS", 1)
    other = path.parent / uuid4().hex
    other.mkdir()
    assert len(cleanup.list_jobs()) == 1


def test_invalid_record_lists_attention_without_raw_value(job):
    job_id, path = job
    (path / "runtime-resources.json").write_text("secret invalid JSON")
    assert cleanup.list_jobs() == [{"job_id": job_id, "status": "NEEDS_ATTENTION"}]
