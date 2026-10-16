#!/usr/bin/env python3
"""Project-aware static server for UI mocks (behind nginx).

/                       index of projects that have <dev>/<project>/docs/mock
/<project>/<path>       serves <dev>/<project>/docs/mock/<path> (read-only)

No code runs from the mocks; paths are resolved and must stay inside that project's docs/mock.
"""
import html
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

DEV = Path(os.environ.get("MOCK_DEV_ROOT", Path.home() / "dev")).resolve()
BINDS = os.environ.get("MOCK_BIND", "127.0.0.1,172.19.0.1").split(",")
PORT = int(os.environ.get("MOCK_PORT", "8094"))


def projects() -> list[str]:
    return sorted(p.parent.parent.name for p in DEV.glob("*/docs/mock") if p.is_dir() and not p.parent.parent.name.startswith("."))


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parts = [p for p in unquote(urlsplit(path).path).split("/") if p]
        if not parts or ".." in parts or parts[0].startswith(".") or parts[0] not in projects():
            return "/nonexistent"
        base = (DEV / parts[0] / "docs" / "mock").resolve()
        target = (base.joinpath(*parts[1:])).resolve()
        if target != base and base not in target.parents:
            return "/nonexistent"
        return str(target)

    def do_GET(self):  # noqa: N802
        if urlsplit(self.path).path == "/":
            body = "<!doctype html><meta charset=utf-8><meta name=color-scheme content='light dark'><title>mocks</title>" \
                   "<body style='font:16px system-ui;padding:2rem'><h3>UI mocks</h3><ul>" + "".join(
                       f"<li><a href='/{html.escape(p)}/'>{html.escape(p)}</a></li>" for p in projects()) + "</ul>"
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_HEAD(self):  # noqa: N802
        if urlsplit(self.path).path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        super().do_HEAD()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):  # journald gets the access log
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    servers = []
    for host in BINDS:
        try:
            servers.append(ThreadingHTTPServer((host, PORT), partial(Handler)))
        except OSError as e:
            print(f"skip {host}:{PORT}: {e}", file=sys.stderr)
    if not servers:
        raise SystemExit("no bind address available")
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
