"""The only production subprocess entry point: bounded output and no inherited secrets."""

import math
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .redaction import redact

MAX_OUTPUT = 65_536
MAX_PROGRESS_TAIL = 16_384
_HEAD_OUTPUT = 16_384
_OMITTED = "\n[MIDDLE OUTPUT OMITTED]\n"
_TAIL_OUTPUT = MAX_OUTPUT - _HEAD_OUTPUT - len(_OMITTED)
_PRIVATE_KEY_MARKER = re.compile(rb"-----(BEGIN|END) (?:[A-Z]+ )*PRIVATE KEY-----")
_INHERITED = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "LANG", "LC_ALL"}
_EXPLICIT = _INHERITED | {
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
    "PACK_HOME",
    "AWS_EC2_METADATA_DISABLED",
    "TF_IN_AUTOMATION",
    "TF_CLI_CONFIG_FILE",
    "CHECKPOINT_DISABLE",
}


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    output: str
    timed_out: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class ProcessProgress:
    """A caller-thread snapshot, containing only bounded, redacted complete records.

    ``last_output_at`` records received bytes, including a buffered partial record.
    ``output_lines`` counts completed CR/LF records (CRLF counts once), including
    suppressed records, plus a final unterminated record when the pipe closes.
    """

    elapsed_seconds: float
    timeout_seconds: float
    output_tail: str
    last_output_at: str | None
    output_lines: int
    running: bool
    returncode: int | None


