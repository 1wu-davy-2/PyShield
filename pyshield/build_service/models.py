"""Data models shared by the MCP build service and its tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .. import builder, tool_license


class FailureClass(str, Enum):
    VALIDATION = "VALIDATION"
    POLICY = "POLICY"
    PROJECT_CODE = "PROJECT_CODE"
    ENVIRONMENT = "ENVIRONMENT"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"
    BUILD_BUSY = "BUILD_BUSY"
    LICENSE = "LICENSE"
    QUOTA = "QUOTA"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class BuildRequestV1:
    """A transport-neutral, already-validated build request.

    ``project`` is relative to one of the configured project roots and
    ``output_name`` is a single path component under the output root.  Absolute
    output paths, ``force``, ``no_verify`` and a caller-supplied interpreter are
    deliberately absent: those are policy decisions owned by the server.
    """

    project: str
    output_name: str
    cipher: str = "auto"
    integrity: str = "protected"
    runtime: str = "pyc"
    expire: str | None = None
    grace_days: int = 0
    bind_mode: str = "any"
    fingerprints: list[str] = field(default_factory=list)
    customer: str = ""
    encrypt_migrations: bool = False
    encrypt_tests: bool = False
    excludes: list[str] = field(default_factory=list)
    mutable_patterns: list[str] = field(default_factory=list)
    idempotency_key: str | None = None


@dataclass(frozen=True)
class BuildPolicy:
    project_roots: tuple[Path, ...]
    output_root: Path
    secret_dir: Path
    job_dir: Path
    audit_dir: Path
    allow_build: bool = False
    allow_project_execution: bool = False
    allow_runtime_source: bool = False
    allow_sensitive_files: bool = False
    allow_concurrent_builds: bool = False
    verify_pythons: dict[str, Path] = field(default_factory=dict)
    max_files: int = 20_000
    max_bytes: int = 1024 ** 3
    max_depth: int = 32
    build_timeout: int = 1800
    check_timeout: int = 120
    ci: bool = False


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    code: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class ValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plan_hash: str = ""
    output_name: str = ""
    effective_cipher: str = ""
    effective_runtime: str = ""
    license: dict = field(default_factory=dict)
    quota: dict = field(default_factory=dict)
    modules: int = 0
    files: int = 0
    total_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "warnings": list(self.warnings),
            "plan_hash": self.plan_hash,
            "output_name": self.output_name,
            "effective_cipher": self.effective_cipher,
            "effective_runtime": self.effective_runtime,
            "license": self.license,
            "quota": self.quota,
            "modules": self.modules,
            "files": self.files,
            "total_bytes": self.total_bytes,
        }


@dataclass
class PreparedBuild:
    request: BuildRequestV1
    policy: BuildPolicy
    project: Path
    output: Path
    options: builder.BuildOptions
    license_status: tool_license.ToolLicenseStatus
    effective_cipher: str
    effective_runtime: str
    plan_hash: str
    warnings: list[str] = field(default_factory=list)
    modules: int = 0
    files: int = 0
    total_bytes: int = 0


@dataclass
class RefundInfo:
    eligible: bool = False
    applied: bool = False
    already_applied: bool = False
    failure_class: str = ""
    code: str = ""
    message: str = ""
    quota_before: int = 0
    quota_after: int = 0
    limit: int = 0
    note_available: bool = False

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "applied": self.applied,
            "already_applied": self.already_applied,
            "failure_class": self.failure_class,
            "code": self.code,
            "message": self.message,
            "quota_before": self.quota_before,
            "quota_after": self.quota_after,
            "limit": self.limit,
            "note_available": self.note_available,
        }


@dataclass
class BuildSummary:
    output_name: str = ""
    build_id: str = ""
    modules: int = 0
    cipher: str = ""
    integrity: str = ""
    runtime: str = ""
    size: int = 0
    elapsed: float = 0.0
    expires: str | None = None
    binding: str = ""
    verified: bool = False

    def to_dict(self) -> dict:
        return {
            "output_name": self.output_name,
            "build_id": self.build_id,
            "modules": self.modules,
            "cipher": self.cipher,
            "integrity": self.integrity,
            "runtime": self.runtime,
            "size": self.size,
            "elapsed": round(self.elapsed, 3),
            "expires": self.expires,
            "binding": self.binding,
            "verified": self.verified,
        }


@dataclass
class BuildJobSnapshot:
    job_id: str
    state: str = JobState.QUEUED.value
    stage: str = "queued"
    percent: int = 0
    message: str = ""
    failure_class: str = ""
    error: str = ""
    refund: RefundInfo | None = None
    summary: BuildSummary | None = None
    charged: bool = False
    duplicate: bool = False
    replayed: bool = False
    quota: dict = field(default_factory=dict)
    created: str = ""
    finished: str = ""
    log_available: bool = False

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "state": self.state,
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
            "failure_class": self.failure_class,
            "error": self.error,
            "refund": self.refund.to_dict() if self.refund else None,
            "summary": self.summary.to_dict() if self.summary else None,
            "charged": self.charged,
            "duplicate": self.duplicate,
            "replayed": self.replayed,
            "quota": self.quota,
            "created": self.created,
            "finished": self.finished,
            "log_available": self.log_available,
        }


@dataclass
class LogPage:
    job_id: str
    lines: list[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "lines": list(self.lines),
            "truncated": self.truncated,
        }


@dataclass
class VerifyReport:
    job_id: str
    ok: bool
    build_id: str = ""
    integrity: str = ""
    manifest_files: int = 0
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "ok": self.ok,
            "build_id": self.build_id,
            "integrity": self.integrity,
            "manifest_files": self.manifest_files,
            "failures": list(self.failures),
        }


@dataclass
class CheckReport:
    job_id: str
    ok: bool
    returncode: int = 0
    timed_out: bool = False
    output: str = ""
    interpreter: str = ""

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "ok": self.ok,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "output": self.output,
            "interpreter": self.interpreter,
        }


@dataclass
class BuildOutcome:
    state: str
    failure_class: str = ""
    error: str = ""
    refund: RefundInfo | None = None
    summary: BuildSummary | None = None


@dataclass
class JobRuntime:
    """Mutable in-memory state for one running job."""

    snapshot: BuildJobSnapshot
    cancel: threading.Event = field(default_factory=threading.Event)
    timed_out: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    lock_context: object | None = field(default=None, repr=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
