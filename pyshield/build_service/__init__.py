"""Local build service backing the PyShield MCP server.

The service owns strict validation, the build job lifecycle, quota settlement,
per-job artifacts and the only opt-in path that executes target project code.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping

from .. import build_spec, builder, cloud_license, secure_store, tool_license, trial_license
from .artifacts import JobArtifacts, redact_text
from .engine import ExecutionHooks, execute, redaction_pairs
from .jobs import snapshot_copy, snapshot_from_dict
from .models import (
    BuildJobSnapshot,
    BuildOutcome,
    BuildPolicy,
    BuildRequestV1,
    BuildSummary,
    CheckReport,
    FailureClass,
    JobRuntime,
    JobState,
    LogPage,
    RefundInfo,
    ValidationIssue,
    ValidationReport,
    VerifyReport,
    utc_now,
)
from .validation import (
    inspect_project as _inspect_project,
    license_status_summary,
    prepare,
    request_fingerprint,
    request_from_mapping,
)
from .verification import (
    VerificationError,
    check_output as _check_output,
    verify_output as _verify_output,
)


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str,
                 details: dict | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


def _ensure_private_dir(value: str | Path, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.exists() and build_spec.is_reparse_point(raw):
        raise ValueError(f"{label} is a symlink or reparse point: {raw}")
    if build_spec.path_has_reparse(raw):
        raise ValueError(f"{label} has a symlink or reparse point in its path: {raw}")
    raw.mkdir(parents=True, exist_ok=True)
    path = raw.resolve()
    if not path.is_dir():
        raise ValueError(f"{label} is not a directory: {path}")
    if build_spec.is_reparse_point(path):
        raise ValueError(f"{label} is a symlink or reparse point: {path}")
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _overlaps(a: Path, b: Path) -> bool:
    return build_spec.is_within(a, b) or build_spec.is_within(b, a)


def _validate_policy(policy: BuildPolicy) -> BuildPolicy:
    roots: list[Path] = []
    for root in policy.project_roots:
        path = build_spec.resolve_existing_directory(root, "project_root")
        if path.parent == path or path.anchor == str(path):
            raise ValueError(f"project_root cannot be a filesystem root: {path}")
        roots.append(path)
    if not roots:
        raise ValueError("at least one project_root is required")

    raw_paths = {
        "output_root": Path(policy.output_root).expanduser().resolve(strict=False),
        "secret_dir": Path(policy.secret_dir).expanduser().resolve(strict=False),
        "job_dir": Path(policy.job_dir).expanduser().resolve(strict=False),
        "audit_dir": Path(policy.audit_dir).expanduser().resolve(strict=False),
    }
    for root in roots:
        for label, raw in raw_paths.items():
            if _overlaps(root, raw):
                raise ValueError(f"project_root {root} overlaps {label} {raw}")
    for left_label, left in raw_paths.items():
        for right_label, right in raw_paths.items():
            if left_label >= right_label:
                continue
            if _overlaps(left, right):
                raise ValueError(f"{left_label} overlaps {right_label}")

    output_root = _ensure_private_dir(policy.output_root, "output_root")
    if output_root.parent == output_root or output_root.anchor == str(output_root):
        raise ValueError("output_root cannot be a filesystem root")
    secret_dir = _ensure_private_dir(policy.secret_dir, "secret_dir")
    job_dir = _ensure_private_dir(policy.job_dir, "job_dir")
    audit_dir = _ensure_private_dir(policy.audit_dir, "audit_dir")

    for root in roots:
        for label, other in (
            ("output_root", output_root),
            ("secret_dir", secret_dir),
            ("job_dir", job_dir),
            ("audit_dir", audit_dir),
        ):
            if _overlaps(root, other):
                raise ValueError(f"project_root {root} overlaps {label} {other}")
    for left_label, left in (
        ("output_root", output_root),
        ("secret_dir", secret_dir),
        ("job_dir", job_dir),
    ):
        for right_label, right in (
            ("secret_dir", secret_dir),
            ("job_dir", job_dir),
            ("audit_dir", audit_dir),
        ):
            if left_label == right_label:
                continue
            if _overlaps(left, right):
                raise ValueError(f"{left_label} overlaps {right_label}")

    interpreters: dict[str, Path] = {}
    for name, value in (policy.verify_pythons or {}).items():
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"verify_python {name} does not exist") from exc
        if not path.is_file() or build_spec.is_reparse_point(path):
            raise ValueError(f"verify_python {name} is not a regular executable file")
        interpreters[str(name)] = path
    if policy.allow_project_execution and not interpreters:
        raise ValueError("allow_project_execution requires at least one verify_python")

    for name, value in (
        ("max_files", policy.max_files),
        ("max_bytes", policy.max_bytes),
        ("max_depth", policy.max_depth),
        ("build_timeout", policy.build_timeout),
        ("check_timeout", policy.check_timeout),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    return replace(
        policy,
        project_roots=tuple(roots),
        output_root=output_root,
        secret_dir=secret_dir,
        job_dir=job_dir,
        audit_dir=audit_dir,
        verify_pythons=interpreters,
    )


def _features(prepared) -> list[str]:
    features: list[str] = []
    if prepared.effective_runtime == "native":
        features.append(tool_license.FEATURE_NATIVE_RUNTIME)
    if prepared.policy.ci:
        features.append(tool_license.FEATURE_CI)
    return features


def _quota_snapshot() -> dict:
    status = tool_license.check(initialize_trial=False)
    if status.quota:
        return {
            "used": status.quota.get("used", 0),
            "limit": status.quota.get("limit"),
        }
    if status.trial:
        return {
            "used": status.trial.get("builds", 0),
            "limit": status.trial.get("max_builds"),
            "builds_left": status.trial.get("builds_left"),
            "days_left": status.trial.get("days_left"),
        }
    return {}


class BuildService:
    def __init__(self, policy: BuildPolicy, *, require_activation: bool = False) -> None:
        self.policy = _validate_policy(policy)
        self.require_activation = require_activation
        self._jobs: dict[str, JobRuntime] = {}
        self._state_lock = threading.Lock()
        self._audit_lock = threading.Lock()
        self._active_build = False
        self._closed = False

    # ------------------------------------------------------------------
    # Read-only API
    # ------------------------------------------------------------------
    def validate(self, request: BuildRequestV1 | Mapping[str, Any]) -> ValidationReport:
        req, issues = self._coerce(request)
        if issues:
            return ValidationReport(ok=False, issues=issues)
        assert req is not None
        _prepared, report = prepare(req, self.policy)
        return report

    def inspect_project(self, project: str) -> dict:
        return _inspect_project(project, self.policy)

    def status_info(self) -> dict:
        status = tool_license.check(initialize_trial=False)
        return {
            "server": {
                "allow_build": self.policy.allow_build,
                "allow_project_execution": self.policy.allow_project_execution,
                "allow_runtime_source": self.policy.allow_runtime_source,
                "allow_sensitive_files": self.policy.allow_sensitive_files,
                "allow_concurrent_builds": self.policy.allow_concurrent_builds,
                "project_root_count": len(self.policy.project_roots),
                "max_files": self.policy.max_files,
                "max_bytes": self.policy.max_bytes,
                "build_timeout": self.policy.build_timeout,
                "check_timeout": self.policy.check_timeout,
            },
            "license": license_status_summary(status),
            "quota": _quota_snapshot(),
            "cloud_guard": cloud_license.guard_state(),
        }

    def status(self, job_id: str) -> BuildJobSnapshot:
        if not isinstance(job_id, str) or not job_id.strip():
            raise ServiceError("INVALID_ARGUMENT", "job_id 不能为空")
        with self._state_lock:
            runtime = self._jobs.get(job_id)
            if runtime is not None:
                return snapshot_copy(runtime.snapshot)
        artifacts = JobArtifacts(self.policy.job_dir, job_id)
        data = artifacts.read_job()
        if data is None:
            raise ServiceError("JOB_NOT_FOUND", f"找不到构建任务：{job_id}")
        return snapshot_from_dict(data)

    def logs(self, job_id: str, tail_lines: int = 200) -> LogPage:
        if not isinstance(tail_lines, int) or isinstance(tail_lines, bool):
            raise ServiceError("INVALID_ARGUMENT", "tail_lines 必须是整数")
        tail_lines = max(1, min(int(tail_lines), 2000))
        self.status(job_id)
        artifacts = JobArtifacts(self.policy.job_dir, job_id)
        lines, truncated = artifacts.log_tail(
            tail_lines, self._job_redactions(job_id),
        )
        return LogPage(job_id=job_id, lines=lines, truncated=truncated)

    # ------------------------------------------------------------------
    # Build lifecycle
    # ------------------------------------------------------------------
    def start(
        self,
        request: BuildRequestV1 | Mapping[str, Any],
        *,
        expect_plan_hash: str | None = None,
        confirm: bool = False,
        idempotency_key: str | None = None,
    ) -> BuildJobSnapshot:
        if not self.policy.allow_build:
            raise ServiceError("BUILD_DISABLED", "MCP 构建工具未启用（缺少 --allow-build）")
        if not confirm:
            raise ServiceError("CONFIRMATION_REQUIRED", "构建请求必须显式 confirm=true")
        # Unactivated installs (local trial never started, no entitlement) must
        # not build through MCP either: the product expects a portal grant.
        gate = tool_license.check(initialize_trial=False)
        # Packaged builds embed a public key; source runs intentionally skip
        # this gate so the dev/test trial flow keeps working.
        if self.require_activation and gate.public_key_configured and (
            gate.mode == "unlicensed"
            or (gate.mode == "trial" and (gate.trial or {}).get("not_started"))
        ):
            raise ServiceError(
                "LICENSE_REQUIRED",
                "尚未激活：请先在客户端「版本与试用」申请试用或导入授权。",
            )
        req, issues = self._coerce(request)
        if issues:
            raise ServiceError(
                "VALIDATION", "构建参数校验失败",
                details={"issues": [item.to_dict() for item in issues]},
            )
        assert req is not None
        if idempotency_key:
            if req.idempotency_key and req.idempotency_key != idempotency_key:
                raise ServiceError(
                    "IDEMPOTENCY_CONFLICT", "请求中的 idempotency_key 与参数不一致",
                )
            req = replace(req, idempotency_key=idempotency_key)
        fingerprint = (
            request_fingerprint(req, self.policy)
            if req.idempotency_key else None
        )
        if req.idempotency_key:
            try:
                existing_reservation = tool_license.find_reservation(
                    idempotency_key=req.idempotency_key,
                    idempotency_fingerprint=fingerprint,
                )
            except (tool_license.ToolLicenseError, secure_store.SecureStoreError) as exc:
                text = str(exc).lower()
                code = "IDEMPOTENCY_CONFLICT" if "idempotency" in text else "LICENSE_INVALID"
                raise ServiceError(code, str(exc)) from exc
            if existing_reservation is not None:
                try:
                    existing = self.status(existing_reservation.token)
                except ServiceError:
                    existing = BuildJobSnapshot(
                        job_id=existing_reservation.token,
                        state="duplicate",
                        message="同一构建请求已经受理，未重复扣减额度",
                        charged=False,
                        duplicate=True,
                        replayed=existing_reservation.replayed,
                        quota=_quota_snapshot(),
                    )
                existing.duplicate = True
                existing.replayed = existing_reservation.replayed
                existing.charged = False
                return existing
        prepared, report = prepare(req, self.policy)
        if prepared is None or not report.ok:
            raise ServiceError(
                "VALIDATION", "构建参数校验失败",
                details={"report": report.to_dict()},
            )
        if expect_plan_hash and expect_plan_hash != prepared.plan_hash:
            raise ServiceError(
                "PLAN_MISMATCH", "构建参数在 validate 之后发生了变化，请重新 validate",
                details={"expected": expect_plan_hash, "actual": prepared.plan_hash},
            )

        # Cloud guard: revoked/expired/unreachable licences must stop MCP
        # builds exactly like the GUI path (no side door around the quota).
        portal_bound = gate.entitlement is not None and bool(
            (gate.entitlement.raw or {}).get("serial")
        )
        cloud_license.configure(
            trial_license.default_server(), enable=True if portal_bound else None,
        )
        try:
            cloud_license.ensure_allowed()
            preflight = (
                cloud_license.preflight(gate.entitlement)
                if portal_bound else {"ok": True, "skipped": True}
            )
        except cloud_license.CloudError as exc:
            cloud_license.mark_blocked(str(exc), getattr(exc, "code", "") or "cloud_error")
            raise ServiceError(
                "LICENSE_BLOCKED", f"云端授权校验未通过：{exc}",
            ) from exc
        if not preflight.get("skipped") and not preflight.get("allowed", False):
            reason = str(preflight.get("reason") or "云端授权校验未通过")
            cloud_license.mark_blocked(reason, "preflight_denied")
            raise ServiceError("LICENSE_BLOCKED", reason)
        if portal_bound and not preflight.get("skipped"):
            cloud_license.apply_update(preflight)

        slot = self._acquire_slot()
        job_id = secrets.token_hex(16)
        reservation: tool_license.BuildReservation | None = None
        started = False
        try:
            if portal_bound:
                try:
                    cloud_license.consume(
                        gate.entitlement, idempotency_key=job_id,
                    )
                except cloud_license.CloudError as exc:
                    cloud_license.mark_blocked(
                        str(exc), getattr(exc, "code", "") or "consume_failed",
                    )
                    self._release_slot(slot)
                    raise ServiceError(
                        "LICENSE_BLOCKED", f"云端扣减构建次数失败：{exc}",
                    ) from exc
            try:
                reservation = tool_license.begin_build(
                    _features(prepared), token=job_id,
                    idempotency_key=req.idempotency_key,
                    idempotency_fingerprint=fingerprint,
                )
            except (tool_license.ToolLicenseError, secure_store.SecureStoreError) as exc:
                text = str(exc).lower()
                code = "QUOTA_EXHAUSTED" if ("quota" in text or "limit" in text) else "LICENSE_INVALID"
                raise ServiceError(code, str(exc)) from exc
            if reservation.duplicate or reservation.replayed:
                self._release_slot(slot)
                try:
                    existing = self.status(reservation.token)
                except ServiceError:
                    existing = BuildJobSnapshot(
                        job_id=reservation.token,
                        state="duplicate",
                        message="同一构建请求已经受理，未重复扣减额度",
                        charged=False,
                        duplicate=True,
                        replayed=reservation.replayed,
                        quota=_quota_snapshot(),
                    )
                existing.duplicate = True
                existing.replayed = reservation.replayed
                existing.charged = False
                return existing

            artifacts = JobArtifacts(self.policy.job_dir, job_id)
            artifacts.write_request(asdict(req))
            artifacts.write_preflight(
                report.issues, report.warnings,
                {
                    "plan_hash": prepared.plan_hash,
                    "effective_cipher": prepared.effective_cipher,
                    "effective_runtime": prepared.effective_runtime,
                },
            )
            snapshot = BuildJobSnapshot(
                job_id=job_id,
                state=JobState.QUEUED.value,
                stage="queued",
                percent=0,
                message="构建任务已排队",
                charged=reservation.charged,
                quota=_quota_snapshot(),
                created=utc_now(),
                log_available=True,
            )
            runtime = JobRuntime(snapshot=snapshot, lock_context=slot)
            with self._state_lock:
                self._jobs[job_id] = runtime
            thread = threading.Thread(
                target=self._run_job,
                args=(prepared, reservation, runtime, artifacts),
                name=f"pyshield-build-{job_id[:8]}",
                daemon=True,
            )
            runtime.thread = thread
            thread.start()
            started = True
            self._audit(
                "build_started", job_id=job_id,
                charged=reservation.charged,
                mode=reservation.mode,
            )
            return snapshot_copy(snapshot)
        except Exception:
            if reservation is not None and not started:
                try:
                    tool_license.finalize_build(
                        reservation, refund=False, reason_code="INTERNAL",
                    )
                except Exception:
                    pass
            if not started:
                with self._state_lock:
                    self._jobs.pop(job_id, None)
            self._release_slot(slot)
            raise

    def cancel(self, job_id: str) -> BuildJobSnapshot:
        with self._state_lock:
            runtime = self._jobs.get(job_id)
            if runtime is None:
                self.status(job_id)
                raise ServiceError("JOB_NOT_RUNNING", "构建任务不在当前进程中运行，无法取消")
            runtime.cancel.set()
            runtime.snapshot.message = "正在取消构建…"
            snapshot = snapshot_copy(runtime.snapshot)
        artifacts = JobArtifacts(self.policy.job_dir, job_id)
        artifacts.write_job(snapshot.to_dict())
        self._audit("cancel_requested", job_id=job_id)
        return snapshot

    def shutdown(self, grace: float = 10.0) -> None:
        self._closed = True
        with self._state_lock:
            runtimes = list(self._jobs.values())
        for runtime in runtimes:
            runtime.cancel.set()
        deadline = time.monotonic() + max(0.0, grace)
        for runtime in runtimes:
            thread = runtime.thread
            if thread is not None and thread.is_alive():
                remaining = max(0.0, deadline - time.monotonic())
                thread.join(timeout=remaining)

    # ------------------------------------------------------------------
    # Verification / opt-in execution
    # ------------------------------------------------------------------
    def verify_output(self, job_id: str, full: bool = False) -> VerifyReport:
        snapshot = self.status(job_id)
        if snapshot.summary is None:
            raise ServiceError("OUTPUT_NOT_FOUND", "该任务还没有成功产物")
        redactions = self._job_redactions(job_id)
        try:
            return _verify_output(
                job_id, snapshot.summary.output_name,
                self.policy.output_root, self.policy.secret_dir,
                full=bool(full), redactions=redactions,
            )
        except VerificationError as exc:
            raise ServiceError(exc.code, exc.message) from exc

    def check_output(self, job_id: str, interpreter_id: str) -> CheckReport:
        if not self.policy.allow_project_execution:
            raise ServiceError(
                "EXECUTION_DISABLED",
                "执行目标项目代码未启用（缺少 --allow-project-execution）",
            )
        interpreter = self.policy.verify_pythons.get(str(interpreter_id))
        if interpreter is None:
            raise ServiceError(
                "INTERPRETER_UNKNOWN", f"未知的解释器标识：{interpreter_id}",
                details={"available": sorted(self.policy.verify_pythons)},
            )
        snapshot = self.status(job_id)
        if snapshot.summary is None:
            raise ServiceError("OUTPUT_NOT_FOUND", "该任务还没有成功产物")
        try:
            result = _check_output(
                job_id, snapshot.summary.output_name,
                self.policy.output_root, self.policy.job_dir,
                interpreter, timeout=self.policy.check_timeout,
                redactions=self._job_redactions(job_id),
            )
        except VerificationError as exc:
            raise ServiceError(exc.code, exc.message) from exc
        self._audit(
            "check_output_run", job_id=job_id,
            returncode=result.returncode, timed_out=result.timed_out,
        )
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _coerce(self, request: BuildRequestV1 | Mapping[str, Any]) -> tuple[BuildRequestV1 | None, list[ValidationIssue]]:
        if isinstance(request, BuildRequestV1):
            return request, []
        if isinstance(request, Mapping):
            return request_from_mapping(request)
        return None, [ValidationIssue("request", "TYPE_INVALID", "request 必须是对象")]

    def _redactions(self) -> list[tuple[str, str]]:
        return [
            (str(self.policy.output_root), "<output-root>"),
            (str(self.policy.secret_dir), "<secret-dir>"),
            (str(self.policy.job_dir), "<job-dir>"),
            (str(Path.home()), "~"),
        ]

    def _job_redactions(self, job_id: str) -> list[tuple[str, str]]:
        redactions = self._redactions()
        try:
            request_path = self.policy.job_dir / job_id / "request.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            project_name = request.get("project")
        except (OSError, ValueError, AttributeError):
            project_name = None
        if isinstance(project_name, str) and project_name:
            for root in self.policy.project_roots:
                candidate = root / project_name
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if build_spec.is_within(resolved, root):
                    redactions.append((str(resolved), "<project>"))
                    break
        return redactions

    def _acquire_slot(self):
        if self.policy.allow_concurrent_builds:
            return None
        with self._state_lock:
            if self._active_build:
                raise ServiceError(
                    "BUILD_BUSY", "已有构建任务在运行", retryable=True,
                )
            self._active_build = True
        lock_context = secure_store.exclusive_lock(
            self.policy.job_dir / "build.lock", timeout=0.05,
        )
        try:
            lock_context.__enter__()
        except secure_store.SecureStoreError as exc:
            with self._state_lock:
                self._active_build = False
            raise ServiceError(
                "BUILD_BUSY", "另一个 MCP 进程正在构建", retryable=True,
            ) from exc
        return lock_context

    def _release_slot(self, lock_context) -> None:
        if lock_context is not None:
            try:
                lock_context.__exit__(None, None, None)
            except Exception:
                pass
        if not self.policy.allow_concurrent_builds:
            with self._state_lock:
                self._active_build = False

    def _run_job(
        self,
        prepared,
        reservation: tool_license.BuildReservation,
        runtime: JobRuntime,
        artifacts: JobArtifacts,
    ) -> None:
        snapshot = runtime.snapshot
        snapshot.state = JobState.RUNNING.value
        snapshot.stage = "starting"
        snapshot.percent = 1
        snapshot.message = "开始构建"
        self._persist(runtime, artifacts)
        last = {"stage": "", "percent": -100}
        try:
            def on_progress(stage: str, percent: int) -> None:
                snapshot.stage = str(stage)
                snapshot.percent = max(0, min(100, int(percent)))
                snapshot.message = str(stage)
                if (
                    stage != last["stage"]
                    or snapshot.percent - last["percent"] >= 5
                    or snapshot.percent >= 100
                ):
                    last["stage"] = stage
                    last["percent"] = snapshot.percent
                    self._persist(runtime, artifacts)

            hooks = ExecutionHooks(
                artifacts=artifacts,
                cancel=runtime.cancel,
                timed_out=runtime.timed_out,
                on_progress=on_progress,
                redactions=redaction_pairs(prepared, self.policy.job_dir),
            )
            outcome: BuildOutcome = execute(prepared, reservation, hooks)
            snapshot.state = outcome.state
            snapshot.failure_class = outcome.failure_class
            snapshot.error = outcome.error
            snapshot.refund = outcome.refund
            snapshot.summary = outcome.summary
            if outcome.state == "success":
                snapshot.stage = "done"
                snapshot.percent = 100
                snapshot.message = "构建完成"
            snapshot.quota = _quota_snapshot()
            snapshot.finished = utc_now()
            self._persist(runtime, artifacts)
            self._audit(
                "build_finished", job_id=snapshot.job_id,
                state=snapshot.state, failure_class=snapshot.failure_class,
                refunded=bool(snapshot.refund and snapshot.refund.applied),
            )
        except Exception:
            try:
                tool_license.finalize_build(
                    reservation, refund=False, reason_code="INTERNAL",
                )
            except Exception:
                pass
            snapshot.state = JobState.FAILED.value
            snapshot.failure_class = FailureClass.INTERNAL.value
            snapshot.error = "internal build error"
            snapshot.finished = utc_now()
            self._persist(runtime, artifacts)
            self._audit(
                "build_finished", job_id=snapshot.job_id,
                state=snapshot.state, failure_class=FailureClass.INTERNAL.value,
                refunded=False,
            )
        finally:
            self._release_slot(runtime.lock_context)
            runtime.lock_context = None

    def _persist(self, runtime: JobRuntime, artifacts: JobArtifacts) -> None:
        runtime.snapshot.log_available = artifacts.build_log.exists()
        artifacts.write_job(runtime.snapshot.to_dict())

    def _audit(self, event: str, **fields: Any) -> None:
        payload = {"event": event, "at": utc_now(), **fields}
        line = json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
        with self._audit_lock:
            try:
                self.policy.audit_dir.mkdir(parents=True, exist_ok=True)
                path = self.policy.audit_dir / "mcp.jsonl"
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            except OSError:
                pass


__all__ = [
    "BuildPolicy",
    "BuildRequestV1",
    "BuildService",
    "CheckReport",
    "ServiceError",
    "ValidationReport",
    "VerifyReport",
]