"""Minimal newline-delimited JSON-RPC 2.0 server for MCP stdio.

Only the stable subset needed by PyShield is implemented: initialize, tools,
resources, prompts, ping, cancellation and shutdown.  The official MCP SDK is
used in tests as a conformance client, not as a runtime dependency.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, BinaryIO

from .. import __version__
from ..build_service import BuildService
from . import resources, schemas
from .schemas import tool_definitions
from .tools import ToolDispatcher

MAX_MESSAGE_BYTES = 1024 * 1024
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_NOT_INITIALIZED = -32002

INSTRUCTIONS = (
    "PyShield local build service. Build tools never execute the target "
    "project and never return build secrets. Only PROJECT_CODE failures refund "
    "quota. pyshield_check_output executes target code and is available only "
    "when the server was started with --allow-project-execution."
)


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _result(request_id: Any, payload: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


class JsonRpcServer:
    def __init__(
        self,
        service: BuildService,
        *,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        stderr: Any = None,
    ):
        self.service = service
        self.policy = service.policy
        self.stdin = stdin if stdin is not None else (
            sys.stdin.buffer if sys.stdin is not None else None
        )
        self.stdout = stdout if stdout is not None else (
            sys.stdout.buffer if sys.stdout is not None else None
        )
        self.stderr = stderr if stderr is not None else sys.stderr
        self.tools = tool_definitions(
            allow_build=self.policy.allow_build,
            allow_execution=self.policy.allow_project_execution,
            allow_runtime_source=self.policy.allow_runtime_source,
        )
        self.tool_map = {tool["name"]: tool for tool in self.tools}
        self.dispatcher = ToolDispatcher(service)
        self._write_lock = threading.Lock()
        self._initialized = False
        self._shutdown = False

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def send(self, payload: dict) -> None:
        if self.stdout is None:
            raise RuntimeError("MCP stdio requires a console stdout")
        data = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        with self._write_lock:
            self.stdout.write(data)
            self.stdout.flush()

    def _log(self, message: str) -> None:
        try:
            if self.stderr is not None:
                self.stderr.write(f"[pyshield-mcp] {message}\n")
                self.stderr.flush()
        except Exception:
            pass

    def serve(self) -> int:
        if self.stdin is None or self.stdout is None:
            raise RuntimeError("MCP stdio requires a console stdin/stdout")
        while not self._shutdown:
            try:
                line = self.stdin.readline(MAX_MESSAGE_BYTES + 1)
            except (OSError, ValueError):
                break
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES:
                self.send(_error(None, INVALID_REQUEST, "message too large"))
                # Drain the remainder of the oversized line.
                while line and not line.endswith(b"\n"):
                    line = self.stdin.readline(MAX_MESSAGE_BYTES + 1)
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send(_error(None, PARSE_ERROR, "invalid JSON"))
                continue
            if isinstance(message, list):
                if not message:
                    self.send(_error(None, INVALID_REQUEST, "empty batch"))
                    continue
                for item in message:
                    self._handle(item)
            else:
                self._handle(message)
        return 0

    # ------------------------------------------------------------------
    # Dispatch (transport neutral)
    # ------------------------------------------------------------------
    def handle(self, message: Any) -> list[dict]:
        """Return the JSON-RPC responses for one message or batch.

        The stdio loop and the HTTP transport share this method so both
        expose exactly the same tools and validation rules.  Notifications
        return an empty list; a batch returns one entry per request.
        """
        if isinstance(message, list):
            if not message:
                return [_error(None, INVALID_REQUEST, "empty batch")]
            responses: list[dict] = []
            for item in message:
                response = self._dispatch(item)
                if response is not None:
                    responses.append(response)
            return responses
        response = self._dispatch(message)
        return [] if response is None else [response]

    def _handle(self, message: Any) -> None:
        for response in self.handle(message):
            self.send(response)

    def _dispatch(self, message: Any) -> dict | None:
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "request must be an object")
        if message.get("jsonrpc") != "2.0":
            return _error(message.get("id"), INVALID_REQUEST, "jsonrpc must be 2.0")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(message.get("id"), INVALID_REQUEST, "method is required")
        has_id = "id" in message
        request_id = message.get("id")
        params = message.get("params")
        if params is None:
            params = {}

        if not has_id:
            self._handle_notification(method, params)
            return None

        try:
            if method == "initialize":
                return self._handle_initialize(request_id, params)
            if method == "ping":
                return _result(request_id, {})
            if not self._initialized:
                return _error(request_id, SERVER_NOT_INITIALIZED, "initialize first")
            if method == "tools/list":
                return _result(request_id, {"tools": self.tools})
            if method == "tools/call":
                return self._handle_tools_call(request_id, params)
            if method == "resources/list":
                return _result(request_id, {"resources": resources.RESOURCES})
            if method == "resources/templates/list":
                return _result(request_id, {"resourceTemplates": []})
            if method == "resources/read":
                return self._handle_resource_read(request_id, params)
            if method == "prompts/list":
                return _result(request_id, {"prompts": resources.PROMPTS})
            if method == "prompts/get":
                return self._handle_prompt_get(request_id, params)
            if method == "logging/setLevel":
                return _result(request_id, {})
            if method == "shutdown":
                self._shutdown = True
                return _result(request_id, None)
            return _error(request_id, METHOD_NOT_FOUND, f"unknown method: {method}")
        except schemas.ArgumentError as exc:
            return _error(
                request_id, INVALID_PARAMS, exc.message,
                {"path": exc.path},
            )
        except Exception as exc:  # noqa: BLE001 - never leak a traceback
            self._log(f"internal error in {method}: {type(exc).__name__}")
            return _error(request_id, INTERNAL_ERROR, "internal error")

    def _handle_notification(self, method: str, params: Any) -> None:
        if method in (
            "notifications/initialized", "notifications/cancelled",
            "notifications/progress", "notifications/roots/list_changed",
        ):
            return
        if method == "exit":
            self._shutdown = True
            return
        # Unknown notifications are ignored by JSON-RPC.

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _handle_initialize(self, request_id: Any, params: Any) -> dict:
        if not isinstance(params, dict):
            raise schemas.ArgumentError("initialize params must be an object", "$.params")
        requested = params.get("protocolVersion")
        version = (
            requested if requested in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        self._initialized = True
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {"name": "pyshield", "version": __version__},
            "instructions": INSTRUCTIONS,
        })

    def _handle_tools_call(self, request_id: Any, params: Any) -> dict:
        if not isinstance(params, dict):
            raise schemas.ArgumentError("tools/call params must be an object", "$.params")
        name = params.get("name")
        if not isinstance(name, str) or name not in self.tool_map:
            raise schemas.ArgumentError(f"unknown tool: {name}", "$.params.name")
        tool = self.tool_map[name]
        arguments = schemas.validate_tool_arguments(
            tool, params.get("arguments", {}),
        )
        result = self.dispatcher.call_safe(name, arguments)
        return _result(request_id, result)

    def _handle_resource_read(self, request_id: Any, params: Any) -> dict:
        if not isinstance(params, dict):
            raise schemas.ArgumentError("resources/read params must be an object", "$.params")
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise schemas.ArgumentError("uri is required", "$.params.uri")
        item = resources.read_resource(uri, self.service)
        return _result(request_id, {"contents": [item]})

    def _handle_prompt_get(self, request_id: Any, params: Any) -> dict:
        if not isinstance(params, dict):
            raise schemas.ArgumentError("prompts/get params must be an object", "$.params")
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise schemas.ArgumentError("name is required", "$.params.name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise schemas.ArgumentError("arguments must be an object", "$.params.arguments")
        return _result(request_id, resources.get_prompt(name, arguments))
