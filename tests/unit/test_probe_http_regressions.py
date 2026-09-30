"""Real local HTTP protocol checks; no Docker, external app, or cloud commands."""

import http.server
import threading

import pytest

from aws_app_packager.models import RuntimeConditions
from aws_app_packager.runtime_check import probe_http


@pytest.mark.parametrize("protocol", ["HTTP/1.0", "HTTP/1.1"])
@pytest.mark.parametrize("length_mode", ["exact", "omitted", "truncated"])
def test_real_closing_http_response_preserves_status_and_marker(protocol, length_mode):
    body = b'{"status":"healthy-marker"}'

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = protocol

        def do_GET(self):
            self.send_response(200)
            self.send_header("Connection", "close")
            if length_mode != "omitted":
                declared = len(body) + (5 if length_mode == "truncated" else 0)
                self.send_header("Content-Length", str(declared))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conditions = RuntimeConditions(
            container_port=8080, health_path="/health", expected_marker="healthy-marker",
        )
        passed, status, message = probe_http(server.server_address[1], conditions)
        assert passed is (length_mode != "truncated")
        assert status == 200
        assert "healthy-marker" not in message
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
