import sys
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from aws_app_packager import config
from aws_app_packager.process_runner import (
    MAX_OUTPUT,
    MAX_PROGRESS_TAIL,
    ProcessProgress,
    ProcessRunner,
    safe_environment,
)
from aws_app_packager.redaction import has_secret_candidate, redact


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / ".work")


def test_secret_redaction_keeps_public_and_loopback_urls():
    text = (
        "password=really-secret\napi_token: 'a-secret-value'\n"
        "jdbc:postgresql://person:password@private/db\n"
        "https://docs.docker.com/engine/security/ http://127.0.0.1:1234/health\n"
        r"C:\Users\Alice\secret-folder\file.py"
    )
    result = redact(text)
    assert "really-secret" not in result
    assert "a-secret-value" not in result
    assert "private/db" not in result
    assert "Alice" not in result
    assert "https://docs.docker.com/engine/security/" in result
    assert "http://127.0.0.1:1234/health" in result


def test_private_key_and_limit():
    text = "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"
    assert "private-material" not in redact(text)
    assert len(redact("x" * 1000, max_length=80)) <= 80
    assert redact("x", max_length=0) == ""
    assert len(redact("longer", max_length=2)) <= 2
    assert "Alice" not in redact(r'error: "C:\\Users\\Alice\\Private Folder\\config"')
    assert not has_secret_candidate("API_TOKEN\npassword=${PASSWORD}\n")


@pytest.mark.parametrize(
    "text",
    [
        "ENV PASSWORD actual-private-value",
        "ENV DATABASE_PASSWORD actual private value with spaces",
        "#7 0.2 ENV API_TOKEN actual-private-value",
        "<password>actual-private-value</password>",
        '<db:password encoding="plain">actual-private-value</db:password>',
        "<apiKey>\nactual-private-value\n</apiKey>",
    ],
)
def test_plain_docker_env_and_xml_secret_values_are_blocked_and_masked(text):
    assert has_secret_candidate(text)
    assert "actual-private-value" not in redact(text)
    assert "actual private value" not in redact(text)


@pytest.mark.parametrize(
    "text", ["ENV PASSWORD ${PASSWORD}", "<password>${PASSWORD}</password>", "<password> </password>"]
)
def test_docker_env_and_xml_placeholders_are_not_secret_values(text):
    assert not has_secret_candidate(text)


def test_large_single_word_is_not_a_secret_assignment():
    assert not has_secret_candidate("a" * 100_000)


def test_environment_does_not_inherit_cloud_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "do-not-copy")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "do-not-copy")
    monkeypatch.setenv("CUSTOM_APP_SECRET", "do-not-copy")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2375")
    monkeypatch.setenv("HTTPS_PROXY", "http://private-proxy")
    env = safe_environment()
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AZURE_CLIENT_SECRET" not in env
    assert "CUSTOM_APP_SECRET" not in env
    assert "DOCKER_HOST" not in env
    assert "HTTPS_PROXY" not in env
    assert env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert env["HOME"] == str(config.WORK_DIR / "tool-home")
    with pytest.raises(ValueError):
        safe_environment({"AWS_ACCESS_KEY_ID": "no"})
    with pytest.raises(ValueError):
        safe_environment({"DOCKER_HOST": "tcp://remote:2375"})


def test_runner_treats_argument_as_data_and_redacts_output():
    payload = "ordinary & echo SHOULD_NOT_RUN; $(bad)"
    result = ProcessRunner().run(
        [
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1]); print('password=hide-this-value')",
            payload,
        ]
    )
    assert result.returncode == 0
    assert payload in result.output
    assert "hide-this-value" not in result.output


def test_process_output_is_bounded_and_long_lines_omitted():
    result = ProcessRunner().run([sys.executable, "-c", "print('x' * 1000000)"])
    assert result.returncode == 0
    assert len(result.output) <= MAX_OUTPUT
    assert "OMITTED" in result.output


def test_process_multiline_private_key_is_redacted():
    code = "print('-----BEGIN PRIVATE KEY-----\\nprivate-material\\n-----END PRIVATE KEY-----')"
    result = ProcessRunner().run([sys.executable, "-c", code])
    assert "private-material" not in result.output


def test_long_process_output_preserves_start_and_final_failure_with_redaction():
    code = (
        "print('BUILD_STARTED'); "
        "[print('ordinary-progress-' + str(i) + 'x' * 80) for i in range(2000)]; "
        "print('password=must-stay-hidden'); "
        "print('-----BEGIN PRIVATE KEY-----\\nprivate-material\\n-----END PRIVATE KEY-----'); "
        "print('FINAL_FAILURE: dependency unavailable'); raise SystemExit(3)"
    )
    result = ProcessRunner().run([sys.executable, "-c", code])
    assert result.returncode == 3
    assert result.output.startswith("BUILD_STARTED")
    assert result.output.rstrip().endswith("FINAL_FAILURE: dependency unavailable")
    assert "MIDDLE OUTPUT OMITTED" in result.output
    assert "must-stay-hidden" not in result.output
    assert "private-material" not in result.output
    assert len(result.output) <= MAX_OUTPUT