def safe_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    home = config.WORK_DIR / "tool-home"
    for directory in (home, home / "docker", home / "pack", home / "tmp"):
        directory.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key.upper() in _INHERITED}
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "DOCKER_CONFIG": str(home / "docker"),
            "PACK_HOME": str(home / "pack"),
            "TEMP": str(home / "tmp"),
            "TMP": str(home / "tmp"),
            "TMPDIR": str(home / "tmp"),
            "AWS_EC2_METADATA_DISABLED": "true",
            "CHECKPOINT_DISABLE": "1",
            "TF_IN_AUTOMATION": "1",
        }
    )
    for key, value in (extra or {}).items():
        if key.upper() not in _EXPLICIT or "\x00" in value:
            raise ValueError("허용되지 않은 프로세스 환경설정입니다.")
        if key.upper() == "AWS_EC2_METADATA_DISABLED" and value != "true":
            raise ValueError("메타데이터 접근 차단을 해제할 수 없습니다.")
        if key.upper() == "DOCKER_HOST" and not value.startswith(("npipe://", "unix:///")):
            raise ValueError("로컬 Docker endpoint만 허용합니다.")
        env[key.upper()] = value
    return env


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Only the process tree launched by this invocation; no shared application cleanup.
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        if taskkill.is_file():
            try:
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=safe_environment(),
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class ProcessRunner:
    def run(
        self,
        args: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: float = 120,
        cancel: threading.Event | None = None,
        env: dict[str, str] | None = None,
        on_progress: Callable[[ProcessProgress], None] | None = None,
    ) -> ProcessResult:
        if not isinstance(args, list) or not args or any(not isinstance(a, str) or "\x00" in a for a in args):
            raise ValueError("명령은 NUL 없는 문자열 인자 배열이어야 합니다.")
        executable = Path(args[0])
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("확인된 실행 파일의 절대경로가 필요합니다.")
        if executable.suffix.lower() in {".cmd", ".bat", ".ps1"} or executable.stem.lower() in {
            "cmd",
            "powershell",
            "pwsh",
            "bash",
            "sh",
            "zsh",
            "aws",
        }:
            raise ValueError("셸 및 AWS CLI 실행은 지원하지 않습니다.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("유한한 양수 timeout이 필요합니다.")
        head: list[str] = []
        tail: deque[str] = deque()
        head_size = tail_size = 0
        truncated = False
        last_output_at: str | None = None
        output_lines = 0
        output_lock = threading.Lock()
        started = time.monotonic()

        def output_snapshot() -> str:
            return "".join(head) + (_OMITTED if truncated else "") + "".join(tail)

        def report(*, running: bool, returncode: int | None = None) -> None:
            if on_progress is None:
                return
            with output_lock:
                progress = ProcessProgress(
                    elapsed_seconds=max(0.0, time.monotonic() - started),
                    timeout_seconds=float(timeout),
                    output_tail=output_snapshot()[-MAX_PROGRESS_TAIL:],
                    last_output_at=last_output_at,
                    output_lines=output_lines,
                    running=running,
                    returncode=returncode,
                )
            try:
                on_progress(progress)
            except Exception:
                # A presentation callback must not interrupt process supervision.
                pass

        def append(line: str) -> None:
            nonlocal head_size, tail_size, truncated
            clean = redact(line, MAX_OUTPUT)
            with output_lock:
                take = min(len(clean), _HEAD_OUTPUT - head_size)
                if take:
                    head.append(clean[:take])
                    head_size += take
                    clean = clean[take:]
                if clean:
                    tail.append(clean)
                    tail_size += len(clean)
                while tail_size > _TAIL_OUTPUT:
                    truncated = True
                    oldest = tail.popleft()
                    excess = tail_size - _TAIL_OUTPUT
                    if len(oldest) > excess:
                        tail.appendleft(oldest[excess:])
                        tail_size -= excess
                    else:
                        tail_size -= len(oldest)

        if cancel is not None and cancel.is_set():
            append("실행 전 취소됨")
            report(running=False, returncode=-1)
            return ProcessResult(-1, "실행 전 취소됨", cancelled=True)

        try:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                env=safe_environment(env),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            message = redact(f"프로세스를 시작하지 못했습니다: {error}")
            append(message)
            report(running=False, returncode=-1)
            return ProcessResult(-1, message)

        started = time.monotonic()
        deadline = started + timeout
        report(running=True)

        def consume() -> None:
            nonlocal last_output_at, output_lines
            pending = bytearray()
            discarding = False
            private_key = False
            skip_lf = False

            def key_markers(record: bytes) -> bool:
                nonlocal private_key
                protected = private_key
                for marker in _PRIVATE_KEY_MARKER.finditer(record):
                    if marker[1] == b"BEGIN":
                        protected = private_key = True
                        append("[PRIVATE KEY REDACTED]\n")
                    else:
                        private_key = False
                return protected

            def complete_record(*, terminated: bool) -> None:
                nonlocal discarding, output_lines
                with output_lock:
                    output_lines += 1
                protected = key_markers(bytes(pending))
                if not discarding and not protected:
                    append(pending.decode("utf-8", errors="replace") + ("\n" if terminated else ""))
                pending.clear()
                discarding = False

            def add_fragment(fragment: bytes) -> None:
                nonlocal discarding
                pending.extend(fragment)
                if len(pending) > MAX_OUTPUT:
                    # Retain enough overlap to recognize a PEM marker split across
                    # chunks, even while the oversized record itself is suppressed.
                    key_markers(bytes(pending))
                    if not discarding:
                        append("[OVERSIZED OUTPUT LINE OMITTED]\n")
                    del pending[:-128]
                    discarding = True

            assert process.stdout is not None
            try:
                while chunk := process.stdout.read(4096):
                    with output_lock:
                        last_output_at = datetime.now(UTC).isoformat()
                    position = 0
                    for delimiter in re.finditer(rb"[\r\n]", chunk):
                        fragment = chunk[position:delimiter.start()]
                        if fragment:
                            skip_lf = False
                            add_fragment(fragment)
                        if delimiter[0] == b"\n" and skip_lf:
                            skip_lf = False
                        else:
                            complete_record(terminated=True)
                            skip_lf = delimiter[0] == b"\r"
                        position = delimiter.end()
                    if position < len(chunk):
                        skip_lf = False
                        add_fragment(chunk[position:])
                if pending or discarding:
                    complete_record(terminated=False)
            finally:
                process.stdout.close()

        reader = threading.Thread(target=consume, name="bounded-process-output", daemon=True)
        reader.start()
        next_progress = started + 1.0
        timed_out = cancelled = False
        while process.poll() is None:
            cancelled = cancel is not None and cancel.is_set()
            timed_out = time.monotonic() >= deadline
            if cancelled or timed_out:
                _stop_process(process)
                break
            if time.monotonic() >= next_progress:
                report(running=True)
                next_progress = time.monotonic() + 1.0
            time.sleep(0.05)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
        with output_lock:
            joined = output_snapshot()
        report(running=False, returncode=process.returncode)
        return ProcessResult(process.returncode, joined, timed_out=timed_out, cancelled=cancelled)
