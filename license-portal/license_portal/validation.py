"""Input validation and normalization for the license portal.

Every public form and every administration action passes through this module
before it reaches the service layer.  The rules are intentionally strict and
server-side authoritative; browser hints are only a convenience.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import date

from pyshield import license_admin, tool_license, trial_license

CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
KEY_PREFIXES = {
    "PST": "trial",
    "PSP": "pro",
    "PSE": "enterprise",
}
LICENSE_KEY_BODY = 16
TRIAL_CODE_BODY = 12

_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_MACHINE_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_ORG_TYPES = {"individual", "company"}
_FEATURE_NAMES = set(tool_license.ALL_FEATURES)

# Characters that make names look identical but behave differently in logs,
# exports, e-mails and support tools.  Reject them instead of normalizing them.
_FORBIDDEN_TEXT = {
    "​", "‌", "‍", "⁠", "﻿",
    "‎", "‏", "‪", "‫", "‬", "‭", "‮",
    "⁦", "⁧", "⁨", "⁩",
}


class ValidationError(Exception):
    """Raised when one or more submitted fields are invalid."""

    def __init__(self, errors: dict[str, str], message: str = "请检查填写的内容") -> None:
        super().__init__(message)
        self.errors = dict(errors)
        self.message = message


def _error(field: str, message: str) -> None:
    raise ValidationError({field: message})


def _text(
    value: object,
    field: str,
    label: str,
    minimum: int,
    maximum: int,
    *,
    allow_newlines: bool = False,
) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if not text:
        _error(field, f"请填写{label}")
    if len(text) < minimum:
        _error(field, f"{label}至少需要 {minimum} 个字符")
    if len(text) > maximum:
        _error(field, f"{label}不能超过 {maximum} 个字符")
    for char in text:
        category = unicodedata.category(char)
        if char in _FORBIDDEN_TEXT:
            _error(field, f"{label}包含不可见字符")
        if category in {"Cc", "Cs", "Co", "Cn"}:
            if allow_newlines and char in {"\n", "\r", "\t"}:
                continue
            _error(field, f"{label}包含非法字符")
    return text


def normalize_email(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        _error("email", "请填写邮箱")
    if len(text) > 254:
        _error("email", "邮箱地址过长")
    local, _, domain = text.partition("@")
    if not local or not domain or len(local) > 64:
        _error("email", "邮箱格式不正确")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        _error("email", "邮箱格式不正确")
    if not _EMAIL_RE.match(text):
        _error("email", "邮箱格式不正确")
    return text


def normalize_phone(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        _error("phone", "请填写手机号")
    text = re.sub(r"[\s\-()（）]", "", raw)
    if text.startswith("00"):
        text = "+" + text[2:]
    if text.startswith("+"):
        digits = text[1:]
        if not digits.isdigit() or not (8 <= len(digits) <= 15):
            _error("phone", "手机号格式不正确")
        return "+" + digits
    if not text.isdigit():
        _error("phone", "手机号格式不正确")
    if 11 <= len(text) <= 15:
        # Mainland China mobile numbers are the default; longer digit strings
        # are accepted as international numbers without a plus sign.
        if len(text) == 11 and not re.match(r"^1[3-9]\d{9}$", text):
            _error("phone", "手机号格式不正确")
        return text
    _error("phone", "手机号格式不正确")
    return text


def normalize_name(value: object) -> str:
    return _text(value, "name", "姓名或称呼", 2, 40)


def normalize_org_type(value: object) -> str:
    text = str(value or "").strip().lower()
    if text not in _ORG_TYPES:
        _error("org_type", "请选择个人或公司")
    return text


def normalize_company_name(value: object, org_type: str) -> str | None:
    if org_type == "individual":
        # Ignore anything the browser may have submitted for the other branch.
        return None
    return _text(value, "company_name", "公司名称", 2, 100)


def normalize_serial(value: object) -> str:
    try:
        return trial_license.normalize_serial(str(value or ""))
    except trial_license.TrialError as exc:
        raise ValidationError({"serial": str(exc)}) from exc


def normalize_machine(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        _error("machine", "缺少机器码，请从软件内打开本页面")
    if not _MACHINE_RE.match(text):
        _error("machine", "机器码格式不正确，请从软件内重新打开本页面")
    return text


def normalize_license_key(value: object) -> str:
    """Normalize a human-entered activation key.

    Accepts lower case, missing hyphens, and the common OCR confusions
    ``O -> 0`` and ``I/L -> 1``.  The server remains the authority on whether
    the key exists.
    """
    raw = str(value or "").strip().upper()
    if not raw:
        _error("key", "请输入激活码")
    cleaned = "".join(char for char in raw if char.isalnum())
    if len(cleaned) < 3:
        _error("key", "激活码格式不正确")
    for char in cleaned:
        if char in {"O", "I", "L"}:
            cleaned = cleaned.replace("O", "0").replace("I", "1").replace("L", "1")
            break
    prefix = cleaned[:3]
    body = cleaned[3:]
    if prefix not in KEY_PREFIXES:
        _error("key", "激活码格式不正确")
    if len(body) != LICENSE_KEY_BODY:
        _error("key", "激活码长度不正确")
    if any(char not in CROCKFORD_ALPHABET for char in body):
        _error("key", "激活码包含非法字符")
    groups = [body[index:index + 4] for index in range(0, len(body), 4)]
    return prefix + "-" + "-".join(groups)


def normalize_trial_code(value: object) -> str:
    raw = str(value or "").strip().upper()
    cleaned = "".join(char for char in raw if char.isalnum())
    if len(cleaned) != 3 + TRIAL_CODE_BODY:
        _error("code", "试用码格式不正确")
    prefix, body = cleaned[:3], cleaned[3:]
    if prefix != "PST" or any(char not in CROCKFORD_ALPHABET for char in body):
        _error("code", "试用码格式不正确")
    groups = [body[index:index + 4] for index in range(0, len(body), 4)]
    return "PST-" + "-".join(groups)


def generate_license_key(edition: str) -> str:
    prefix = {"trial": "PST", "pro": "PSP", "enterprise": "PSE"}.get(edition)
    if prefix is None:
        raise ValueError(f"unknown edition {edition!r}")
    body = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(LICENSE_KEY_BODY))
    groups = [body[index:index + 4] for index in range(0, len(body), 4)]
    return prefix + "-" + "-".join(groups)


def generate_trial_code() -> str:
    body = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(TRIAL_CODE_BODY))
    groups = [body[index:index + 4] for index in range(0, len(body), 4)]
    return "PST-" + "-".join(groups)


def looks_like_license_key(value: object) -> bool:
    try:
        normalize_license_key(value)
    except ValidationError:
        return False
    return True


def validate_trial_application(payload: dict) -> dict:
    errors: dict[str, str] = {}
    result: dict = {}

    def capture(field: str, func, *args, **kwargs):
        try:
            result[field] = func(*args, **kwargs)
        except ValidationError as exc:
            errors.update(exc.errors)

    capture("serial", normalize_serial, payload.get("serial"))
    capture("machine", normalize_machine, payload.get("machine"))
    capture("email", normalize_email, payload.get("email"))
    capture("phone", normalize_phone, payload.get("phone"))
    capture("name", normalize_name, payload.get("name"))
    capture("org_type", normalize_org_type, payload.get("org_type"))
    if "org_type" in result:
        try:
            result["company_name"] = normalize_company_name(
                payload.get("company_name"), result["org_type"],
            )
        except ValidationError as exc:
            errors.update(exc.errors)

    consent = str(payload.get("consent") or "").strip().lower()
    if consent not in {"1", "true", "yes", "on", "同意"}:
        errors["consent"] = "请先阅读并同意隐私政策与服务条款"

    if errors:
        raise ValidationError(errors)
    result["consent"] = True
    return result


def validate_email_code(payload: dict) -> dict:
    errors: dict[str, str] = {}
    try:
        application_id = int(str(payload.get("application_id") or "").strip())
        if application_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors["application_id"] = "申请编号无效"
        application_id = 0
    code = str(payload.get("code") or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        errors["code"] = "请输入 6 位邮箱验证码"
    if errors:
        raise ValidationError(errors)
    return {"application_id": application_id, "code": code}


def validate_resend_request(payload: dict) -> dict:
    try:
        application_id = int(str(payload.get("application_id") or "").strip())
        if application_id <= 0:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValidationError({"application_id": "申请编号无效"}) from exc
    return {"application_id": application_id}


def validate_upgrade_request(payload: dict) -> dict:
    errors: dict[str, str] = {}
    result: dict = {}
    edition = str(payload.get("edition") or "").strip().lower()
    if edition not in {"pro", "enterprise"}:
        errors["edition"] = "请选择专业版或企业版"
    else:
        result["edition"] = edition
    serial_raw = str(payload.get("serial") or "").strip()
    if serial_raw:
        try:
            result["serial"] = normalize_serial(serial_raw)
        except ValidationError as exc:
            errors.update(exc.errors)
    else:
        result["serial"] = ""
    try:
        result["contact"] = _text(payload.get("contact"), "contact", "联系方式", 2, 120)
    except ValidationError as exc:
        errors.update(exc.errors)
    try:
        result["notes"] = _text(
            payload.get("notes"), "notes", "备注", 0, 1000, allow_newlines=True,
        ) if str(payload.get("notes") or "").strip() else ""
    except ValidationError as exc:
        errors.update(exc.errors)
    if errors:
        raise ValidationError(errors)
    return result


def validate_login(payload: dict) -> dict:
    errors: dict[str, str] = {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or len(username) > 64:
        errors["username"] = "请输入用户名"
    if not password or len(password) > 256:
        errors["password"] = "请输入密码"
    if errors:
        raise ValidationError(errors)
    return {"username": username, "password": password}


def validate_key_issue(payload: dict, *, default_valid_days: int = 30) -> dict:
    errors: dict[str, str] = {}
    result: dict = {}

    edition = str(payload.get("edition") or "pro").strip().lower()
    if edition not in tool_license.EDITIONS:
        errors["edition"] = "未知版本"
    else:
        result["edition"] = edition

    def integer(field: str, label: str, default: int, minimum: int, maximum: int) -> int:
        raw = payload.get(field)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            number = int(str(raw).strip())
        except (TypeError, ValueError):
            errors[field] = f"{label}必须是整数"
            return default
        if number < minimum or number > maximum:
            errors[field] = f"{label}必须在 {minimum}–{maximum} 之间"
        return number

    result["days"] = integer("days", "有效期天数", 365, 1, 3650)
    result["max_builds"] = integer("max_builds", "每月构建次数", 100, 0, 100000)
    result["seats"] = integer("seats", "席位数", 1, 1, 10000)
    result["max_activations"] = integer("max_activations", "可激活设备数", 1, 1, 10000)
    result["grace_days"] = integer("grace_days", "宽限天数", 14, 0, 3650)
    result["quantity"] = integer("quantity", "生成数量", 1, 1, 200)
    result["redemption_days"] = integer(
        "redemption_days", "兑换期限天数", default_valid_days, 1, 3650,
    )

    notes_raw = str(payload.get("notes") or "").strip()
    if notes_raw:
        try:
            result["notes"] = _text(notes_raw, "notes", "备注", 1, 500, allow_newlines=True)
        except ValidationError as exc:
            errors.update(exc.errors)
    else:
        result["notes"] = ""

    batch_name = str(payload.get("batch_name") or "").strip()
    if batch_name:
        try:
            result["batch_name"] = _text(batch_name, "batch_name", "批次名称", 1, 100)
        except ValidationError as exc:
            errors.update(exc.errors)
    else:
        result["batch_name"] = ""

    features_raw = str(payload.get("features") or "").strip()
    if features_raw:
        items = [item.strip() for item in re.split(r"[,;，；]", features_raw) if item.strip()]
        unknown = [item for item in items if item not in _FEATURE_NAMES and item != "*"]
        if unknown:
            errors["features"] = f"未知功能项：{', '.join(unknown)}"
        result["features"] = list(dict.fromkeys(items)) if items else []
        if not items and "edition" in result:
            result["features"] = list(license_admin.EDITION_FEATURES.get(result["edition"], []))
    elif "edition" in result:
        result["features"] = list(license_admin.EDITION_FEATURES.get(result["edition"], []))
    else:
        result["features"] = []

    if errors:
        raise ValidationError(errors)
    return result


def validate_renewal(payload: dict, *, default_days: int = 365) -> dict:
    errors: dict[str, str] = {}
    result: dict = {}

    def optional_int(field: str, label: str, minimum: int, maximum: int):
        raw = payload.get(field)
        if raw is None or str(raw).strip() == "":
            return None
        try:
            number = int(str(raw).strip())
        except (TypeError, ValueError):
            errors[field] = f"{label}必须是整数"
            return None
        if number < minimum or number > maximum:
            errors[field] = f"{label}必须在 {minimum}–{maximum} 之间"
        return number

    days = optional_int("days", "续费天数", 1, 3650)
    if days is None:
        days = default_days
    result["days"] = days

    expires_raw = str(payload.get("expires") or "").strip()
    if expires_raw:
        try:
            expires = date.fromisoformat(expires_raw)
        except ValueError:
            errors["expires"] = "到期日期格式应为 YYYY-MM-DD"
            expires = None
        if expires is not None and expires < date.today():
            errors["expires"] = "到期日期不能早于今天"
        result["expires"] = expires
    else:
        result["expires"] = None

    result["max_builds"] = optional_int("max_builds", "每月构建次数", 0, 100000)
    result["grace_days"] = optional_int("grace_days", "宽限天数", 0, 3650)

    notes = str(payload.get("notes") or "").strip()
    if notes:
        try:
            result["notes"] = _text(notes, "notes", "备注", 1, 500, allow_newlines=True)
        except ValidationError as exc:
            errors.update(exc.errors)
    else:
        result["notes"] = ""

    if errors:
        raise ValidationError(errors)
    return result


def validate_edition_change(payload: dict, *, default_days: int = 365) -> dict:
    errors: dict[str, str] = {}
    result: dict = {}
    edition = str(payload.get("edition") or "").strip().lower()
    if edition not in {"pro", "enterprise"}:
        errors["edition"] = "请选择专业版或企业版"
    else:
        result["edition"] = edition
    try:
        renewal = validate_renewal(payload, default_days=default_days)
    except ValidationError as exc:
        errors.update(exc.errors)
        renewal = {"days": default_days, "expires": None, "max_builds": None,
                   "grace_days": None, "notes": ""}
    result.update(renewal)
    result["seats"] = None
    raw_seats = payload.get("seats")
    if raw_seats is not None and str(raw_seats).strip():
        try:
            seats = int(str(raw_seats).strip())
            if seats < 1 or seats > 10000:
                errors["seats"] = "席位数必须在 1–10000 之间"
            result["seats"] = seats
        except (TypeError, ValueError):
            errors["seats"] = "席位数必须是整数"
    if errors:
        raise ValidationError(errors)
    return result


def mask_email(value: str) -> str:
    local, _, domain = str(value or "").partition("@")
    if not local or not domain:
        return str(value or "")
    if len(local) <= 2:
        masked = local[0] + "*"
    else:
        masked = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked}@{domain}"


def mask_phone(value: str) -> str:
    text = str(value or "")
    digits = re.sub(r"\D", "", text)
    if len(digits) < 7:
        return text
    return f"{digits[:3]}****{digits[-4:]}"


def is_expired(expires: str | None, *, grace_days: int = 0, today_: date | None = None) -> bool:
    if not expires:
        return False
    try:
        expiry = date.fromisoformat(str(expires))
    except ValueError:
        return True
    from datetime import timedelta

    return (today_ or date.today()) > expiry + timedelta(days=max(0, int(grace_days)))