def test_timeout_stops_owned_process():
    result = ProcessRunner().run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)
    assert result.timed_out and result.returncode != 0


def test_cancel_before_execution():
    event = threading.Event()
    event.set()
    result = ProcessRunner().run([sys.executable, "-c", "raise RuntimeError()"], cancel=event)
    assert result.cancelled


def test_cancellation_stops_owned_process():
    event = threading.Event()
    timer = threading.Timer(0.15, event.set)
    timer.start()
    try:
        result = ProcessRunner().run([sys.executable, "-c", "import time; time.sleep(5)"], cancel=event)
    finally:
        timer.cancel()
    assert result.cancelled and result.returncode != 0


@pytest.mark.parametrize(
    "args", ["python -c bad", ["python", "-c", "bad"], [], [sys.executable, "bad\x00arg"]]
)
def test_runner_rejects_shell_strings_relative_executable_and_nul(args):
    with pytest.raises(ValueError):
        ProcessRunner().run(args)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_runner_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        ProcessRunner().run([sys.executable, "-c", "pass"], timeout=timeout)


def test_progress_is_frozen_and_live_on_calling_thread_before_child_exits(tmp_path):
    finished = tmp_path / "child-finished"
    events = []
    threads = []
    live_before_exit = []
    caller_thread = threading.get_ident()

    def observe(progress):
        events.append(progress)
        threads.append(threading.get_ident())
        if progress.running and "READY" in progress.output_tail:
            live_before_exit.append(not finished.exists())

    code = (
        "import pathlib,sys,time; print('READY', flush=True); time.sleep(1.4); "
        "pathlib.Path(sys.argv[1]).write_text('done'); print('FINISHED')"
    )
    result = ProcessRunner().run(
        [sys.executable, "-c", code, str(finished)], timeout=5, on_progress=observe
    )
    assert result.returncode == 0
    assert live_before_exit and all(live_before_exit)
    assert set(threads) == {caller_thread}
    assert all(isinstance(event, ProcessProgress) for event in events)
    assert events[0].running and events[0].returncode is None
    assert events[0].output_tail == "" and events[0].last_output_at is None
    assert events[-1].running is False and events[-1].returncode == 0
    assert events[-1].output_lines == 2
    assert events[-1].output_tail == result.output
    assert all(event.timeout_seconds == 5.0 for event in events)
    assert [event.elapsed_seconds for event in events] == sorted(event.elapsed_seconds for event in events)
    assert datetime.fromisoformat(events[-1].last_output_at).utcoffset().total_seconds() == 0
    with pytest.raises(FrozenInstanceError):
        events[-1].running = True


def test_quiet_process_emits_heartbeat_with_elapsed_time():
    events = []
    result = ProcessRunner().run(
        [sys.executable, "-c", "import time; time.sleep(1.3)"],
        timeout=5,
        on_progress=events.append,
    )
    assert result.returncode == 0
    assert any(event.running and event.elapsed_seconds >= 1 for event in events)
    assert all(event.output_tail == "" and event.output_lines == 0 for event in events)
    assert all(event.last_output_at is None for event in events)
    assert not events[-1].running


def test_cr_progress_records_are_live_and_crlf_counts_once():
    events = []
    code = (
        "import os,time; os.write(1, b'phase-one\\r'); time.sleep(1.2); "
        "os.write(1, b'\\nphase-two\\rphase-three\\r\\nend')"
    )
    result = ProcessRunner().run([sys.executable, "-c", code], on_progress=events.append)
    assert result.returncode == 0
    assert any(event.running and event.output_tail == "phase-one\n" for event in events)
    assert events[-1].output_lines == 4
    assert result.output == "phase-one\nphase-two\nphase-three\nend"


def test_partial_secret_is_hidden_until_complete_record():
    events = []
    code = (
        "import sys,time; sys.stdout.write('pass'); sys.stdout.flush(); time.sleep(1.15); "
        "sys.stdout.write('word=hidden-partial-value'); sys.stdout.flush(); time.sleep(1.15); "
        "sys.stdout.write('\\nSAFE\\n'); sys.stdout.flush()"
    )
    result = ProcessRunner().run([sys.executable, "-c", code], on_progress=events.append)
    partial_events = [event for event in events if event.running and event.last_output_at is not None]
    assert len(partial_events) >= 2
    assert all(event.output_tail == "" and event.output_lines == 0 for event in partial_events)
    assert all("hidden-partial-value" not in event.output_tail for event in events)
    assert "hidden-partial-value" not in result.output
    assert "SAFE" in events[-1].output_tail and events[-1].output_lines == 2


