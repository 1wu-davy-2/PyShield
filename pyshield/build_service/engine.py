"""Build execution, failure classification and quota settlement."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import builder, tool_license
from .artifacts import JobArtifacts, redact_text
from .models import (
    BuildOutcome,
    BuildSummary,
    FailureClass,
    PreparedBuild,
    RefundInfo,
)


@dataclass
class ExecutionHooks:
    artifacts: JobArtifacts
    cancel: threading.Event
    timed_out: threading.Event
    on_progress: Callable[[str, int], None]
    redactions: list[tuple[str, str]]


def redaction_pairs(prepared: PreparedBuild, job_root: Path) -> list[tuple[str, str]]:
    pairs = [
        (str(prepared.project), "<project>"),
        (str(prepared.output), "<output>"),
        (str(prepared.policy.secret_dir), "<secret-dir>"),
        (str(prepared.policy.job_dir), "<job-dir>"),
        (str(prepared.policy.output_root), "<output-root>"),
        (str(Path.home()), "~"),
    ]
    return [(needle, replacement) for needle, replacement in pairs if needle]


def summarize(prepared: PreparedBuild, result: builder.BuildResult) -> BuildSummary:
    return BuildSummary(
        output_name=prepared.output.name,
        build_id=result.build_id,
        modules=result.modules,
        cipher=result.cipher,
        integrity=prepared.request.integrity,
        runtime=prepared.effective_runtime,
        size=result.output_size,
        elapsed=result.elapsed,
        expires=prepared.request.expire,
        binding=prepared.request.bind_mode,
        verified=False,
    )


def _safe_finalize(reservation: tool_license.BuildReservation, reason: str) -> None:
    try:
        tool_license.finalize_build(reservation, refund=False, reason_code=reason)
    except Exception:
        # The build itself already happened; do not turn a ledger cleanup
        # failure into a build failure. The pending reservation is visible in
        # the audit for later reconciliation.
        pass


def _refund_info(result: tool_license.RefundResult) -> RefundInfo:
    return RefundInfo(
        eligible=True,
        applied=result.applied,
        already_applied=result.already_applied,
        failure_class=FailureClass.PROJECT_CODE.value,
        message=result.message,
        quota_before=result.before_used,
        quota_after=result.after_used,
        limit=result.limit,
        note_available=True,
    )


def execute(
    prepared: PreparedBuild,
    reservation: tool_license.BuildReservation,
    hooks: ExecutionHooks,
) -> BuildOutcome:
    """Run the build and settle the quota according to the failure class."""
    options = prepared.options
    options.cancel = hooks.cancel
    options.progress = hooks.on_progress
    options.log_sink = hooks.artifacts.append_log
    options.secret_path = str(
        prepared.policy.secret_dir / reservation.token / "build.shield-secret.json"
    )
    options.console_log = False
    options.no_verify = True

    def on_timeout() -> None:
        hooks.timed_out.set()
        hooks.cancel.set()

    timer = threading.Timer(max(1, prepared.policy.build_timeout), on_timeout)
    timer.daemon = True
    timer.start()
    try:
        result = builder.build(options)
    except builder.ProjectCodeError as exc:
        message = str(exc)
        evidence = message[:2000]
        try:
            refund_result = tool_license.refund_build(
                reservation, FailureClass.PROJECT_CODE.value,
                "PROJECT_CODE_ERROR", message, evidence,
            )
            refund = _refund_info(refund_result)
        except Exception as refund_exc:
            refund = RefundInfo(
                eligible=True, applied=False, failure_class=FailureClass.PROJECT_CODE.value,
                code="REFUND_FAILED", message=str(refund_exc),
            )
        redacted = redact_text(message, hooks.redactions)
        hooks.artifacts.append_log(f"project code error: {redacted}")
        hooks.artifacts.write_refund(
            refund, FailureClass.PROJECT_CODE.value, "PROJECT_CODE_ERROR",
            message, evidence, refund.quota_before, refund.quota_after, refund.limit,
        )
        return BuildOutcome(
            state="failed", failure_class=FailureClass.PROJECT_CODE.value,
            error=redacted, refund=refund,
        )
    except builder.BuildCancelled as exc:
        failure = FailureClass.TIMEOUT if hooks.timed_out.is_set() else FailureClass.CANCELLED
        _safe_finalize(reservation, failure.value)
        message = "构建超时" if failure is FailureClass.TIMEOUT else "构建已取消"
        hooks.artifacts.append_log(message)
        return BuildOutcome(state="cancelled", failure_class=failure.value, error=message)
    except (builder.BuildError, OSError, MemoryError) as exc:
        _safe_finalize(reservation, FailureClass.ENVIRONMENT.value)
        message = redact_text(str(exc), hooks.redactions)
        hooks.artifacts.append_log(f"environment error: {message}")
        return BuildOutcome(
            state="failed", failure_class=FailureClass.ENVIRONMENT.value, error=message,
        )
    except Exception as exc:  # noqa: BLE001 - last-resort classification
        _safe_finalize(reservation, FailureClass.INTERNAL.value)
        hooks.artifacts.append_log(f"internal error: {type(exc).__name__}")
        return BuildOutcome(
            state="failed", failure_class=FailureClass.INTERNAL.value,
            error="internal build error",
        )
    else:
        if hooks.timed_out.is_set():
            # The build completed after the watchdog fired; treat it as a
            # completed build, but do not report a timeout that did not occur.
            hooks.timed_out.clear()
        try:
            status = tool_license.check(initialize_trial=False)
            builder.update_tool_info(result.output, status.watermark())
        except Exception:
            pass
        _safe_finalize(reservation, "SUCCESS")
        summary = summarize(prepared, result)
        hooks.artifacts.write_result(summary)
        hooks.artifacts.append_log(f"build succeeded: {summary.build_id}")
        return BuildOutcome(
            state="success", summary=summary,
        )
    finally:
        timer.cancel()
