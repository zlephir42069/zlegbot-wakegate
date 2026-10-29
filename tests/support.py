"""Shared fixtures. HTTP stays on 127.0.0.1."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# http.server closes the accepted fd from the makefile, and CPython then warns
# that the socket wrapper is still around. The fd is already closed.
warnings.filterwarnings(
    "ignore",
    message=r"unclosed <socket\.socket",
    category=ResourceWarning,
)

REPO = Path(__file__).resolve().parents[1]
_PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        status, body = self.server.routes.get(self.path, (404, b"missing"))
        if isinstance(body, str):
            body = body.encode()
        self._send(status, body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        self.server.posts.append(raw)
        self.server.seen_headers.append(
            {
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
            }
        )
        status = 200
        if self.server.fail_remaining > 0:
            self.server.fail_remaining -= 1
            status = 500
        self._send(status, b"ok" if status < 400 else b"err")

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class LocalServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.routes = {}
        self.httpd.posts = []
        self.httpd.seen_headers = []
        self.httpd.fail_remaining = 0
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)


class TempCase(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore_env)
        for key in _PROXY_KEYS:
            os.environ.pop(key, None)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._env)

    def server(self) -> LocalServer:
        running = LocalServer()
        self.addCleanup(running.close)
        return running
