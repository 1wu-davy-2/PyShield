"""Serialization helpers for build job snapshots."""

from __future__ import annotations

import copy
from typing import Any

from .models import BuildJobSnapshot, BuildSummary, RefundInfo


def snapshot_from_dict(data: dict[str, Any]) -> BuildJobSnapshot:
    refund_data = data.get("refund")
    summary_data = data.get("summary")
    return BuildJobSnapshot(
        job_id=str(data.get("job_id") or ""),
        state=str(data.get("state") or "queued"),
        stage=str(data.get("stage") or "queued"),
        percent=int(data.get("percent") or 0),
        message=str(data.get("message") or ""),
        failure_class=str(data.get("failure_class") or ""),
        error=str(data.get("error") or ""),
        refund=RefundInfo(**refund_data) if isinstance(refund_data, dict) else None,
        summary=BuildSummary(**summary_data) if isinstance(summary_data, dict) else None,
        charged=bool(data.get("charged", False)),
        duplicate=bool(data.get("duplicate", False)),
        replayed=bool(data.get("replayed", False)),
        quota=dict(data.get("quota") or {}),
        created=str(data.get("created") or ""),
        finished=str(data.get("finished") or ""),
        log_available=bool(data.get("log_available", False)),
    )


def snapshot_copy(snapshot: BuildJobSnapshot) -> BuildJobSnapshot:
    return copy.deepcopy(snapshot)
