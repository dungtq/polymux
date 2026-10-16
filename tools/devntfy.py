#!/usr/bin/env python3
"""Tiny fake ntfy server for the dev pm instance: `devntfy.py <port> [--project PATH]`.

Serves `GET /<topic>/json?poll=1` as JSON lines (topic `agent` has fixture items, any other topic is empty).
Binds 127.0.0.1 only. No state, no secrets.
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def items(project: str) -> list[dict]:
    now = int(time.time())
    return [
        {"id": "devntfy01", "time": now - 120, "event": "message", "topic": "agent",
         "title": "claude-7cd08a - Fix the login form", "message": "Fixed the form and added a test. All green.",
         "tags": [project], "priority": 3},
        {"id": "devntfy02", "time": now - 900, "event": "message", "topic": "agent",
         "title": "codex-3fa91c - Refactor parser", "message": "Refactor done, 3 files touched.", "tags": ["/srv/other"],
         "priority": 3},
        {"id": "devntfy03", "time": now - 3600, "event": "message", "topic": "agent", "priority": 4,
         "title": "claude-7cd08a - " + "An extremely long task title that keeps going " * 4,
         "message": "A very long result message that must be clipped by the inbox row. " * 12,
         "tags": [project, "extra-tag"]},
        {"id": "devntfy04", "time": now - 7200, "event": "message", "topic": "agent",
         "title": "devin-5be204 - Failed to build", "message": "Build failed: missing dependency.", "tags": [], "priority": 5},
        {"id": "devntfy05", "time": now - 300, "event": "message", "topic": "agent", "priority": 5,
         "title": "pm alert -- devhost: host nas unreachable", "message": "host nas unreachable. See /servers",
         "tags": [project, "warning"]},
        {"id": "devntfy06", "time": now - 1800, "event": "message", "topic": "agent", "priority": 3,
         "title": "pm recovered -- devhost: host nas", "message": "host nas is back to normal.",
         "tags": [project, "white_check_mark"]},
    ]


def make_handler(project: str):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0].strip("/").split("/")
            body = b""
            if len(path) == 2 and path[1] == "json":
                if path[0] == "agent":
                    body = "".join(json.dumps(i) + "\n" for i in items(project)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
            else:
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # silent
            pass

    return H


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        sys.exit("usage: devntfy.py <port> [--project PATH]")
    project = sys.argv[sys.argv.index("--project") + 1] if "--project" in sys.argv else "/srv/fixproj"
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), make_handler(project)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
