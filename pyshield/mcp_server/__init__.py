"""PyShield MCP build server entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import __version__
from ..build_service import BuildPolicy, BuildService
from .protocol import JsonRpcServer
from .schemas import tool_definitions


def _default_state_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "PyShield" / "mcp"
    return Path.home() / ".pyshield" / "mcp"


def _parse_interpreters(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for index, value in enumerate(values, 1):
        text = str(value).strip()
        if not text:
            continue
        if "=" in text and not text.startswith(("/", "\\")):
            name, raw = text.split("=", 1)
            name = name.strip() or f"python{index}"
        else:
            name, raw = f"python{index}", text
        if name in result:
            name = f"{name}-{index}"
        result[name] = Path(raw).expanduser()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyshield.mcp_server",
        description="Local stdio MCP build service for PyShield.",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument(
        "--project-root", action="append", required=True, metavar="PATH",
        help="allowed project root (repeatable)",
    )
    parser.add_argument(
        "--output-root", default=None, metavar="PATH",
        help="directory for build outputs (default: <state-dir>/outputs)",
    )
    parser.add_argument(
        "--state-dir", default=None, metavar="PATH",
        help="state directory for secrets, jobs and audit logs",
    )
    parser.add_argument("--secret-dir", default=None, metavar="PATH")
    parser.add_argument("--job-dir", default=None, metavar="PATH")
    parser.add_argument("--audit-dir", default=None, metavar="PATH")
    parser.add_argument(
        "--allow-build", action="store_true",
        help="enable pyshield_start_build and pyshield_cancel_build",
    )
    parser.add_argument(
        "--allow-project-execution", action="store_true",
        help="enable pyshield_check_output (executes target project code)",
    )
    parser.add_argument("--allow-runtime-source", action="store_true")
    parser.add_argument("--allow-sensitive-files", action="store_true")
    parser.add_argument("--allow-concurrent-builds", action="store_true")
    parser.add_argument(
        "--verify-python", action="append", default=[], metavar="[ID=]PATH",
        help="whitelisted interpreter for pyshield_check_output (repeatable)",
    )
    parser.add_argument("--build-timeout", type=int, default=1800)
    parser.add_argument("--check-timeout", type=int, default=120)
    parser.add_argument("--max-project-files", type=int, default=20_000)
    parser.add_argument("--max-project-bytes", type=int, default=1024 ** 3)
    parser.add_argument("--max-depth", type=int, default=32)
    parser.add_argument("--ci", action="store_true", help="require the ci license feature")
    parser.add_argument(
        "--selftest", action="store_true",
        help="validate configuration and print a JSON summary, then exit",
    )
    return parser


def _policy_from_args(args: argparse.Namespace) -> BuildPolicy:
    state_dir = Path(args.state_dir).expanduser() if args.state_dir else _default_state_dir()
    output_root = Path(args.output_root).expanduser() if args.output_root else state_dir / "outputs"
    secret_dir = Path(args.secret_dir).expanduser() if args.secret_dir else state_dir / "secrets"
    job_dir = Path(args.job_dir).expanduser() if args.job_dir else state_dir / "jobs"
    audit_dir = Path(args.audit_dir).expanduser() if args.audit_dir else state_dir / "audit"
    return BuildPolicy(
        project_roots=tuple(Path(value) for value in args.project_root),
        output_root=output_root,
        secret_dir=secret_dir,
        job_dir=job_dir,
        audit_dir=audit_dir,
        allow_build=bool(args.allow_build),
        allow_project_execution=bool(args.allow_project_execution),
        allow_runtime_source=bool(args.allow_runtime_source),
        allow_sensitive_files=bool(args.allow_sensitive_files),
        allow_concurrent_builds=bool(args.allow_concurrent_builds),
        verify_pythons=_parse_interpreters(args.verify_python),
        max_files=args.max_project_files,
        max_bytes=args.max_project_bytes,
        max_depth=args.max_depth,
        build_timeout=args.build_timeout,
        check_timeout=args.check_timeout,
        ci=bool(args.ci),
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(__version__)
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        policy = _policy_from_args(args)
        service = BuildService(policy, require_activation=True)
    except (ValueError, OSError) as exc:
        print(f"[pyshield-mcp] configuration error: {exc}", file=sys.stderr)
        return 2

    tools = tool_definitions(
        allow_build=service.policy.allow_build,
        allow_execution=service.policy.allow_project_execution,
        allow_runtime_source=service.policy.allow_runtime_source,
    )
    # Redirected stdout/stderr default to the locale encoding on Windows;
    # Chinese reasons in the status payload would then reach clients as GBK
    # bytes and break JSON parsing. Force UTF-8 on both text streams.
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - odd wrappers
            pass

    if args.selftest:
        payload = {
            "ok": True,
            "server": "pyshield",
            "version": __version__,
            "tools": [tool["name"] for tool in tools],
            "status": service.status_info(),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        service.shutdown(grace=1)
        return 0

    protocol_out = sys.stdout.buffer if sys.stdout is not None else None
    protocol_in = sys.stdin.buffer if sys.stdin is not None else None
    if protocol_out is None or protocol_in is None:
        print("[pyshield-mcp] stdio is unavailable", file=sys.stderr)
        service.shutdown(grace=1)
        return 2
    # From this point on, any accidental print() must not corrupt JSON-RPC.
    sys.stdout = sys.stderr
    server = JsonRpcServer(
        service, stdin=protocol_in, stdout=protocol_out, stderr=sys.stderr,
    )
    print(
        f"[pyshield-mcp] ready; tools={','.join(tool['name'] for tool in tools)}",
        file=sys.stderr,
        flush=True,
    )
    try:
        return server.serve()
    except KeyboardInterrupt:
        return 130
    finally:
        service.shutdown(grace=10)


__all__ = ["main", "build_parser", "JsonRpcServer"]
