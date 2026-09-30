import http.server
import json
import threading
import time
from unittest.mock import Mock

from aws_app_packager import preflight, runtime_check
from aws_app_packager.models import RuntimeConditions
from aws_app_packager.process_runner import ProcessResult


def test_remote_docker_context_never_reaches_daemon(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(preflight, "find_tool", lambda name: "docker.exe" if name == "docker" else None)
    monkeypatch.setenv("DOCKER_HOST", "ssh://untrusted-server")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    runner = Mock()
    state = preflight.check_environment(runner)
    assert not state["docker_ready"]
    assert state["blockers"]
    runner.run.assert_not_called()


def test_local_daemon_fields_are_required(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(preflight, "WORK_DIR", tmp_path)
    monkeypatch.setattr(preflight, "find_tool", lambda name: "docker.exe" if name == "docker" else None)
    monkeypatch.setenv("DOCKER_HOST", "npipe:////./pipe/docker_engine")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    runner = Mock()
    runner.run.return_value = ProcessResult(
        0, json.dumps({"Version": "test", "Os": "windows", "Arch": "amd64"})
    )
    assert not preflight.check_environment(runner)["docker_ready"]
    runner.run.return_value = ProcessResult(
        0, json.dumps({"Version": "test", "Os": "linux", "Arch": "amd64"})
    )
    state = preflight.check_environment(runner)
    assert state["docker_ready"]
    args, kwargs = runner.run.call_args
    assert "--host" in args[0] and "--config" in args[0]
    assert kwargs["env"]["DOCKER_HOST"] == "npipe:////./pipe/docker_engine"


def test_context_inspection_failure_is_not_misreported_as_remote(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(preflight, "find_tool", lambda name: "docker.exe" if name == "docker" else None)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    runner = Mock()
    runner.run.return_value = ProcessResult(1, "failed to read local context metadata")
    state = preflight.check_environment(runner)
    assert not state["docker_ready"]
    assert "context 정보를 읽을 수 없습니다" in state["blockers"][0]
    assert "로컬 named pipe" not in state["blockers"][0]
    assert state["diagnostics"] == ["failed to read local context metadata"]


class LocalHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        try:
            if self.path == "/slow":
                for _ in range(10):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.04)
            else:
                self.wfile.write(b"healthy-marker")
        except (OSError, ConnectionError):
            pass

    def log_message(self, *_):
        pass


def test_real_loopback_http_ignores_proxy_redirects_and_bounds_slow_body(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        conditions = RuntimeConditions(
            container_port=8080, health_path="/health", expected_marker="healthy-marker"
        )
        assert runtime_check.probe_http(port, conditions)[0]
        conditions.health_path = "/redirect"
        result = runtime_check.probe_http(port, conditions)
        assert not result[0] and result[1] == 302
        conditions.health_path = "/slow"
        before = time.monotonic()
        assert not runtime_check.probe_http(port, conditions, timeout=0.12)[0]
        assert time.monotonic() - before < 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
