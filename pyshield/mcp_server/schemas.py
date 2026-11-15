"""Tool schemas and a small strict JSON-Schema subset validator.

The MCP SDK is deliberately not a runtime dependency.  The schemas below are
also used by the server itself, so an agent cannot smuggle unknown fields or
wrong types past the advertised contract.
"""

from __future__ import annotations

import json
import re
from typing import Any


class ArgumentError(ValueError):
    def __init__(self, message: str, path: str = "$"):
        super().__init__(message)
        self.message = message
        self.path = path


_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9._-]{8,128}$"
_FINGERPRINT_PATTERN = r"^[0-9a-fA-F]{32}$"


BUILD_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["project", "output_name"],
    "properties": {
        "project": {"type": "string", "minLength": 1, "maxLength": 512},
        "output_name": {"type": "string", "minLength": 1, "maxLength": 120},
        "cipher": {
            "type": "string",
            "enum": ["auto", "chacha-pure", "chacha", "aesgcm"],
        },
        "integrity": {
            "type": "string",
            "enum": ["protected", "full", "off"],
        },
        "runtime": {
            "type": "string",
            "enum": ["pyc", "native"],
        },
        "expire": {
            "anyOf": [
                {"type": "string", "minLength": 1, "maxLength": 32},
                {"type": "integer", "minimum": 1, "maximum": 36500},
            ],
        },
        "grace_days": {"type": "integer", "minimum": 0, "maximum": 3650},
        "bind_mode": {
            "type": "string",
            "enum": ["any", "first-run", "fixed"],
        },
        "fingerprints": {
            "type": "array",
            "maxItems": 100,
            "uniqueItems": True,
            "items": {"type": "string", "pattern": _FINGERPRINT_PATTERN},
        },
        "customer": {"type": "string", "maxLength": 200},
        "encrypt_migrations": {"type": "boolean"},
        "encrypt_tests": {"type": "boolean"},
        "excludes": {
            "type": "array",
            "maxItems": 100,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "mutable_patterns": {
            "type": "array",
            "maxItems": 100,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "idempotency_key": {"type": "string", "pattern": _IDEMPOTENCY_PATTERN},
    },
}


def _job_id_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": 128}


def tool_definitions(*, allow_build: bool, allow_execution: bool,
                     allow_runtime_source: bool) -> list[dict[str, Any]]:
    request_schema = json.loads(json.dumps(BUILD_REQUEST_SCHEMA))
    if allow_runtime_source:
        request_schema["properties"]["runtime"]["enum"].append("source")

    tools: list[dict[str, Any]] = [
        {
            "name": "pyshield_status",
            "description": "Show PyShield MCP server configuration, tool license and remaining build quota. Read-only.",
            "inputSchema": {
                "type": "object", "additionalProperties": False, "properties": {},
            },
            "annotations": {
                "readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": False,
            },
        },
        {
            "name": "pyshield_inspect_project",
            "description": "Inspect a project under an allowed project root: package/module counts, file size, sensitive files and syntax issues. Does not execute project code and does not consume quota.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["project"],
                "properties": {
                    "project": {"type": "string", "minLength": 1, "maxLength": 512},
                },
            },
            "annotations": {
                "readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": False,
            },
        },
        {
            "name": "pyshield_validate_build",
            "description": "Strictly validate a build request without writing output or consuming quota. Returns a plan_hash that can be passed to pyshield_start_build.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["request"],
                "properties": {"request": request_schema},
            },
            "annotations": {
                "readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": False,
            },
        },
        {
            "name": "pyshield_build_status",
            "description": "Return the state, stage, progress, failure class, refund and summary of a build job. Read-only.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["job_id"],
                "properties": {"job_id": _job_id_schema()},
            },
            "annotations": {
                "readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": False,
            },
        },
        {
            "name": "pyshield_build_logs",
            "description": "Return the redacted tail of a build log. Read-only.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["job_id"],
                "properties": {
                    "job_id": _job_id_schema(),
                    "tail_lines": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
            },
            "annotations": {
                "readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": False,
            },
        },
        {
            "name": "pyshield_verify_output",
            "description": "Statically verify a successful job's manifest and release hygiene. Does not execute project code and does not consume quota.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["job_id"],
                "properties": {
                    "job_id": _job_id_schema(),
                    "full": {"type": "boolean"},
                },
            },
            "annotations": {
                "readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": False,
            },
        },
    ]
    if allow_build:
        tools.extend([
            {
                "name": "pyshield_start_build",
                "description": "Start a build job after strict validation and quota deduction. This tool does NOT execute the target project's code (no manage.py check). Only target-project code errors refund quota.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["request", "confirm"],
                    "properties": {
                        "request": request_schema,
                        "expect_plan_hash": {
                            "type": "string", "pattern": r"^[0-9a-f]{64}$",
                        },
                        "idempotency_key": {
                            "type": "string", "pattern": _IDEMPOTENCY_PATTERN,
                        },
                        "confirm": {"type": "boolean", "const": True},
                    },
                },
                "annotations": {
                    "readOnlyHint": False, "destructiveHint": True,
                    "idempotentHint": False, "openWorldHint": False,
                },
            },
            {
                "name": "pyshield_cancel_build",
                "description": "Request cancellation of a running build job. Cancellation is a non-code failure and does not refund quota.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["job_id"],
                    "properties": {"job_id": _job_id_schema()},
                },
                "annotations": {
                    "readOnlyHint": False, "destructiveHint": False,
                    "idempotentHint": True, "openWorldHint": False,
                },
            },
        ])
    if allow_execution:
        tools.append({
            "name": "pyshield_check_output",
            "description": "Copy a successful build to a temporary sandbox and run the protected manage.py check with a whitelisted interpreter. This EXECUTES target project code and requires explicit server opt-in.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["job_id", "interpreter_id"],
                "properties": {
                    "job_id": _job_id_schema(),
                    "interpreter_id": {"type": "string", "minLength": 1, "maxLength": 64},
                },
            },
            "annotations": {
                "readOnlyHint": False, "destructiveHint": False,
                "idempotentHint": False, "openWorldHint": False,
            },
        })
    return tools


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ArgumentError(f"unsupported schema reference: {ref}")
    current: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ArgumentError(f"unresolvable schema reference: {ref}")
        current = current[part]
    if not isinstance(current, dict):
        raise ArgumentError(f"schema reference is not an object: {ref}")
    return current


