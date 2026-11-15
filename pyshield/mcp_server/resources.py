"""MCP resources and prompts for the PyShield build service."""

from __future__ import annotations

import json
from typing import Any

from . import schemas

RESOURCES = [
    {
        "uri": "pyshield://status",
        "name": "PyShield MCP status",
        "description": "Current server configuration, tool license and quota.",
        "mimeType": "application/json",
    },
    {
        "uri": "pyshield://docs/runbook",
        "name": "PyShield runbook",
        "description": "Build, deploy and license operations.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "pyshield://docs/security-hardening",
        "name": "PyShield security hardening",
        "description": "Threat model, residual risks and hardening notes.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "pyshield://docs/mcp-server",
        "name": "PyShield MCP server",
        "description": "MCP setup, tools, quota and refund policy.",
        "mimeType": "text/markdown",
    },
]

RUNBOOK = """# PyShield MCP runbook (summary)

1. Call `pyshield_status` to check the tool license and quota.
2. Call `pyshield_inspect_project` for the project under an allowed root.
3. Call `pyshield_validate_build` and keep the returned `plan_hash`.
4. Call `pyshield_start_build` with `confirm: true` and the plan hash.
5. Poll `pyshield_build_status`; read `pyshield_build_logs` for details.
6. Use `pyshield_verify_output` for static manifest verification.
7. `pyshield_check_output` is only available when the server was started with
   `--allow-project-execution`; it executes the target project in a sandbox.

Quota is charged once when a build is admitted. Only failures classified as
`PROJECT_CODE` are refunded; environment, cancellation and timeout failures are
not refunded. Build secrets are stored outside the output directory and are
never returned by MCP tools.
"""

SECURITY = """# PyShield MCP security boundaries (summary)

- stdio only; no listening socket.
- Writes require `--allow-build`; target project execution requires
  `--allow-project-execution` and a whitelisted interpreter.
- Project roots and the output root are allow-listed and must be disjoint.
- Symlinks, junctions and other reparse points are rejected.
- Build secrets are written to a private secret directory, never returned.
- stdout carries only JSON-RPC; build logs go to per-job files and stderr.
- The client is offline by design. This is cost-raising protection, not an
  absolute confidentiality guarantee.
"""

MCP_DOC = """# PyShield MCP server

Start a console server with:

```
PyShieldMCP --project-root E:\\work --output-root E:\\work\\pyshield-output --allow-build
```

Read-only tools are always available. `--allow-build` enables build and cancel.
`--allow-project-execution --verify-python python=C:\\Python312\\python.exe`
enables `pyshield_check_output`, which executes the target project in a sandbox.

The server speaks newline-delimited JSON-RPC 2.0 on stdio and does not open a
network port.
"""

PROMPTS = [
    {
        "name": "protect-django-project",
        "description": "Guide an agent through validating and building a Django project.",
        "arguments": [
            {
                "name": "project",
                "description": "Project path relative to an allowed project root.",
                "required": True,
            },
            {
                "name": "output_name",
                "description": "Single directory name under the output root.",
                "required": True,
            },
        ],
    },
    {
        "name": "verify-build",
        "description": "Guide an agent through checking a completed build.",
        "arguments": [
            {
                "name": "job_id",
                "description": "Build job returned by pyshield_start_build.",
                "required": True,
            },
        ],
    },
]


def read_resource(uri: str, service) -> dict[str, Any]:
    if uri == "pyshield://status":
        text = json.dumps(service.status_info(), indent=2, ensure_ascii=False)
        return {"uri": uri, "mimeType": "application/json", "text": text}
    if uri == "pyshield://docs/runbook":
        return {"uri": uri, "mimeType": "text/markdown", "text": RUNBOOK}
    if uri == "pyshield://docs/security-hardening":
        return {"uri": uri, "mimeType": "text/markdown", "text": SECURITY}
    if uri == "pyshield://docs/mcp-server":
        return {"uri": uri, "mimeType": "text/markdown", "text": MCP_DOC}
    raise schemas.ArgumentError(f"unknown resource: {uri}", "$.uri")


def get_prompt(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    arguments = arguments or {}
    if name == "protect-django-project":
        project = str(arguments.get("project") or "<project>")
        output_name = str(arguments.get("output_name") or "<output-name>")
        text = (
            f"Protect the Django project {project} into {output_name}. "
            "First call pyshield_inspect_project, then pyshield_validate_build, "
            "then pyshield_start_build with confirm=true. Do not run target "
            "project code unless the user explicitly enables check_output."
        )
        return {
            "description": "Validate and build a Django project with PyShield.",
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
    if name == "verify-build":
        job_id = str(arguments.get("job_id") or "<job-id>")
        text = (
            f"Verify PyShield build {job_id}: call pyshield_build_status, then "
            "pyshield_build_logs, then pyshield_verify_output. Do not use "
            "pyshield_check_output unless project execution was explicitly enabled."
        )
        return {
            "description": "Inspect and statically verify a completed build.",
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
    raise schemas.ArgumentError(f"unknown prompt: {name}", "$.name")
