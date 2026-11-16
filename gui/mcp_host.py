"""Host the MCP build service inside the desktop builder process.

The packaged PyShieldBuilder.exe is a single binary that offers three things:

* the pywebview GUI,
* an MCP stdio entry point (``PyShieldBuilder.exe --mcp ...``),
* a loopback Streamable-HTTP MCP endpoint started together with the GUI.

This module owns the last one: it turns GUI settings into a ``BuildPolicy``,
keeps the bearer token private on disk, and renders copy-paste snippets for
Claude Code / Cursor / generic JSON-RPC agents.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from pyshield import secure_store
from pyshield.build_service import BuildPolicy, BuildService
from pyshield.mcp_server.http_transport import (
    DEFAULT_PORT,
    McpHttpError,
    McpHttpServer,
    format_token,
)

from . import core

TOKEN_FILENAME = "mcp-token.txt"
STATE_DIRNAME = "mcp"
PORT_MIN = 1024
PORT_MAX = 65535
PORT_SCAN = 20


class McpHostError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _token_path() -> Path:
    return core.config_dir() / TOKEN_FILENAME


def load_token() -> str:
    """Read the persisted bearer token, creating one on first use."""
    path = _token_path()
    try:
        raw = secure_store.read_private(path)
    except OSError:
        raw = None
    token = (raw or b"").decode("utf-8", "ignore").strip()
    if len(token) >= 24:
        return token
    return rotate_token()


def rotate_token() -> str:
    token = format_token()
    secure_store.write_private(_token_path(), token.encode("utf-8"))
    return token


def default_project_root() -> Path:
    """Pick a usable default root that cannot overlap the state directory."""
    documents = Path.home() / "Documents"
    if documents.is_dir():
        return documents
    fallback = Path.home() / "PyShieldProjects"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _port(value: object, fallback: int = DEFAULT_PORT) -> int:
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return port if PORT_MIN <= port <= PORT_MAX else fallback


class McpHost:
    """Own the embedded HTTP MCP server and its build policy."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._server: McpHttpServer | None = None
        self._service: BuildService | None = None
        self._error = ""
        self._fallback = False
        self._project_root = ""
        self._allow_build = False
        self._requested_port = DEFAULT_PORT

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._server is not None and self._server.running

    def _stdio_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [str(Path(sys.executable).resolve()), "--mcp"]
        return [sys.executable or "python", "-m", "pyshield.mcp_server"]

    def status(self) -> dict:
        with self._lock:
            payload = self._server.status() if self._server else {
                "running": False,
                "host": "127.0.0.1",
                "port": self._requested_port,
                "url": f"http://127.0.0.1:{self._requested_port}/mcp",
                "token_required": True,
                "server": "pyshield-mcp-http",
                "tools": [],
                "tool_count": 0,
                "sessions": 0,
                "requests": 0,
                "errors": 0,
                "rejected": 0,
                "last_method": "",
                "last_at": "",
                "methods": {},
                "uptime": 0,
            }
            payload.update({
                "enabled": self.running,
                "requested_port": self._requested_port,
                "port_fallback": self._fallback,
                "project_root": self._project_root,
                "allow_build": self._allow_build,
                "token": load_token(),
                "error": self._error,
                "stdio_command": _join(self._stdio_command()),
                "pid": os.getpid(),
            })
            return payload

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self, settings: dict | None = None) -> dict:
        settings = settings or core.load_settings()
        with self._lock:
            if self.running:
                return {"ok": True, "status": self.status(), "message": "MCP 服务已在运行"}

            port = _port(settings.get("mcp_port"), DEFAULT_PORT)
            self._requested_port = port
            root = str(settings.get("mcp_project_root") or "").strip()
            project_root = Path(root).expanduser() if root else default_project_root()
            self._allow_build = bool(settings.get("mcp_allow_build"))
            self._project_root = str(project_root)
            state_dir = core.config_dir() / STATE_DIRNAME
            token = load_token()

            try:
                policy = self._policy(project_root, state_dir, self._allow_build)
                service = BuildService(policy)
            except (ValueError, OSError) as exc:
                self._error = str(exc)
                return {"ok": False, "error": str(exc), "code": "policy", "status": self.status()}

            server: McpHttpServer | None = None
            last_error = ""
            for candidate in range(port, min(port + PORT_SCAN, PORT_MAX) + 1):
                instance = McpHttpServer(service, port=candidate, token=token)
                try:
                    instance.start()
                except McpHttpError as exc:
                    last_error = str(exc)
                    continue
                server = instance
                break
            if server is None:
                service.shutdown(grace=1)
                self._error = last_error or "端口不可用"
                self._server = None
                self._service = None
                return {
                    "ok": False, "error": self._error, "code": "port",
                    "status": self.status(),
                }

            self._server = server
            self._service = service
            self._fallback = server.port != port
            self._error = ""
            return {
                "ok": True,
                "status": self.status(),
                "message": (
                    f"MCP 已监听 {server.url}"
                    if not self._fallback
                    else f"端口 {port} 被占用，已改用 {server.port}"
                ),
            }

    def stop(self) -> dict:
        with self._lock:
            server, self._server = self._server, None
            service, self._service = self._service, None
            if server is not None:
                server.stop()
            if service is not None:
                try:
                    service.shutdown(grace=2)
                except Exception:  # noqa: BLE001 - shutdown must not break quit
                    pass
            return {"ok": True, "status": self.status()}

    def restart(self, settings: dict | None = None) -> dict:
        with self._lock:
            self.stop()
            self._server = None
            return self.start(settings)

    def apply_settings(self, settings: dict | None = None) -> dict:
        """Restart only when the endpoint-relevant settings changed."""
        settings = settings or core.load_settings()
        if not self.running:
            if settings.get("mcp_enabled", True):
                return self.start(settings)
            return {"ok": True, "status": self.status(), "message": "MCP 服务未启用"}
        changed = (
            _port(settings.get("mcp_port"), DEFAULT_PORT) != self._requested_port
            or _root_of(settings) != self._project_root
            or bool(settings.get("mcp_allow_build")) != self._allow_build
        )
        if changed:
            self.stop()
            return self.start(settings)
        return {"ok": True, "status": self.status(), "message": "MCP 配置未变化"}

    def rotate_token(self) -> dict:
        return {"ok": True, "status": self.status(), "token": rotate_token()}

    # ------------------------------------------------------------------
    # Snippets
    # ------------------------------------------------------------------
    def snippet(self, kind: str = "http") -> dict:
        status = self.status()
        url = status["url"]
        token = status["token"]
        root = status["project_root"] or str(default_project_root())
        allow = ["--allow-build"] if status["allow_build"] else []
        prefix = self._stdio_command()
        extra = ["--project-root", root, *allow]
        text = ""
        if kind == "stdio":
            text = _join([*prefix, *extra])
        elif kind == "http-json":
            text = _json_block({
                "mcpServers": {
                    "pyshield": {
                        "type": "http",
                        "url": url,
                        "headers": {"Authorization": f"Bearer {token}"},
                    }
                }
            })
        elif kind == "stdio-json":
            text = _json_block({
                "mcpServers": {
                    "pyshield": {
                        "command": prefix[0],
                        "args": [*prefix[1:], *extra],
                    }
                }
            })
        elif kind == "claude-http":
            text = _join([
                "claude mcp add --transport http pyshield", url,
                "--header", f'"Authorization: Bearer {token}"',
            ])
        elif kind == "health":
            text = _join(["curl", f"http://127.0.0.1:{status['port']}/health"])
        else:
            raise McpHostError("snippet", f"unknown snippet: {kind}")
        return {"ok": True, "kind": kind, "text": text, "status": status}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _policy(project_root: Path, state_dir: Path, allow_build: bool) -> BuildPolicy:
        root = project_root.expanduser()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"项目根目录不存在：{root}")
        if root.parent == root:
            raise ValueError("项目根目录不能是磁盘根目录")
        return BuildPolicy(
            project_roots=(root,),
            output_root=state_dir / "outputs",
            secret_dir=state_dir / "secrets",
            job_dir=state_dir / "jobs",
            audit_dir=state_dir / "audit",
            allow_build=allow_build,
        )


def _root_of(settings: dict) -> str:
    raw = str(settings.get("mcp_project_root") or "").strip()
    return str(Path(raw).expanduser() if raw else default_project_root())


def _join(parts: list[str]) -> str:
    quoted = []
    for part in parts:
        if not part:
            continue
        quoted.append(f'"{part}"' if " " in part else part)
    return " ".join(quoted)


def _json_block(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


__all__ = [
    "McpHost", "McpHostError", "default_project_root", "load_token",
    "rotate_token", "TOKEN_FILENAME",
]
