from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def make_project(root: Path) -> tuple[Path, Path]:
    projects = root / "projects"
    project = projects / "demo"
    project.mkdir(parents=True)
    (project / "manage.py").write_text("print('ok')\n", encoding="utf-8")
    package = project / "mypkg"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "tools.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8",
    )
    return projects, project


def server_command(root: Path, projects: Path, *extra: str) -> list[str]:
    return [
        sys.executable, "-m", "pyshield.mcp_server",
        "--project-root", str(projects),
        "--output-root", str(root / "out"),
        "--state-dir", str(root / "state"),
        *extra,
    ]


class McpProcess:
    def __init__(self, root: Path, projects: Path, *extra: str,
                 env: dict[str, str] | None = None):
        child_env = os.environ.copy()
        child_env["PYSHELD_DEV"] = "1"
        child_env.pop("PYSHELD_TOOL_LICENSE", None)
        if env:
            child_env.update(env)
            for key, value in env.items():
                if value is None:
                    child_env.pop(key, None)
        self.proc = subprocess.Popen(
            server_command(root, projects, *extra),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=child_env,
        )
        self.next_id = 1
        self.stderr_lines: list[str] = []

    def send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def send_raw(self, text: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def recv(self) -> dict:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read() if self.proc.stderr else ""
            raise RuntimeError(f"MCP server closed; stderr={stderr[:2000]}")
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> tuple[int, dict]:
        request_id = self.next_id
        self.next_id += 1
        self.send({
            "jsonrpc": "2.0", "id": request_id, "method": method,
            "params": params or {},
        })
        return request_id, self.recv()

    def initialize(self) -> dict:
        _id, response = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pyshield-tests", "version": "1"},
        })
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return response

    def call_tool(self, name: str, arguments: dict) -> tuple[dict, dict]:
        _id, response = self.request("tools/call", {
            "name": name, "arguments": arguments,
        })
        result = response.get("result") or {}
        return result, response

    def close(self) -> int:
        if self.proc.poll() is None:
            try:
                self.request("shutdown", {})
            except Exception:
                pass
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        return int(self.proc.returncode or 0)

    def stderr_text(self) -> str:
        if self.proc.stderr is None:
            return ""
        try:
            return self.proc.stderr.read()
        except Exception:
            return ""


def wait_for_state(process: McpProcess, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result, _response = process.call_tool(
            "pyshield_build_status", {"job_id": job_id},
        )
        structured = result.get("structuredContent") or {}
        if structured.get("state") in ("success", "failed", "cancelled", "duplicate"):
            return structured
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")
