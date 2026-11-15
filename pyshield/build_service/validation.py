"""Strict preflight validation for MCP builds."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .. import build_spec, builder, crypto, licensing, tool_license
from .models import (
    BuildPolicy,
    BuildRequestV1,
    PreparedBuild,
    ValidationIssue,
    ValidationReport,
)

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_ALLOWED_FIELDS = {
    "project", "output_name", "cipher", "integrity", "runtime", "expire",
    "grace_days", "bind_mode", "fingerprints", "customer",
    "encrypt_migrations", "encrypt_tests", "excludes", "mutable_patterns",
    "idempotency_key",
}


def _issue(field: str, code: str, message: str, severity: str = "error") -> ValidationIssue:
    return ValidationIssue(field=field, code=code, message=message, severity=severity)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string_list(value: Any, field_name: str) -> tuple[list[str], list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if value is None:
        return [], issues
    if not isinstance(value, (list, tuple)):
        return [], [_issue(field_name, "TYPE_INVALID", f"{field_name} 必须是字符串数组")]
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            issues.append(_issue(field_name, "TYPE_INVALID", f"{field_name} 只能包含字符串"))
            continue
        text = item.strip()
        if text and text not in result:
            result.append(text)
    return result, issues


def request_from_mapping(payload: Mapping[str, Any]) -> tuple[BuildRequestV1 | None, list[ValidationIssue]]:
    """Strictly coerce an untrusted JSON object into ``BuildRequestV1``."""
    issues: list[ValidationIssue] = []
    if not isinstance(payload, Mapping):
        return None, [_issue("request", "TYPE_INVALID", "request 必须是 JSON 对象")]
    unknown = sorted(set(payload) - _ALLOWED_FIELDS)
    if unknown:
        issues.append(_issue("request", "UNKNOWN_FIELD", f"未知字段：{', '.join(unknown)}"))

    project = payload.get("project")
    output_name = payload.get("output_name")
    if not isinstance(project, str):
        issues.append(_issue("project", "TYPE_INVALID", "project 必须是字符串"))
        project = ""
    if not isinstance(output_name, str):
        issues.append(_issue("output_name", "TYPE_INVALID", "output_name 必须是字符串"))
        output_name = ""

    string_fields = {
        "cipher": "auto",
        "integrity": "protected",
        "runtime": "pyc",
    }
    values: dict[str, Any] = {}
    for name, default in string_fields.items():
        value = payload.get(name, default)
        if not isinstance(value, str):
            issues.append(_issue(name, "TYPE_INVALID", f"{name} 必须是字符串"))
            value = default
        values[name] = value.strip() or default

    expire = payload.get("expire")
    if expire is not None and (
        isinstance(expire, bool) or not isinstance(expire, (str, int))
    ):
        issues.append(_issue("expire", "TYPE_INVALID", "expire 必须是天数或 YYYY-MM-DD"))
        expire = None

    grace = payload.get("grace_days", 0)
    if not _is_int(grace):
        issues.append(_issue("grace_days", "TYPE_INVALID", "grace_days 必须是整数"))
        grace = 0

    bind_mode = payload.get("bind_mode", "any")
    if not isinstance(bind_mode, str):
        issues.append(_issue("bind_mode", "TYPE_INVALID", "bind_mode 必须是字符串"))
        bind_mode = "any"

    fingerprints, fp_issues = _string_list(payload.get("fingerprints", []), "fingerprints")
    issues.extend(fp_issues)

    customer = payload.get("customer", "")
    if customer is None:
        customer = ""
    if not isinstance(customer, str):
        issues.append(_issue("customer", "TYPE_INVALID", "customer 必须是字符串"))
        customer = ""

    booleans: dict[str, bool] = {}
    for name in ("encrypt_migrations", "encrypt_tests"):
        value = payload.get(name, False)
        if not _is_bool(value):
            issues.append(_issue(name, "TYPE_INVALID", f"{name} 必须是布尔值"))
            value = False
        booleans[name] = value

    excludes, exclude_issues = _string_list(payload.get("excludes", []), "excludes")
    issues.extend(exclude_issues)
    mutable, mutable_issues = _string_list(
        payload.get("mutable_patterns", []), "mutable_patterns",
    )
    issues.extend(mutable_issues)

    idempotency = payload.get("idempotency_key")
    if idempotency is not None and not isinstance(idempotency, str):
        issues.append(_issue("idempotency_key", "TYPE_INVALID", "idempotency_key 必须是字符串"))
        idempotency = None

    if issues:
        return None, issues
    return BuildRequestV1(
        project=project,
        output_name=output_name,
        cipher=values["cipher"],
        integrity=values["integrity"],
        runtime=values["runtime"],
        expire=str(expire) if expire is not None else None,
        grace_days=grace,
        bind_mode=bind_mode,
        fingerprints=fingerprints,
        customer=customer,
        encrypt_migrations=booleans["encrypt_migrations"],
        encrypt_tests=booleans["encrypt_tests"],
        excludes=excludes,
        mutable_patterns=mutable,
        idempotency_key=idempotency,
    ), []


def _select_project(project: str, policy: BuildPolicy) -> tuple[Path | None, ValidationIssue | None]:
    relative_issue = build_spec.validate_project_relative(project)
    if relative_issue:
        return None, ValidationIssue(
            field=relative_issue.field, code=relative_issue.code,
            message=relative_issue.message,
        )
    last_error: ValidationIssue | None = None
    for root in policy.project_roots:
        raw_candidate = root / project
        if build_spec.path_has_reparse(raw_candidate, stop=root):
            last_error = _issue(
                "project", "PATH_REPARSE", "project 或其父目录是链接/重解析点",
            )
            continue
        candidate = raw_candidate.resolve(strict=False)
        if not build_spec.is_within(candidate, root):
            last_error = _issue("project", "PATH_ESCAPE", "project 必须位于 project_root 内")
            continue
        if not candidate.exists():
            last_error = _issue("project", "PROJECT_MISSING", f"项目不存在：{project}")
            continue
        if not candidate.is_dir():
            last_error = _issue("project", "PATH_NOT_DIR", f"项目不是目录：{project}")
            continue
        return candidate, None
    return None, last_error or _issue("project", "PROJECT_MISSING", f"项目不存在：{project}")


def _normalize_expire(value: str | int | None, today: date) -> tuple[str | None, ValidationIssue | None]:
    if value is None or str(value).strip() == "":
        return None, None
    text = str(value).strip()
    if text.isdigit():
        days = int(text)
        if not 1 <= days <= build_spec.MAX_EXPIRE_DAYS:
            return None, _issue(
                "expire", "EXPIRE_RANGE",
                f"有效天数必须在 1 到 {build_spec.MAX_EXPIRE_DAYS} 之间",
            )
        return str(days), None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None, _issue("expire", "EXPIRE_INVALID", "expire 必须是天数或 YYYY-MM-DD")
    if parsed < today:
        return None, _issue("expire", "EXPIRE_IN_PAST", "到期日期不能早于今天")
    return parsed.isoformat(), None


def license_status_summary(status: tool_license.ToolLicenseStatus) -> dict:
    return _license_summary(status)


def _license_summary(status: tool_license.ToolLicenseStatus) -> dict:
    entitlement = status.entitlement
    reason = str(status.reason or "")
    if status.machine:
        reason = reason.replace(status.machine, "<machine>")
    data = {
        "mode": status.mode,
        "valid": status.valid,
        "code": status.code,
        "reason": reason,
        "edition": entitlement.edition if entitlement else (
            "trial" if status.mode == "trial" else None
        ),
        "features": list(entitlement.features) if entitlement else [],
        "expires": entitlement.expires.isoformat() if entitlement and entitlement.expires else None,
    }
    if status.trial:
        data["trial"] = {
            "days_left": status.trial.get("days_left"),
            "builds_left": status.trial.get("builds_left"),
            "not_started": bool(status.trial.get("not_started")),
        }
    if status.quota:
        data["quota"] = {
            "used": status.quota.get("used", 0),
            "limit": status.quota.get("limit"),
        }
    return data


def _quota_summary(status: tool_license.ToolLicenseStatus) -> dict:
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


def request_fingerprint(request: BuildRequestV1, policy: BuildPolicy) -> str:
    """Stable fingerprint for idempotency-key conflict detection."""
    project, _issue = _select_project(request.project, policy)
    output = policy.output_root / request.output_name
    payload = {
        **asdict(request),
        "project_path": str(project) if project is not None else request.project,
        "output_path": str(output),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_hash(prepared: PreparedBuild) -> str:
    request = prepared.request
    payload = {
        "version": 1,
        "project": str(prepared.project),
        "output": str(prepared.output),
        "cipher": prepared.effective_cipher,
        "runtime": prepared.effective_runtime,
        "integrity": request.integrity,
        "expire": request.expire,
        "grace_days": request.grace_days,
        "bind_mode": request.bind_mode,
        "fingerprints": sorted(request.fingerprints),
        "customer": request.customer,
        "encrypt_migrations": request.encrypt_migrations,
        "encrypt_tests": request.encrypt_tests,
        "excludes": sorted(request.excludes),
        "mutable_patterns": sorted(request.mutable_patterns),
        "allow_runtime_source": prepared.policy.allow_runtime_source,
        "allow_sensitive_files": prepared.policy.allow_sensitive_files,
        "ci": prepared.policy.ci,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare(request: BuildRequestV1, policy: BuildPolicy,
            today: date | None = None) -> tuple[PreparedBuild | None, ValidationReport]:
    """Validate a request and build ``BuildOptions`` without side effects."""
    today = today or date.today()
    issues: list[ValidationIssue] = []
    warnings: list[str] = []

    project, project_issue = _select_project(request.project, policy)
    if project_issue:
        issues.append(project_issue)

    output_issue = build_spec.validate_output_name(request.output_name)
    if output_issue:
        issues.append(ValidationIssue(
            field=output_issue.field, code=output_issue.code,
            message=output_issue.message,
        ))
    output = policy.output_root / request.output_name if request.output_name else policy.output_root
    if request.output_name and not build_spec.is_within(output, policy.output_root):
        issues.append(_issue("output_name", "OUTPUT_ESCAPE", "输出路径越出 output_root"))
    if build_spec.normpath(output) == build_spec.normpath(policy.output_root):
        issues.append(_issue("output_name", "OUTPUT_INVALID", "输出目录不能等于 output_root"))
    if output.exists():
        issues.append(_issue("output_name", "OUTPUT_EXISTS", f"输出目录已存在：{request.output_name}"))
    if not output.parent.exists():
        issues.append(_issue("output_name", "OUTPUT_PARENT_MISSING", "输出根目录不存在"))
    elif build_spec.is_reparse_point(output.parent):
        issues.append(_issue("output_name", "PATH_REPARSE", "输出父目录是链接/重解析点"))

    if project:
        for label, other in (
            ("output_root", policy.output_root),
            ("secret_dir", policy.secret_dir),
            ("job_dir", policy.job_dir),
            ("audit_dir", policy.audit_dir),
        ):
            if build_spec.is_within(project, other) or build_spec.is_within(other, project):
                issues.append(_issue(
                    "project", "ROOT_OVERLAP",
                    f"project 与 {label} 不能互相包含",
                ))
        if build_spec.is_within(output, project) or build_spec.is_within(project, output):
            issues.append(_issue(
                "output_name", "ROOT_OVERLAP",
                "输出目录不能位于项目内，也不能包含项目",
            ))

    scan = None
    if project:
        try:
            scan = build_spec.scan_project(
                project,
                max_files=policy.max_files,
                max_bytes=policy.max_bytes,
                max_depth=policy.max_depth,
                allow_sensitive=True,
            )
        except build_spec.SpecError as exc:
            issues.append(_issue("project", exc.code, exc.message))
        else:
            if scan.sensitive_files:
                message = "项目包含敏感文件：" + ", ".join(scan.sensitive_files[:8])
                if len(scan.sensitive_files) > 8:
                    message += "…"
                if policy.allow_sensitive_files:
                    warnings.append(message + "（已显式允许）")
                else:
                    issues.append(_issue("project", "SENSITIVE_FILE", message))
            syntax_issues = build_spec.check_python_syntax(project, scan.files)
            issues.extend(
                ValidationIssue(field=item.field, code=item.code, message=item.message)
                for item in syntax_issues
            )

    cipher_issue = build_spec.validate_cipher(request.cipher)
    if cipher_issue:
        issues.append(ValidationIssue(
            field=cipher_issue.field, code=cipher_issue.code, message=cipher_issue.message,
        ))
    integrity_issue = build_spec.validate_integrity(request.integrity)
    if integrity_issue:
        issues.append(ValidationIssue(
            field=integrity_issue.field, code=integrity_issue.code,
            message=integrity_issue.message,
        ))
    runtime_mode = "pyc" if request.runtime == "auto" else request.runtime
    runtime_issue = build_spec.validate_runtime(
        runtime_mode, allow_source=policy.allow_runtime_source,
    )
    if runtime_issue:
        issues.append(ValidationIssue(
            field=runtime_issue.field, code=runtime_issue.code,
            message=runtime_issue.message,
        ))
    expire, expire_issue = _normalize_expire(request.expire, today)
    if expire_issue:
        issues.append(expire_issue)
    grace_issue = build_spec.validate_grace_days(request.grace_days)
    if grace_issue:
        issues.append(ValidationIssue(
            field=grace_issue.field, code=grace_issue.code, message=grace_issue.message,
        ))
    issues.extend(build_spec.validate_bind_mode(request.bind_mode, request.fingerprints))
    customer_issue = build_spec.validate_customer(request.customer)
    if customer_issue:
        issues.append(ValidationIssue(
            field=customer_issue.field, code=customer_issue.code,
            message=customer_issue.message,
        ))
    issues.extend(build_spec.validate_patterns(request.excludes, "excludes"))
    issues.extend(build_spec.validate_patterns(request.mutable_patterns, "mutable_patterns"))
    if request.idempotency_key and not _IDEMPOTENCY_RE.fullmatch(request.idempotency_key):
        issues.append(_issue(
            "idempotency_key", "IDEMPOTENCY_INVALID",
            "idempotency_key 应为 8-128 位字母、数字、点、下划线或短横线",
        ))

    features: list[str] = []
    if runtime_mode == "native":
        features.append(tool_license.FEATURE_NATIVE_RUNTIME)
    if policy.ci:
        features.append(tool_license.FEATURE_CI)
    status = tool_license.check(features=features, initialize_trial=False)
    request_cipher = request.cipher
    trial_edition = False
    if not status.valid:
        code = "QUOTA_EXHAUSTED" if status.code == "quota_exhausted" else "LICENSE_INVALID"
        issues.append(_issue("license", code, status.reason))
    else:
        trial_edition = (
            status.mode == "trial"
            or (status.entitlement is not None and status.entitlement.edition == tool_license.EDITION_TRIAL)
        )
        if trial_edition:
            if request_cipher == "auto":
                request_cipher = "chacha-pure"
            for problem in tool_license.trial_build_problems(
                request_cipher, request.integrity, request.customer,
            ):
                issues.append(_issue("license", "TRIAL_RESTRICTED", problem))
        if runtime_mode == "native":
            available, reason = builder.native_runtime_available()
            if not available:
                issues.append(_issue("runtime", "NATIVE_UNAVAILABLE", f"原生运行时不可用：{reason}"))

    try:
        effective_cipher = crypto.resolve_cipher(request_cipher)
    except ValueError as exc:
        issues.append(_issue("cipher", "CIPHER_UNAVAILABLE", str(exc)))
        effective_cipher = request_cipher

    modules = 0
    if project:
        try:
            patterns = builder.default_excludes(
                request.encrypt_migrations, request.encrypt_tests,
            ) + list(request.excludes)
            modules = len(builder.discover_modules(project, patterns))
        except builder.BuildError as exc:
            issues.append(_issue("project", "PROJECT_MODULES", str(exc)))

    if scan is not None:
        required = scan.total_bytes * 2 + 64 * 1024 * 1024
        free = build_spec.free_bytes(policy.output_root)
        if free is not None and free < required:
            issues.append(_issue(
                "output", "DISK_SPACE",
                f"输出根剩余空间不足（需要约 {required} 字节，剩余 {free} 字节）",
            ))
    if not os.access(policy.output_root, os.W_OK):
        issues.append(_issue("output", "OUTPUT_NOT_WRITABLE", "输出根目录不可写"))
    if request.runtime == "source" and policy.allow_runtime_source:
        warnings.append("source runtime 会把解密逻辑以明文发布，仅用于调试")

    report = ValidationReport(
        ok=not any(item.severity == "error" for item in issues),
        issues=issues,
        warnings=warnings,
        output_name=request.output_name,
        effective_cipher=effective_cipher,
        effective_runtime=runtime_mode,
        license=_license_summary(status),
        quota=_quota_summary(status),
        modules=modules,
        files=len(scan.files) if scan else 0,
        total_bytes=scan.total_bytes if scan else 0,
    )
    if not report.ok or project is None:
        return None, report

    options = builder.BuildOptions(
        project=str(project),
        output=str(output),
        excludes=list(request.excludes),
        mutable_patterns=list(request.mutable_patterns),
        encrypt_migrations=request.encrypt_migrations,
        encrypt_tests=request.encrypt_tests,
        expire=expire,
        grace_days=request.grace_days,
        bind_device=list(request.fingerprints) if request.bind_mode == "fixed" else [],
        bind_first_run=request.bind_mode == "first-run",
        cipher=request_cipher,
        customer=request.customer or None,
        integrity=request.integrity,
        runtime_pyc=runtime_mode == "pyc",
        runtime_source=runtime_mode == "source",
        runtime_native=runtime_mode == "native",
        no_verify=True,
        force=False,
        verify_timeout=policy.check_timeout,
        console_log=False,
        require_absent_output=True,
        tool_info=status.watermark(),
    )
    prepared = PreparedBuild(
        request=request,
        policy=policy,
        project=project,
        output=output,
        options=options,
        license_status=status,
        effective_cipher=effective_cipher,
        effective_runtime=runtime_mode,
        plan_hash="",
        warnings=warnings,
        modules=modules,
        files=len(scan.files),
        total_bytes=scan.total_bytes,
    )
    prepared.plan_hash = _plan_hash(prepared)
    report.plan_hash = prepared.plan_hash
    return prepared, report


def inspect_project(project_name: str, policy: BuildPolicy) -> dict:
    """Return read-only project metadata; never executes project code."""
    project, issue = _select_project(project_name, policy)
    if issue:
        return {"ok": False, "issues": [issue.to_dict()]}
    assert project is not None
    try:
        scan = build_spec.scan_project(
            project,
            max_files=policy.max_files,
            max_bytes=policy.max_bytes,
            max_depth=policy.max_depth,
            allow_sensitive=True,
        )
    except build_spec.SpecError as exc:
        return {
            "ok": False,
            "issues": [ValidationIssue(exc.field, exc.code, exc.message).to_dict()],
        }
    syntax = build_spec.check_python_syntax(project, scan.files)
    status = tool_license.check(initialize_trial=False)
    native_available, native_reason = builder.native_runtime_available()
    try:
        packages = len(builder.find_packages(project))
        modules = len(builder.discover_modules(project, builder.default_excludes()))
    except builder.BuildError:
        packages = 0
        modules = 0
    return {
        "ok": True,
        "project": project_name,
        "manage_py": True,
        "packages": packages,
        "modules": modules,
        "files": len(scan.files),
        "total_bytes": scan.total_bytes,
        "max_depth": scan.max_depth,
        "sensitive_files": scan.sensitive_files[:20],
        "syntax_issues": [item.message for item in syntax],
        "license": _license_summary(status),
        "native_runtime": {"available": native_available, "reason": native_reason},
    }
