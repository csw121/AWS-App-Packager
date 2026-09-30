import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from aws_app_packager import jobs
from aws_app_packager.jobs import BusyError, JobManager, JobPersistenceError
from aws_app_packager.models import RuntimeConditions, fingerprint
from aws_app_packager.presets import PRESETS
from aws_app_packager.process_runner import ProcessProgress


@pytest.mark.parametrize(
    "path", ["https://example.com", "//host/path", "/a?token=x", "/../x", "/a#x", "/%2fhost"]
)
def test_health_path_rejects_external_and_token_targets(path):
    with pytest.raises(ValidationError):
        RuntimeConditions(container_port=8080, health_path=path)


@pytest.mark.parametrize("port", [-1, 0, 65536])
def test_invalid_port(port):
    with pytest.raises(ValidationError):
        RuntimeConditions(container_port=port, health_path="/health")


def test_no_secret_environment():
    with pytest.raises(ValidationError):
        RuntimeConditions(container_port=8080, health_path="/", environment={"DB_PASSWORD": "not-real"})


def test_preset_or_port_change_changes_identity():
    original = RuntimeConditions(container_port=8080, health_path="/health")
    small = original.model_copy(update={"preset": "small"})
    other_port = original.model_copy(update={"container_port": 9000})
    assert len({fingerprint(v) for v in (original, small, other_port)}) == 3
    assert [(p.vcpu, p.memory_mib, p.cpu_units, p.desired_count) for p in PRESETS.values()] == [
        (0.5, 1024, 512, 1),
        (1, 2048, 1024, 1),
        (2, 4096, 2048, 1),
    ]