def test_split_private_key_is_never_exposed_in_live_progress():
    events = []
    code = (
        "import sys,time; "
        "sys.stdout.write('-----BE'); sys.stdout.flush(); time.sleep(.05); "
        "sys.stdout.write('GIN RSA PRIVATE KEY-----\\rprivate-material-one\\r'); "
        "sys.stdout.flush(); time.sleep(1.2); "
        "sys.stdout.write('private-material-two\\n-----END RSA PRIVATE KEY-----\\nSAFE\\n'); "
        "sys.stdout.flush()"
    )
    result = ProcessRunner().run([sys.executable, "-c", code], on_progress=events.append)
    assert any(event.running and "PRIVATE KEY REDACTED" in event.output_tail for event in events)
    assert all("private-material" not in event.output_tail for event in events)
    assert "private-material" not in result.output
    assert events[-1].output_lines == 5 and "SAFE" in result.output


def test_oversized_record_preserves_private_key_suppression():
    events = []
    code = (
        "import sys; sys.stdout.write('-----BEGIN PRIVATE KEY-----' + 'x' * 100000); "
        "sys.stdout.write('\\nprivate-material\\n-----END PRIVATE KEY-----\\nSAFE\\n')"
    )
    result = ProcessRunner().run([sys.executable, "-c", code], on_progress=events.append)
    assert "private-material" not in result.output
    assert all("private-material" not in event.output_tail for event in events)
    assert "SAFE" in result.output


def test_live_tail_is_bounded_redacted_and_final_output_keeps_head_and_tail():
    events = []
    code = (
        "import time; print('BUILD_STARTED', flush=True); "
        "[print('ordinary-progress-' + str(i) + 'x' * 80) for i in range(2000)]; "
        "print('password=live-secret-value', flush=True); time.sleep(1.2); "
        "print('FINAL_FAILURE'); raise SystemExit(3)"
    )
    result = ProcessRunner().run([sys.executable, "-c", code], on_progress=events.append)
    assert any(event.running and len(event.output_tail) == MAX_PROGRESS_TAIL for event in events)
    assert all(len(event.output_tail) <= MAX_PROGRESS_TAIL for event in events)
    assert all("live-secret-value" not in event.output_tail for event in events)
    assert result.output.startswith("BUILD_STARTED") and "MIDDLE OUTPUT OMITTED" in result.output
    assert len(result.output) <= MAX_OUTPUT
    assert events[-1].output_tail.endswith("FINAL_FAILURE\n") and events[-1].returncode == 3


def test_callback_exceptions_do_not_break_timeout_supervision():
    events = []

    def failing_callback(progress):
        events.append(progress)
        raise RuntimeError("presentation failed")

    result = ProcessRunner().run(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=1.2,
        on_progress=failing_callback,
    )
    assert result.timed_out and result.returncode != 0
    assert any(event.running and event.elapsed_seconds >= 1 for event in events)
    assert not events[-1].running and events[-1].returncode == result.returncode


def test_callback_can_cancel_even_if_it_raises():
    cancel = threading.Event()
    events = []

    def cancelling_callback(progress):
        events.append(progress)
        if progress.running and progress.elapsed_seconds >= 1:
            cancel.set()
        raise RuntimeError("journal unavailable")

    result = ProcessRunner().run(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=5,
        cancel=cancel,
        on_progress=cancelling_callback,
    )
    assert result.cancelled and result.returncode != 0
    assert not result.timed_out
    assert not events[-1].running and events[-1].returncode == result.returncode


def test_pre_cancel_reports_only_final_state():
    cancel = threading.Event()
    cancel.set()
    events = []
    result = ProcessRunner().run(
        [sys.executable, "-c", "raise RuntimeError('must not run')"],
        cancel=cancel,
        on_progress=events.append,
    )
    assert result.cancelled
    assert len(events) == 1 and not events[0].running and events[0].returncode == -1
    assert events[0].last_output_at is None and events[0].output_lines == 0


def test_spawn_failure_reports_only_redacted_final_state(monkeypatch):
    events = []

    def fail_spawn(*args, **kwargs):
        raise OSError("password=spawn-secret")

    monkeypatch.setattr("aws_app_packager.process_runner.subprocess.Popen", fail_spawn)
    result = ProcessRunner().run([sys.executable, "-c", "pass"], on_progress=events.append)
    assert result.returncode == -1
    assert len(events) == 1 and not events[0].running
    assert events[0].returncode == -1 and events[0].last_output_at is None
    assert "spawn-secret" not in events[0].output_tail and "spawn-secret" not in result.output
