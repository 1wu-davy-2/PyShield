"""Static verification and opt-in sandboxed project checks."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .. import build_spec, builder
from .artifacts import redact_text
from .models import CheckReport, VerifyReport


class VerificationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_output(output_root: Path, output_name: str) -> Path:
    output = output_root / output_name
    if not build_spec.is_within(output, output_root) or output == output_root:
        raise VerificationError("PATH_ESCAPE", "输出路径越出 output_root")
    if not output.is_dir():
        raise VerificationError("OUTPUT_NOT_FOUND", "构建输出不存在")
    return output


def verify_output(
    job_id: str,
    output_name: str,
    output_root: Path,
    secret_dir: Path,
    *,
    full: bool = False,
    redactions: Iterable[tuple[str, str]] = (),
) -> VerifyReport:
    output = _safe_output(Path(output_root), output_name)
    secret = Path(secret_dir) / job_id / "build.shield-secret.json"
    if not secret.is_file():
        raise VerificationError("SECRET_MISSING", "该构建的密钥文件不存在，无法校验签名")
    try:
        report = builder.verify(builder.VerifyOptions(
            dist=str(output), secret=str(secret), full=bool(full),
        ))
    except builder.BuildError as exc:
        raise VerificationError("VERIFY_FAILED", redact_text(str(exc), redactions)) from exc
    manifest = report.get("manifest") or {}
    failures = [
        redact_text(str(item), redactions)
        for item in (report.get("failures") or [])
    ]
    return VerifyReport(
        job_id=job_id,
        ok=bool(report.get("ok")),
        build_id=str(report.get("build_id") or ""),
        integrity=str(report.get("integrity") or ""),
        manifest_files=int(manifest.get("files") or 0) if isinstance(manifest, dict) else 0,
        failures=failures,
    )


def _minimal_environment(dist: Path, sandbox: Path) -> dict[str, str]:
    keep = (
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT",
        "COMSPEC", "LANG", "LC_ALL", "NUMBER_OF_PROCESSORS",
    )
    env = {key: os.environ[key] for key in keep if key in os.environ}
    state_dir = sandbox / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYSHELD_LICENSE": str(dist / "pyshield.lic"),
        "PYSHELD_STATE_DIR": str(state_dir),
        "PYSHELD_BINDING_FILE": str(sandbox / "first-run.binding"),
    })
    return env


def check_output(
    job_id: str,
    output_name: str,
    output_root: Path,
    job_dir: Path,
    interpreter: Path,
    *,
    timeout: int = 120,
    redactions: Iterable[tuple[str, str]] = (),
) -> CheckReport:
    """Copy the distribution to a sandbox and run ``manage.py check``.

    This is the only MCP path that executes target project code.  It never
    copies the sibling build secret, and it uses a minimal environment.
    """
    output = _safe_output(Path(output_root), output_name)
    try:
        build_spec.assert_no_reparse_tree(output)
    except build_spec.SpecError as exc:
        raise VerificationError(exc.code, exc.message) from exc
    if builder.directory_size(output) > 2 * 1024 ** 3:
        raise VerificationError("LIMIT_BYTES", "构建输出超过 check_output 的大小上限")
    sandbox = Path(job_dir) / "checks" / job_id / secrets.token_hex(4)
    dist = sandbox / "dist"
    sandbox.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(output, dist, symlinks=False)
        env = _minimal_environment(dist, sandbox)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                [str(interpreter), str(dist / "manage.py"), "check"],
                cwd=dist, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            return CheckReport(
                job_id=job_id, ok=False, timed_out=True,
                interpreter=interpreter.name,
                output=f"manage.py check timed out after {timeout}s",
            )
        raw = ((completed.stdout or "") + (completed.stderr or "")).strip()
        text = redact_text(raw[:8192], redactions)
        return CheckReport(
            job_id=job_id, ok=completed.returncode == 0,
            returncode=completed.returncode, output=text,
            interpreter=interpreter.name,
        )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
