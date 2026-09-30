"""Loopback-only launcher with a bounded alternative-port search."""

import argparse
import socket
import subprocess
import sys

from .config import ROOT


def available_port(first: int = 18502) -> int:
    if not 1024 <= first <= 65515:
        raise ValueError("Port must be between 1024 and 65515.")
    for port in range(first, first + 20):
        if port == 18501:
            continue
        try:
            with socket.socket() as probe:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise OSError("No loopback port is available. Close this app's previous window or choose --port.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18502)
    args = parser.parse_args()
    try:
        port = available_port(args.port)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"AWS App Packager: http://127.0.0.1:{port}", flush=True)
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "streamlit_app.py"),
            "--server.address=127.0.0.1",
            f"--server.port={port}",
            "--browser.gatherUsageStats=false",
            "--server.headless=true",
        ],
        cwd=ROOT,
        shell=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
