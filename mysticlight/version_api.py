# SPDX-License-Identifier: GPL-3.0-or-later
"""realm-sigil /api/version endpoint (CLAUDE.md realm-sigil rule)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_START = time.time()
_START_ISO = datetime.now(timezone.utc).isoformat()
_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", _PROJECT, *args], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def build_version() -> dict:
    sys.path.insert(0, os.path.expanduser("~/Projects/realm-sigil/python"))
    try:
        from realm_sigil import version_dict
    finally:
        sys.path.pop(0)
    return version_dict(
        "mysticlight", "MSI Mystic Light → Home Assistant MQTT bridge",
        "fantasy", "https://github.com/jphein/mysticlight",
        hash=_git("rev-parse", "--short", "HEAD") or "dev",
        branch=_git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        dirty=bool(_git("status", "--porcelain")),
        built=_START_ISO, started=_START_ISO, uptime=int(time.time() - _START),
        runtime="python%d.%d" % sys.version_info[:2],
        host=socket.gethostname(), pid=os.getpid(),
    )


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/version":
            self.send_error(404)
            return
        body = json.dumps(build_version()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_in_thread(port: int) -> tuple:
    srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
