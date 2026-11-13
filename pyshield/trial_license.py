"""Client-side trial registration for the PyShield desktop builder.

The desktop app is distributed as a single executable. On first launch it
derives a stable serial number from the machine fingerprint plus a random salt,
shows it in the UI, and opens the trial site with that serial attached. The
site registers the request and issues a short-lived, machine-bound trial
entitlement; the app polls for it (or receives a ``pyshield://`` deep link) and
installs it as the regular tool license.

This module deliberately reuses :mod:`pyshield.tool_license`: a trial grant is
an ordinary signed tool entitlement with ``edition="trial"``, limited features,
a 14 day expiry and a small build quota.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timezone
from pathlib import Path

from . import __version__, fingerprint, secure_store, tool_license

INSTALL_FORMAT = "pyshield-install/1"
INSTALL_FILE = "install.json"
ENV_TRIAL_SERVER = "PYSHELD_TRIAL_SERVER"
MAX_RESPONSE_BYTES = 1024 * 1024
SERIAL_PREFIX = "PS"
SERIAL_GROUPS = 3
SERIAL_GROUP_SIZE = 4
_SERIAL_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
DEEP_LINK_SCHEME = "pyshield"


class TrialError(Exception):
    """Raised when the trial registration flow cannot be completed."""


def install_path() -> Path:
    return tool_license.state_dir() / INSTALL_FILE


def _machine() -> str:
    return fingerprint.machine_fingerprint().lower()


def serial_from(machine: str, salt: str) -> str:
    """Derive a human readable serial from the machine fingerprint and salt."""
    digest = hashlib.sha256(f"pyshield-serial-v1|{machine}|{salt}".encode("utf-8")).digest()
    chars: list[str] = []
    value = 0
    bits = 0
    for byte in digest:
        value = (value << 8) | byte
        bits += 8
        while bits >= 5 and len(chars) < SERIAL_GROUPS * SERIAL_GROUP_SIZE:
            bits -= 5
            chars.append(_SERIAL_ALPHABET[(value >> bits) & 0x1F])
    body = "".join(chars)
    groups = [body[i:i + SERIAL_GROUP_SIZE] for i in range(0, len(body), SERIAL_GROUP_SIZE)]
    return f"{SERIAL_PREFIX}-" + "-".join(groups)


def normalize_serial(value: str) -> str:
    """Accept ``psabcdef...`` or the formatted form and return the canonical one."""
    text = "".join(ch for ch in str(value or "").upper() if ch.isalnum())
    if text.startswith(SERIAL_PREFIX):
        text = text[len(SERIAL_PREFIX):]
    if len(text) != SERIAL_GROUPS * SERIAL_GROUP_SIZE:
        raise TrialError("序列号格式不正确，应为 PS-XXXX-XXXX-XXXX")
    if any(ch not in _SERIAL_ALPHABET for ch in text):
        raise TrialError("序列号包含非法字符")
    groups = [text[i:i + SERIAL_GROUP_SIZE] for i in range(0, len(text), SERIAL_GROUP_SIZE)]
    return f"{SERIAL_PREFIX}-" + "-".join(groups)


def install_state(path: str | Path | None = None) -> dict:
    """Load or create the local install record (stable serial per machine)."""
    target = Path(path) if path is not None else install_path()
    raw = secure_store.read_private(target)
    if raw:
        try:
            state = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrialError(f"本机安装记录已损坏：{target}") from exc
        if not isinstance(state, dict) or state.get("format") != INSTALL_FORMAT:
            raise TrialError(f"本机安装记录格式不支持：{target}")
        state["serial"] = normalize_serial(state.get("serial"))
        state.setdefault("machine", _machine())
        return state

    machine = _machine()
    state = {
        "format": INSTALL_FORMAT,
        "serial": serial_from(machine, secrets.token_hex(8)),
        "machine": machine,
        "salt": secrets.token_hex(8),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    secure_store.write_private(
        target, json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n",
    )
    return state


def local_serial() -> str:
    return install_state()["serial"]


# ---------------------------------------------------------------------------
# trial site
# ---------------------------------------------------------------------------


def default_server() -> str:
    """Resolve the trial site: environment first, then the build-time default."""
    value = os.environ.get(ENV_TRIAL_SERVER, "").strip().rstrip("/")
    if value:
        return value
    try:
        from . import _site  # type: ignore
    except ImportError:
        return ""
    return str(getattr(_site, "DEFAULT_SITE", "") or "").strip().rstrip("/")


def _validate_server(server: str) -> str:
    value = (server or "").strip().rstrip("/")
    if not value:
        raise TrialError("试用服务地址未配置")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TrialError(f"试用服务地址无效：{value}")
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local:
        raise TrialError("试用服务必须使用 HTTPS")
    return value


def handshake_url(server: str, edition: str = "trial", source: str = "app") -> str:
    """Build the site URL that carries the serial as an input parameter."""
    base = _validate_server(server)
    state = install_state()
    query = urllib.parse.urlencode({
        "serial": state["serial"],
        "machine": state["machine"],
        "edition": edition,
        "source": source,
        "version": __version__,
    })
    return f"{base}/activate?{query}"


def deep_link(serial: str) -> str:
    return f"{DEEP_LINK_SCHEME}://activate?{urllib.parse.urlencode({'serial': normalize_serial(serial)})}"


def open_site(server: str, edition: str = "trial") -> str:
    url = handshake_url(server, edition=edition)
    if not webbrowser.open(url):
        raise TrialError(f"无法打开浏览器，请手动访问：{url}")
    return url


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _request(method: str, server: str, path: str, payload: dict | None, timeout: float) -> dict:
    base = _validate_server(server)
    data = None
    headers = {"Accept": "application/json", "User-Agent": f"PyShield/{__version__}"}
    if payload is not None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise TrialError(f"试用服务返回 HTTP {exc.code}：{detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TrialError(f"无法连接试用服务：{exc}") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise TrialError("试用服务响应过大")
    try:
        document = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TrialError("试用服务返回了无效响应") from exc
    if not isinstance(document, dict):
        raise TrialError("试用服务返回了无效响应")
    return document


def fetch_status(server: str, serial: str | None = None, timeout: float = 10.0) -> dict:
    state = install_state()
    query = urllib.parse.urlencode({
        "serial": normalize_serial(serial or state["serial"]),
        "machine": state["machine"],
    })
    return _request("GET", server, f"/api/trial/status?{query}", None, timeout)


def claim(server: str, machine: str | None = None, timeout: float = 15.0) -> dict:
    """Ask the site for the signed trial entitlement of this machine."""
    state = install_state()
    payload = {
        "serial": state["serial"],
        "machine": machine or state["machine"],
        "version": __version__,
    }
    response = _request("POST", server, "/api/trial/claim", payload, timeout)
    document = response.get("document")
    if not isinstance(document, dict):
        raise TrialError(str(response.get("error") or "试用服务没有返回授权文件"))
    return document


def apply_document(document: dict, public_key: bytes | None = None) -> tool_license.Entitlement:
    """Verify a trial entitlement and install it as the active tool license."""
    key = public_key if public_key is not None else tool_license.active_public_key()
    if key is None:
        if str(os.environ.get(tool_license.ENV_DEV, "")).strip().lower() not in {"1", "true", "yes"}:
            raise TrialError("构建器未内置根公钥，无法校验试用授权")
        payload = document.get("payload") if isinstance(document, dict) else None
        if not isinstance(payload, dict):
            raise TrialError("试用授权文件无效")
    else:
        payload = tool_license.verify_document(
            document, key, tool_license.TOOL_LICENSE_FORMAT,
        )
    entitlement = tool_license.parse_entitlement(payload)
    machine = _machine()
    if entitlement.bind_mode != tool_license.BIND_FIXED:
        raise TrialError("试用授权没有绑定本机")
    if not entitlement.machine_allowed(machine):
        raise TrialError("试用授权绑定了其他机器")
    if entitlement.activation_required:
        raise TrialError("试用授权仍处于待激活状态")
    data = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    secure_store.write_private(tool_license.tool_license_path(), data)
    return entitlement


def claim_and_apply(server: str, public_key: bytes | None = None) -> tool_license.Entitlement:
    return apply_document(claim(server), public_key=public_key)


def wait_for_grant(
    server: str,
    timeout: float = 180.0,
    interval: float = 3.0,
    public_key: bytes | None = None,
) -> tool_license.Entitlement:
    """Poll the site until the trial is granted, then install it."""
    import time

    deadline = time.monotonic() + max(5.0, timeout)
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            status = fetch_status(server)
        except TrialError as exc:
            last_error = str(exc)
        else:
            state = str(status.get("status") or "")
            if state == "granted":
                return apply_document(claim(server), public_key=public_key)
            if state == "rejected":
                raise TrialError(str(status.get("reason") or "试用申请未通过"))
        time.sleep(interval)
    raise TrialError(last_error or "等待授权超时，请在网页完成申请后重试")


# ---------------------------------------------------------------------------
# status for the UI
# ---------------------------------------------------------------------------


def _days_left(expires: date | None, today: date | None = None) -> int | None:
    if expires is None:
        return None
    return max(0, (expires - (today or date.today())).days)


def status(server: str | None = None) -> dict:
    """Describe the local serial plus the current trial/entitlement state."""
    state = install_state()
    site = (server or "").strip().rstrip("/") or default_server()
    result = {
        "serial": state["serial"],
        "machine": state["machine"],
        "created": state.get("created"),
        "server": site,
        "edition": None,
        "mode": "unlicensed",
        "trial": False,
        "active": False,
        "expires": None,
        "days_left": None,
        "builds_left": None,
        "build_limit": None,
        "license_id": None,
    }
    try:
        license_status = tool_license.check()
    except tool_license.ToolLicenseError:
        return result
    result["mode"] = license_status.mode
    entitlement = license_status.entitlement
    if entitlement is not None:
        result.update({
            "edition": entitlement.edition,
            "trial": entitlement.edition == tool_license.EDITION_TRIAL,
            "active": license_status.valid,
            "expires": entitlement.expires.isoformat() if entitlement.expires else None,
            "days_left": _days_left(entitlement.expires),
            "build_limit": entitlement.max_builds_per_month or None,
            "license_id": entitlement.license_id,
        })
        quota = license_status.quota or {}
        if entitlement.max_builds_per_month:
            used = int(quota.get("used", 0))
            if not quota and getattr(license_status, "code", "") == "quota_exhausted":
                used = entitlement.max_builds_per_month
            result["builds_left"] = max(0, entitlement.max_builds_per_month - used)
    elif license_status.mode == "trial":
        trial = license_status.trial or {}
        result.update({
            "edition": tool_license.EDITION_TRIAL,
            "trial": True,
            "active": True,
            "days_left": int(trial.get("days_left") or 0),
            "builds_left": int(trial.get("builds_left") or 0),
        })
    return result
