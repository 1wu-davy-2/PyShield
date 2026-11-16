#!/usr/bin/env python3
"""End-to-end stdio smoke test for a source or frozen PyShield MCP server."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
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
    return projects, project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--server", required=True,
        help="PyShieldMCP executable, or 'module' for python -m pyshield.mcp_server",
    )
    parser.add_argument(
        "--arg", action="append", default=[], metavar="VALUE",
        help="extra argv inserted before the generated flags "
             "(use --arg=--mcp for PyShieldBuilder.exe)",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name).resolve()
    projects, _project = make_project(root)
    if args.server == "module":
        command = [sys.executable, "-m", "pyshield.mcp_server"]
    else:
        command = [str(Path(args.server).resolve())]
    command += list(args.arg)
    command += [
        "--project-root", str(projects),
        "--output-root", str(root / "out"),
        "--state-dir", str(root / "state"),
        "--allow-build",
    ]
    env = os.environ.copy()
    env["PYSHELD_DEV"] = "1"
    env.pop("PYSHELD_TOOL_LICENSE", None)
    env.pop("PYSHELD_TOOL_PUBLIC_KEY", None)
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    next_id = 1
    responses: list[dict] = []

    def kill() -> None:
        if process.poll() is None:
            process.kill()

    watchdog = threading.Timer(max(10.0, args.timeout), kill)
    watchdog.daemon = True
    watchdog.start()

    def send(payload: dict) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def recv() -> dict:
        assert process.stdout is not None
        line = process.stdout.readline()
        if not line:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"MCP server stopped; stderr={stderr[-2000:]}")
        response = json.loads(line)
        responses.append(response)
        return response

    def request(method: str, params: dict | None = None) -> dict:
        nonlocal next_id
        request_id = next_id
        next_id += 1
        send({
            "jsonrpc": "2.0", "id": request_id,
            "method": method, "params": params or {},
        })
        return recv()

    try:
        init = request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pyshield-mcp-smoke", "version": "1"},
        })
        assert init["result"]["serverInfo"]["name"] == "pyshield", init
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        listed = request("tools/list")
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert "pyshield_start_build" in names, names

        build_request = {
            "project": "demo",
            "output_name": "demo-build",
            "cipher": "chacha-pure",
            "runtime": "pyc",
        }
        validation = request("tools/call", {
            "name": "pyshield_validate_build",
            "arguments": {"request": build_request},
        })["result"]["structuredContent"]
        assert validation["ok"], validation

        started = request("tools/call", {
            "name": "pyshield_start_build",
            "arguments": {
                "request": build_request,
                "expect_plan_hash": validation["plan_hash"],
                "confirm": True,
            },
        })["result"]["structuredContent"]
        job_id = started["job_id"]

        deadline = time.monotonic() + args.timeout
        final = None
        while time.monotonic() < deadline:
            final = request("tools/call", {
                "name": "pyshield_build_status",
                "arguments": {"job_id": job_id},
            })["result"]["structuredContent"]
            if final.get("state") in ("success", "failed", "cancelled"):
                break
            time.sleep(0.05)
        assert final and final["state"] == "success", final

        logs = request("tools/call", {
            "name": "pyshield_build_logs",
            "arguments": {"job_id": job_id, "tail_lines": 20},
        })["result"]["structuredContent"]
        assert logs["lines"], logs

        verified = request("tools/call", {
            "name": "pyshield_verify_output",
            "arguments": {"job_id": job_id},
        })["result"]["structuredContent"]
        assert verified["ok"], verified

        secret_path = root / "state" / "secrets" / job_id / "build.shield-secret.json"
        secret = json.loads(secret_path.read_text(encoding="utf-8"))
        blobs = json.dumps(responses, ensure_ascii=False) + json.dumps(logs, ensure_ascii=False)
        for key in ("master_key", "build_key", "signing_key"):
            assert str(secret[key]) not in blobs, f"{key} leaked"

        request("shutdown", {})
        print("MCP smoke OK")
        return 0
    finally:
        watchdog.cancel()
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        temp.cleanup()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"MCP smoke FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