def test_job_single_instance_lock_and_cancel(tmp_path):
    first, second = JobManager(tmp_path), JobManager(tmp_path)
    entered = threading.Event()

    def slow(_job, cancel, stage):
        stage("이미지 생성")
        entered.set()
        cancel.wait(4)
        return {}

    job_id = first.start(slow, kind="test")
    assert entered.wait(2)
    with pytest.raises(BusyError):
        first.start(slow, kind="duplicate")
    with pytest.raises(BusyError):
        second.start(slow, kind="duplicate")
    first.cancel()
    first.wait()
    assert first.snapshot()["status"] == "CANCELLED"
    record = json.loads((tmp_path / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
    assert record["events"] == ["이미지 생성"]
    second.start(lambda *_: {}, kind="after_release")
    second.wait()
    assert second.snapshot()["status"] == "DONE"


def test_job_error_not_fake_success_and_log_is_redacted(tmp_path):
    manager = JobManager(tmp_path)

    def broken(*_):
        raise RuntimeError("password=not-a-real-secret-value")

    manager.start(broken, kind="failure")
    manager.wait()
    assert manager.snapshot()["status"] == "FAILED"
    assert "not-a-real-secret-value" not in manager.snapshot()["error"]


def assert_lock_released(directory):
    following = JobManager(directory)
    following.start(lambda *_: {}, kind="lock_release_probe")
    following.wait()
    assert following.snapshot()["status"] == "DONE"


def test_initial_save_failure_does_not_execute_action_or_leak_lock(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    action = Mock()
    monkeypatch.setattr(manager, "_save", Mock(side_effect=OSError("password=private-test-value")))
    with pytest.raises(RuntimeError, match="작업 기록"):
        manager.start(action, kind="save_failure")
    action.assert_not_called()
    state = manager.snapshot()
    assert state["status"] == "FAILED" and state["error_kind"] == "PERSISTENCE_FAILED"
    assert state["finished_at"] and "private-test-value" not in state["error"]
    manager.wait()  # An unstarted thread must not make wait() raise.
    assert_lock_released(tmp_path)


def test_thread_start_failure_persists_failure_and_releases_lock(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    action = Mock()
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", Mock(side_effect=RuntimeError("cannot start test thread")))
        with pytest.raises(RuntimeError, match="cannot start"):
            manager.start(action, kind="thread_failure")
    action.assert_not_called()
    state = manager.snapshot()
    assert state["status"] == "FAILED" and state["finished_at"]
    record = json.loads((tmp_path / "jobs" / f"{state['job_id']}.json").read_text(encoding="utf-8"))
    assert record["status"] == "FAILED"
    manager.wait()
    assert_lock_released(tmp_path)


def test_final_save_failure_remains_visible_and_releases_worker_lock(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    original_save = manager._save
    failures = []

    def save_initial_only():
        if manager.snapshot()["status"] == "RUNNING":
            original_save()
        else:
            raise OSError("password=private-final-write-value")

    monkeypatch.setattr(manager, "_save", save_initial_only)
    monkeypatch.setattr(threading, "excepthook", failures.append)
    manager.start(lambda *_: {}, kind="final_write_failure")
    manager.wait()
    state = manager.snapshot()
    assert state["status"] == "FAILED" and state["error_kind"] == "PERSISTENCE_FAILED"
    assert "private-final-write-value" not in state["error"]
    assert state["finished_at"] and not failures
    assert_lock_released(tmp_path)


def test_stage_save_failure_is_a_failure_even_if_cancellation_was_requested(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    original_save = manager._save

    def fail_stage_save():
        if manager.snapshot().get("events"):
            raise OSError("stage write failed")
        original_save()

    monkeypatch.setattr(manager, "_save", fail_stage_save)

    def action(_job, cancel, stage):
        cancel.set()
        stage("실패하는 단계 저장")

    manager.start(action, kind="stage_write_failure")
    manager.wait()
    state = manager.snapshot()
    assert state["status"] == "FAILED" and state["error_kind"] == "PERSISTENCE_FAILED"
    assert_lock_released(tmp_path)


@pytest.mark.parametrize("cleanup", ["NEEDS_ATTENTION", "NOT_NEEDED", None])
def test_http_pass_with_incomplete_cleanup_is_failed_job(tmp_path, cleanup):
    manager = JobManager(tmp_path)
    check = SimpleNamespace(status="PASS", cleanup_status=cleanup)
    manager.start(lambda *_: {"check": check}, kind="incomplete_cleanup")
    manager.wait()
    state = manager.snapshot()
    assert state["status"] == "FAILED"
    assert state["error_kind"] == "CLEANUP_INCOMPLETE"
    assert manager.result()["check"].status == "PASS"  # Preserve actual HTTP observation for diagnostics.
    assert_lock_released(tmp_path)


def test_http_pass_with_verified_cleanup_is_done(tmp_path):
    manager = JobManager(tmp_path)
    check = SimpleNamespace(status="PASS", cleanup_status="CLEANED")
    manager.start(lambda *_: {"check": check}, kind="clean_http_pass")
    manager.wait()
    assert manager.snapshot()["status"] == "DONE"


def progress(*, elapsed=0.0, output="", lines=0, running=True, returncode=None):
    return ProcessProgress(
        elapsed_seconds=elapsed, timeout_seconds=60.0, output_tail=output,
        last_output_at=datetime.now(UTC).isoformat() if output else None,
        output_lines=lines, running=running, returncode=returncode,
    )


@pytest.fixture
def live_manager(tmp_path):
    manager = JobManager(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def action(*_):
        entered.set()
        assert release.wait(5)
        return {}

    manager.start(action, kind="live_progress")
    assert entered.wait(2)
    try:
        yield manager
    finally:
        release.set()
        manager.wait()


def test_live_progress_is_observable_and_persisted_before_worker_finishes(live_manager, tmp_path):
    manager = live_manager
    manager.stage("이미지 생성")
    observation = progress(elapsed=2.5, output="password=private-progress-value\nMaven output", lines=2)
    manager.process_progress(observation)
    state = manager.snapshot()
    assert state["status"] == "RUNNING"
    assert "finished_at" not in state
    assert state["process"]["elapsed_seconds"] == 2.5
    assert state["process"]["timeout_seconds"] == 60.0
    assert state["process"]["last_output_at"] == observation.last_output_at
    assert state["process"]["output_lines"] == 2
    assert state["process"]["running"] and state["process"]["returncode"] is None
    started = datetime.fromisoformat(state["process"]["started_at"])
    updated = datetime.fromisoformat(state["updated_at"])
    assert (updated - started).total_seconds() == 2.5
    assert datetime.fromisoformat(state["stage_started_at"]).tzinfo is not None
    assert "private-progress-value" not in json.dumps(state)
    assert state["live_log"] == state["process"]["output_tail"]
    journal = (tmp_path / "jobs" / f"{state['job_id']}.json").read_text(encoding="utf-8")
    assert "private-progress-value" not in journal
    assert json.loads(journal)["process"] == state["process"]


def test_command_labels_retain_until_command_or_stage_changes(live_manager, monkeypatch):
    manager = live_manager
    monkeypatch.setattr(jobs, "infer_phase", lambda text: "Maven 빌드" if "phase" in text else None)
    monkeypatch.setattr(
        jobs, "infer_performance_note", lambda text: "최초 다운로드" if "note" in text else None,
    )
    manager.stage("이미지 생성")
    manager.process_progress(progress(elapsed=0, output="phase note", lines=1))
    command_start = manager.snapshot()["process"]["started_at"]
    stage_start = manager.snapshot()["stage_started_at"]
    manager.process_progress(progress(elapsed=1, output="later output", lines=2))
    manager.stage("이미지 생성")
    state = manager.snapshot()
    assert state["process"]["phase_label"] == "Maven 빌드"
    assert state["process"]["performance_note"] == "최초 다운로드"
    assert state["process"]["started_at"] == command_start
    assert state["stage_started_at"] == stage_start
    assert state["events"] == ["이미지 생성"]
    manager.process_progress(progress(elapsed=2, output="finished", lines=3, running=False, returncode=0))
    manager.process_progress(progress())
    state = manager.snapshot()
    assert state["process"]["phase_label"] is None
    assert state["process"]["performance_note"] is None
    assert state["process"]["last_output_at"] is None
    assert state["process"]["output_lines"] == 0
    assert state["live_log"] == "finished"
    manager.stage("결과 확인")
    state = manager.snapshot()
    assert state["process"] is None and state["live_log"] == "finished"
    assert [row["stage"] for row in state["stage_history"]] == ["준비", "이미지 생성", "결과 확인"]
    assert all(set(row) == {"stage", "started_at"} for row in state["stage_history"])


def test_buffered_byte_timestamp_is_kept_without_publishing_incomplete_line(live_manager):
    manager = live_manager
    manager.process_progress(progress(output="previous safe line", lines=1))
    arrived = datetime.now(UTC).isoformat()
    manager.process_progress(ProcessProgress(
        elapsed_seconds=1.0, timeout_seconds=60.0, output_tail="",
        last_output_at=arrived, output_lines=1, running=True, returncode=None,
    ))
    state = manager.snapshot()
    assert state["process"]["last_output_at"] == arrived
    assert state["process"]["output_tail"] == ""
    assert state["live_log"] == "previous safe line"


def test_progress_persistence_is_throttled_but_live_memory_updates(live_manager, monkeypatch):
    manager = live_manager
    tick = [0.0]
    monkeypatch.setattr(jobs, "time", SimpleNamespace(monotonic=lambda: tick[0]))
    saved = Mock(wraps=manager._save)
    monkeypatch.setattr(manager, "_save", saved)
    manager.process_progress(progress())
    assert saved.call_count == 1
    for tick[0] in (0.2, 0.4, 0.99):
        manager.process_progress(progress(elapsed=tick[0], output=f"line {tick[0]}", lines=1))
    assert saved.call_count == 1
    assert live_manager.snapshot()["process"]["elapsed_seconds"] == 0.99
    tick[0] = 1.0
    manager.process_progress(progress(elapsed=1, output="one second", lines=2))
    assert saved.call_count == 2
    tick[0] = 1.1
    manager.process_progress(progress(elapsed=1.1, output="end", lines=3, running=False, returncode=7))
    assert saved.call_count == 3
    assert manager.snapshot()["process"]["returncode"] == 7


def test_stage_history_and_redacted_live_journal_remain_bounded(live_manager, tmp_path):
    manager = live_manager
    for index in range(30):
        manager.stage(f"단계 {index} " + "x" * 600)
    manager.process_progress(progress(
        output="password=private-bounded-value\n" + "safe\n" * 8000, lines=8001,
    ))
    state = manager.snapshot()
    assert len(state["stage_history"]) == len(state["events"]) == 24
    assert all(len(row["stage"]) <= 512 for row in state["stage_history"])
    assert len(state["live_log"]) <= 16_384
    assert len(state["process"]["output_tail"]) <= 16_384
    journal = (tmp_path / "jobs" / f"{state['job_id']}.json").read_text(encoding="utf-8")
    assert "private-bounded-value" not in journal
    assert len(journal) < 70_000


def test_cancel_records_request_before_signalling_and_retry_starts_fresh(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    entered = threading.Event()

    def action(_job, cancel, stage):
        stage("이미지 생성")
        manager.process_progress(progress(output="old command", lines=1))
        entered.set()
        cancel.wait(4)
        return {}

    manager.start(action, kind="cancelled_progress")
    assert entered.wait(2)
    original_save = manager._save
    observations = []

    def save():
        if manager.snapshot().get("cancel_requested_at"):
            observations.append((manager._cancel.is_set(), manager.snapshot()["cancel_requested_at"]))
        original_save()

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_save", save)
        manager.cancel()
        manager.wait()
    assert observations[0][0] is False
    state = manager.snapshot()
    assert state["status"] == "CANCELLED"
    assert datetime.fromisoformat(state["cancel_requested_at"]).tzinfo is not None
    assert state["process"]["running"] is False
    release = threading.Event()
    manager.start(lambda *_: release.wait(4), kind="retry")
    try:
        state = manager.snapshot()
        assert state["status"] == "RUNNING"
        assert state["process"] is None and state["live_log"] == ""
        assert "cancel_requested_at" not in state and "finished_at" not in state
        assert state["events"] == [] and len(state["stage_history"]) == 1
    finally:
        release.set()
        manager.wait()


def test_cancel_journal_failure_still_signals_and_cannot_turn_into_success(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    entered = threading.Event()

    def action(_job, cancel, _stage):
        entered.set()
        assert cancel.wait(4)
        return {}

    manager.start(action, kind="cancel_write_failure")
    assert entered.wait(2)
    monkeypatch.setattr(manager, "_save", Mock(side_effect=OSError("password=private-cancel-value")))
    manager.cancel()
    manager.wait()
    state = manager.snapshot()
    assert manager._cancel.is_set() and state["cancel_requested_at"]
    assert state["status"] == "FAILED" and state["error_kind"] == "PERSISTENCE_FAILED"
    assert state["finished_at"] and "private-cancel-value" not in json.dumps(state)
    assert_lock_released(tmp_path)


def test_callback_journal_failure_cancels_even_if_runner_swallows_callback_error(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    original_save = manager._save

    def save():
        if manager.snapshot().get("process"):
            raise OSError("progress journal unavailable")
        original_save()

    monkeypatch.setattr(manager, "_save", save)

    def action(_job, cancel, stage):
        try:
            manager.process_progress(progress(output="safe line", lines=1))
        except JobPersistenceError:
            pass  # ProcessRunner isolates callbacks but still observes the same cancel Event.
        assert cancel.is_set()
        stage("자원 정리")
        return {}

    manager.start(action, kind="progress_write_failure")
    manager.wait()
    state = manager.snapshot()
    assert state["status"] == "FAILED" and state["error_kind"] == "PERSISTENCE_FAILED"
    assert state["finished_at"]
    assert_lock_released(tmp_path)


def test_stale_progress_and_stage_callbacks_cannot_change_finished_job(tmp_path):
    manager = JobManager(tmp_path)
    manager.start(lambda *_: {}, kind="complete")
    manager.wait()
    before = manager.snapshot()
    manager.process_progress(progress(output="stale command", lines=1))
    manager.stage("늦게 도착한 단계")
    assert manager.snapshot() == before
