"""Per-job logs, refund notes and redaction helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

from .. import secure_store
from .models import BuildSummary, RefundInfo, ValidationIssue, utc_now

_MAX_LOG_BYTES = 16 * 1024 * 1024
_TAIL_BYTES = 16 * 1024

_KEY_FIELD_RE = re.compile(
    r'"(master_key|build_key|signing_key|signing_public_key)"\s*:\s*"[^"]*"'
)
_LONG_SECRET_RE = re.compile(r"[A-Za-z0-9!#$%&()*+;<=>?@\[\]^_`{|}~-]{80,}")
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL,
)
_ENV_SECRET_RE = re.compile(r"(PYSHELD_[A-Z0-9_]+)\s*=\s*\S+")


def redact_text(text: str, redactions: Iterable[tuple[str, str]] = ()) -> str:
    value = str(text or "")
    for needle, replacement in redactions:
        if needle:
            value = value.replace(needle, replacement)
    value = _PEM_RE.sub("<redacted-pem>", value)
    value = _KEY_FIELD_RE.sub(lambda m: f'"{m.group(1)}": "<redacted>"', value)
    value = _ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", value)
    value = _LONG_SECRET_RE.sub("<redacted-secret>", value)
    return value


class JobArtifacts:
    """Owns the on-disk artifacts for one build job."""

    def __init__(self, job_root: Path, job_id: str):
        self.job_id = job_id
        self.root = Path(job_root) / job_id
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self.build_log = self.root / "build.log"
        self.result_path = self.root / "result.json"
        self.refund_path = self.root / "refund.json"
        self.refund_note_path = self.root / "refund.txt"
        self.job_path = self.root / "job.json"

    def _write_json(self, path: Path, payload: dict) -> None:
        data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        secure_store.write_private(path, (data + "\n").encode("utf-8"))

    def write_request(self, payload: dict) -> None:
        self._write_json(self.root / "request.json", payload)

    def write_preflight(self, issues: list[ValidationIssue], warnings: list[str],
                        extra: dict | None = None) -> None:
        self._write_json(
            self.root / "preflight.json",
            {
                "ok": not any(item.severity == "error" for item in issues),
                "issues": [item.to_dict() for item in issues],
                "warnings": list(warnings),
                **(extra or {}),
            },
        )

    def write_job(self, snapshot: dict) -> None:
        self._write_json(self.job_path, snapshot)

    def write_result(self, summary: BuildSummary, extra: dict | None = None) -> None:
        self._write_json(self.result_path, {**summary.to_dict(), **(extra or {})})

    def write_refund(self, refund: RefundInfo, failure_class: str, code: str,
                     message: str, evidence: str, quota_before: int,
                     quota_after: int, limit: int) -> None:
        payload = {
            "job_id": self.job_id,
            "failure_class": failure_class,
            "code": code,
            "message": redact_text(message),
            "evidence": redact_text(evidence),
            "eligible": refund.eligible,
            "applied": refund.applied,
            "already_applied": refund.already_applied,
            "quota_before": quota_before,
            "quota_after": quota_after,
            "limit": limit,
            "refunded_at": utc_now(),
            "policy": "only PROJECT_CODE failures are refunded",
        }
        self._write_json(self.refund_path, payload)
        if refund.applied or refund.already_applied:
            note = (
                f"本次构建因目标项目代码错误失败，已退还 1 次构建额度。\n"
                f"原因：{redact_text(message)}\n"
                f"额度：{quota_after}/{limit}\n"
            )
        else:
            note = (
                "本次构建失败，但不符合额度退还条件（仅目标项目代码错误退还）。\n"
                f"原因：{redact_text(message)}\n"
            )
        secure_store.write_private(self.refund_note_path, note.encode("utf-8"))

    def append_log(self, message: str) -> None:
        line = f"[{utc_now()}] {redact_text(message)}\n"
        try:
            with open(self.build_log, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
        except OSError:
            return
        try:
            os.chmod(self.build_log, 0o600)
        except OSError:
            pass
        try:
            if self.build_log.stat().st_size > _MAX_LOG_BYTES:
                with open(self.build_log, "rb") as handle:
                    handle.seek(-_MAX_LOG_BYTES, os.SEEK_END)
                    data = handle.read()
                secure_store.write_private(
                    self.build_log,
                    b"[truncated]\n" + data,
                )
        except OSError:
            pass

    def log_tail(self, max_lines: int = 200,
                 redactions: Iterable[tuple[str, str]] = ()) -> tuple[list[str], bool]:
        try:
            size = self.build_log.stat().st_size
            with open(self.build_log, "rb") as handle:
                if size > _TAIL_BYTES:
                    handle.seek(-_TAIL_BYTES, os.SEEK_END)
                data = handle.read()
        except OSError:
            return [], False
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        truncated = size > _TAIL_BYTES
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            truncated = True
        return [redact_text(line, redactions) for line in lines], truncated

    def read_job(self) -> dict | None:
        try:
            raw = self.job_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