def validate_schema(value: Any, schema: dict[str, Any], *,
                    root: dict[str, Any] | None = None, path: str = "$") -> list[str]:
    root = root or schema
    if "$ref" in schema:
        schema = _resolve_ref(root, str(schema["$ref"]))
    errors: list[str] = []
    if "anyOf" in schema:
        candidates = schema["anyOf"]
        for candidate in candidates:
            if not validate_schema(value, candidate, root=root, path=path):
                return []
        return [f"{path}: does not match any allowed shape"]
    if "oneOf" in schema:
        matches = [
            candidate for candidate in schema["oneOf"]
            if not validate_schema(value, candidate, root=root, path=path)
        ]
        return [] if len(matches) == 1 else [f"{path}: must match exactly one shape"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_matches(value, item) for item in types):
            errors.append(f"{path}: expected {'/'.join(types)}")
            return errors
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(str(schema["pattern"]), value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen = set()
            for item in value:
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if marker in seen:
                    errors.append(f"{path}: duplicate array item")
                    break
                seen.add(marker)
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema(
                    item, items, root=root, path=f"{path}[{index}]",
                ))
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for name in required:
            if name not in value:
                errors.append(f"{path}.{name}: required")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(f"{path}.{name}: unknown field")
        for name, subschema in properties.items():
            if name in value:
                errors.extend(validate_schema(
                    value[name], subschema, root=root, path=f"{path}.{name}",
                ))
    return errors


def validate_tool_arguments(tool: dict[str, Any], arguments: Any) -> dict[str, Any]:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ArgumentError("tool arguments must be a JSON object", "$")
    errors = validate_schema(arguments, tool["inputSchema"])
    if errors:
        raise ArgumentError("; ".join(errors[:12]), "$")
    return arguments
