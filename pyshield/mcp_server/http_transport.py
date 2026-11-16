"""Loopback-only HTTP transport for the PyShield MCP build service.

The desktop builder hosts this server inside its own process so one exe can
offer both the GUI and an MCP endpoint.  Only the Streamable HTTP subset that
stdio-first clients (Claude Code, Cursor, generic JSON-RPC agents) actually
use is implemented:

``POST /mcp``
    One JSON-RPC request, notification or batch.  Requests answer with
    ``application/json``; notifications answer ``202`` with an empty body.
``DELETE /mcp``
    Terminates a session; always ``204`` because sessions are advisory.
``GET /health``
    Unauthenticated liveness probe with counters, used by the desktop UI.
``GET /mcp``
    ``405``: this server never pushes unsolicited messages, so no SSE stream.

Security posture: bound to ``127.0.0.1`` only, bearer-token required unless the
host explicitly disables it, request bodies capped at ``MAX_MESSAGE_BYTES``,
and no request data is echoed into logs.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .. import __version__
from ..build_service import BuildService
from .protocol import MAX_MESSAGE_BYTES, PARSE_ERROR, JsonRpcServer, _error

SERVER_NAME = "pyshield-mcp-http"
DEFAULT_PORT = 8790
TOKEN_HEADER = "X-PyShield-Token"
SESSION_HEADER = "Mcp-Session-Id"
MAX_SESSIONS = 64


class McpHttpError(RuntimeError):
    """Raised when the loopback listener cannot be started."""


def format_token() -> str:
    """Return a new opaque bearer token for the local endpoint."""
    return secrets.token_urlsafe(32)


class _Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.requests = 0
        self.errors = 0
        self.rejected = 0
        self.last_method = ""
        self.last_at = ""
        self.methods: dict[str, int] = {}

    def record(self, method: str) -> None:
        with self.lock:
            self.requests += 1
            self.last_method = method or ""
            self.last_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            if method:
                self.methods[method] = self.methods.get(method, 0) + 1

    def record_error(self) -> None:
        with self.lock:
            self.errors += 1

    def record_rejected(self) -> None:
        with self.lock:
            self.rejected += 1

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "requests": self.requests,
                "errors": self.errors,
                "rejected": self.rejected,
                "last_method": self.last_method,
                "last_at": self.last_at,
                "methods": dict(self.methods),
                "uptime": int(time.time() - self.started_at),
            }


class McpHttpServer:
    """Serve one :class:`JsonRpcServer` over loopback HTTP."""

    def __init__(
        self,
        service: BuildService,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        token: str = "",
        require_token: bool = True,
        sessions: int = MAX_SESSIONS,
    ) -> None:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("the embedded MCP server only binds to loopback")
        if require_token and not token:
            raise ValueError("require_token needs a non-empty token")
        self.service = service
        self.host = host
        self.port = int(port)
        self.token = token
        self.require_token = bool(require_token and token)
        self.rpc = JsonRpcServer(service)
        self.stats = _Stats()
        self._sessions: list[str] = []
        self._session_limit = max(1, int(sessions))
        self._session_lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("localhost", "::1") else self.host
        return f"http://{host}:{self.port}/mcp"

    @property
    def running(self) -> bool:
        return self._httpd is not None and not self._closed

    def start(self) -> int:
        if self.running:
            return self.port
        handler = _make_handler(self)
        try:
            httpd = _LoopbackServer((self.host, self.port), handler)
        except OSError as exc:
            raise McpHttpError(
                f"cannot listen on {self.host}:{self.port}: {exc}"
            ) from exc
        self.port = int(httpd.server_address[1])
        self._httpd = httpd
        self._closed = False
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="pyshield-mcp-http",
            daemon=True,
        )
        self._thread.start()
        return self.port

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        self._closed = True
        if httpd is not None:
            try:
                httpd.shutdown()
            finally:
                httpd.server_close()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)

    # ------------------------------------------------------------------
    # Protocol plumbing
    # ------------------------------------------------------------------
    def handle(self, message: Any) -> list[dict]:
        responses = self.rpc.handle(message)
        method = ""
        if isinstance(message, dict):
            method = str(message.get("method") or "")
        elif isinstance(message, list):
            method = f"batch:{len(message)}"
        self.stats.record(method)
        for response in responses:
            if "error" in response:
                self.stats.record_error()
        return responses

    def new_session(self) -> str:
        sid = secrets.token_hex(16)
        with self._session_lock:
            self._sessions.append(sid)
            if len(self._sessions) > self._session_limit:
                del self._sessions[: len(self._sessions) - self._session_limit]
        return sid

    def status(self) -> dict[str, Any]:
        tools = [tool["name"] for tool in self.rpc.tools]
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port if self.running else self.port,
            "url": self.url,
            "token_required": self.require_token,
            "server": SERVER_NAME,
            "version": __version__,
            "tools": tools,
            "tool_count": len(tools),
            "sessions": len(self._sessions),
            **self.stats.snapshot(),
        }

    def authorize(self, headers: Any) -> bool:
        if not self.require_token:
            return True
        supplied = (headers.get(TOKEN_HEADER) or "").strip()
        if not supplied:
            auth = (headers.get("Authorization") or "").strip()
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        return bool(supplied) and secrets.compare_digest(supplied, self.token)


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    # Do not set SO_REUSEADDR: on Windows it lets a second process bind the
    # same port and silently steal MCP traffic from the GUI.
    allow_reuse_address = False

    def server_bind(self) -> None:
        super().server_bind()


def _make_handler(owner: McpHttpServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = SERVER_NAME
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # -- helpers ---------------------------------------------------
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            """Never write request lines to stderr/stdout."""
            return

        def _send(self, status: int, payload: Any = None,
                  extra: dict[str, str] | None = None) -> None:
            body = b""
            if payload is not None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _unauthorized(self) -> None:
            self._send(
                HTTPStatus.UNAUTHORIZED,
                {"error": "unauthorized", "hint": f"send {TOKEN_HEADER}"},
                {"WWW-Authenticate": "Bearer"},
            )

        def _read_body(self) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                return None
            if length <= 0 or length > MAX_MESSAGE_BYTES:
                return None
            return self.rfile.read(length)

        # -- verbs -----------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/health":
                self._send(HTTPStatus.OK, {
                    "ok": True,
                    "server": SERVER_NAME,
                    "version": __version__,
                    "pid": _pid(),
                    "port": owner.port,
                    **owner.stats.snapshot(),
                })
                return
            if path == "/mcp":
                self._send(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    {"error": "POST JSON-RPC to this endpoint"},
                    {"Allow": "POST, DELETE"},
                )
                return
            if path in ("/", "/index.html"):
                body = _INDEX_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path != "/mcp":
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not owner.authorize(self.headers):
                owner.stats.record_rejected()
                self._unauthorized()
                return
            raw = self._read_body()
            if raw is None:
                self._send(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    _error(None, PARSE_ERROR, "missing or oversized body"),
                )
                return
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    _error(None, PARSE_ERROR, "invalid JSON"),
                )
                return

            responses = owner.handle(message)
            extra: dict[str, str] = {}
            if _is_initialize(message):
                extra[SESSION_HEADER] = owner.new_session()
            if not responses:
                self._send(HTTPStatus.ACCEPTED, None, extra)
                return
            payload: Any = responses[0] if isinstance(message, dict) else responses
            self._send(HTTPStatus.OK, payload, extra)

        def do_DELETE(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/mcp":
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def _is_initialize(message: Any) -> bool:
    return (
        isinstance(message, dict)
        and message.get("method") == "initialize"
        and "id" in message
    )


def _pid() -> int:
    import os

    return os.getpid()


_INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<title>PyShield MCP</title>
<style>
 body{font:14px/1.6 system-ui,sans-serif;margin:40px auto;max-width:720px;color:#1f2328}
 code{background:#f2f4f6;padding:2px 6px;border-radius:4px}
 pre{background:#f2f4f6;padding:14px;border-radius:8px;overflow:auto}
</style>
<h1>PyShield MCP 服务</h1>
<p>该端口由 PyShield 加密构建器内置，只监听回环地址。</p>
<pre>POST http://127.0.0.1:PORT/mcp
Authorization: Bearer &lt;token&gt;
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}</pre>
<p>状态：<code>/health</code></p>
</html>
"""
