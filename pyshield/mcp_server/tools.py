"""MCP tool handlers backed by :class:`pyshield.build_service.BuildService`."""

from __future__ import annotations

import json
from typing import Any

from ..build_service import BuildService, ServiceError
from . import schemas


def _text_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": bool(is_error),
    }


class ToolDispatcher:
    def __init__(self, service: BuildService):
        self.service = service

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        if name == "pyshield_status":
            return _text_result(self.service.status_info())
        if name == "pyshield_inspect_project":
            return _text_result(self.service.inspect_project(arguments["project"]))
        if name == "pyshield_validate_build":
            return _text_result(self.service.validate(arguments["request"]).to_dict())
        if name == "pyshield_start_build":
            snapshot = self.service.start(
                arguments["request"],
                expect_plan_hash=arguments.get("expect_plan_hash"),
                confirm=bool(arguments.get("confirm", False)),
                idempotency_key=arguments.get("idempotency_key"),
            )
            return _text_result(snapshot.to_dict())
        if name == "pyshield_build_status":
            return _text_result(self.service.status(arguments["job_id"]).to_dict())
        if name == "pyshield_build_logs":
            return _text_result(self.service.logs(
                arguments["job_id"], int(arguments.get("tail_lines", 200)),
            ).to_dict())
        if name == "pyshield_cancel_build":
            return _text_result(self.service.cancel(arguments["job_id"]).to_dict())
        if name == "pyshield_verify_output":
            return _text_result(self.service.verify_output(
                arguments["job_id"], bool(arguments.get("full", False)),
            ).to_dict())
        if name == "pyshield_check_output":
            return _text_result(self.service.check_output(
                arguments["job_id"], arguments["interpreter_id"],
            ).to_dict())
        raise schemas.ArgumentError(f"unknown tool: {name}", "$.name")

    def call_safe(self, name: str, arguments: Any) -> dict[str, Any]:
        try:
            return self.call(name, arguments)
        except ServiceError as exc:
            return _text_result(
                {"ok": False, **exc.to_dict()}, is_error=True,
            )
