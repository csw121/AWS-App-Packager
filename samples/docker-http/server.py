"""Authored fixed routes; never serves files, credentials, or external services."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            data, status = {"status": "UP", "marker": "packager-python-v1"}, 200
        elif self.path == "/":
            data, status = {"app": "AWS App Packager sample", "runtime": "Python stdlib"}, 200
        else:
            data, status = {"error": "not found"}, 404
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
