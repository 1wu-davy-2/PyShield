"""Cloud entitlement guard: encrypted verify / preflight / consume calls.

The desktop client talks to the license portal through three encrypted
endpoints (``docs/cloud-license-api.md``):

* ``verify``    — randomised 1-5 minute heartbeat binding check.
* ``preflight`` — may this install start a build?
* ``consume``   — charge one build unit right before a build starts.

Request and response bodies are PSX1 envelopes sealed with a per-license
guard key derived from ``serial | machine | display_code`` (HKDF-SHA256,
salt ``pyshield-cloud-guard-v1``).  The display code is the trial code or
activation-key prefix the portal handed out at claim/activate time.

Policy: fail closed.  Any anomaly — network failure, decryption failure,
revoked/expired licence, machine mismatch, replay, quota exhaustion — marks
the guard as blocked and the build path refuses to run until a later
heartbeat succeeds again.
"""

from __future__ import annotations

import json
import os
import random
import secrets
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from . import crypto, fingerprint, tool_license, trial_license

ENV_ENABLE = "PYSHELD_CLOUD_API"
ENV_BASE = "PYSHELD_CLOUD_API_BASE"
DEFAULT_INTERVAL = (60, 300)  # seconds, randomised per round
GUARD_KEY_SALT = b"pyshield-cloud-guard-v1"
MAX_RESPONSE_BYTES = 1024 * 1024


class CloudError(RuntimeError):
    """Raised when the cloud guard cannot confirm this install is allowed."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


_lock = threading.Lock()
_guard = {
    "blocked": False,
    "reason": "",
    "code": "",
    "blocked_at": 0.0,
    "last_ok": 0.0,
}
_server_override = ""
_enable_override: bool | None = None
_watchdog: threading.Thread | None = None


def configure(server: str = "", enable: bool | None = None) -> None:
    """Set the portal address (GUI settings) and optionally force-enable."""
    global _server_override, _enable_override
    with _lock:
        _server_override = str(server or "").strip().rstrip("/")
        _enable_override = enable


def enabled() -> bool:
    with _lock:
        override = _enable_override
    if override is not None:
        return override
    return os.environ.get(ENV_ENABLE, "").strip().lower() in {"1", "true", "yes", "on"}


def base_url() -> str:
    value = os.environ.get(ENV_BASE, "").strip().rstrip("/")
    if value:
        return value
    with _lock:
        override = _server_override
    return override or trial_license.default_server()


def guard_state() -> dict:
    with _lock:
        state = dict(_guard)
    state["enabled"] = enabled()
    state["server"] = base_url()
    return state


def mark_blocked(reason: str, code: str = "cloud_error") -> None:
    with _lock:
        _guard.update({
            "blocked": True,
            "reason": str(reason),
            "code": str(code),
            "blocked_at": time.time(),
        })


def _mark_ok() -> None:
    with _lock:
        _guard.update({
            "blocked": False, "reason": "", "code": "",
            "blocked_at": 0.0, "last_ok": time.time(),
        })


def ensure_allowed() -> None:
    """Raise if a previous cloud check failed; the build path must stop."""
    if not enabled():
        return
    with _lock:
        blocked = _guard["blocked"]
        reason = _guard["reason"]
        code = _guard["code"]
    if blocked:
        raise CloudError(reason or "云端授权校验未通过", code)


# ---------------------------------------------------------------------------
# key material
# ---------------------------------------------------------------------------


def derive_key(serial: str, machine: str, display_code: str) -> bytes:
    ikm = f"{serial}|{machine}|{display_code}".encode("utf-8")
    return crypto.hkdf_sha256(ikm, salt=GUARD_KEY_SALT, info=b"client-api", length=32)


def _display_code(serial: str) -> str:
    state = trial_license.install_state()
    codes = state.get("codes") or {}
    code = str(codes.get(serial) or "")
    if code:
        return code
    # Bootstrap: the plaintext status endpoint echoes the display code once,
    # over the same TLS channel used for claim/activate.
    base = base_url()
    if not base:
        raise CloudError("未配置云端授权服务地址", "no_server")
    try:
        response = trial_license._request(
            "POST", base, "/api/license/status",
            {
                "serial": serial,
                "machine": state["machine"],
                "license_id": "",
                "revision": 0,
            },
            10.0,
        )
    except trial_license.TrialError as exc:
        raise CloudError(f"无法获取本机授权加密码：{exc}", "display_code_fetch") from exc
    code = str(response.get("trial_code") or "")
    if not code:
        raise CloudError("云端没有返回本机授权的加密码", "missing_display_code")
    trial_license.remember_code(serial, code)
    return code


def identity(entitlement) -> dict:
    if entitlement is None:
        raise CloudError("没有可校验的授权文件", "no_entitlement")
    raw = getattr(entitlement, "raw", None) or {}
    serial = str(raw.get("serial") or "")
    if not serial:
        raise CloudError("授权文件缺少序列号，无法进行云端校验", "no_serial")
    return {
        "serial": serial,
        "machine": fingerprint.machine_fingerprint().lower(),
        "license_id": entitlement.license_id,
        "edition": entitlement.edition,
        "revision": int(raw.get("revision") or 0),
    }


# ---------------------------------------------------------------------------
# encrypted transport
# ---------------------------------------------------------------------------


def _request(name: str, path: str, entitlement, extra: dict | None = None) -> dict:
    ident = identity(entitlement)
    serial = ident["serial"]
    key = derive_key(serial, ident["machine"], _display_code(serial))
    nonce = secrets.token_hex(16)
    inner = dict(ident)
    inner["nonce"] = nonce
    inner["client_time"] = int(time.time())
    if extra:
        inner.update(extra)
    aad = f"pyshield-cloud:{name}:{serial}".encode("utf-8")
    envelope = crypto.seal(
        key,
        json.dumps(inner, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        aad,
        cipher="chacha-pure",
    )
    base = base_url()
    if not base:
        raise CloudError("未配置云端授权服务地址", "no_server")
    body = json.dumps({"serial": serial, "data": crypto.b85encode(envelope)}).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "PyShield-cloud-guard/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        code, message = f"http_{exc.code}", detail
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict):
                code = str(parsed.get("code") or code)
                message = str(parsed.get("error") or message)
        except ValueError:
            pass
        raise CloudError(message or f"云端授权服务返回 HTTP {exc.code}", code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise CloudError(f"云端授权服务不可达：{exc}", "network") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CloudError("云端授权服务响应过大", "response_too_large")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CloudError("云端授权服务返回了非 JSON 响应", "bad_response") from exc
    if not isinstance(document, dict):
        raise CloudError("云端授权服务返回了无效响应", "bad_response")
    if not document.get("ok"):
        raise CloudError(
            str(document.get("error") or "云端授权校验未通过"),
            str(document.get("code") or "cloud_error"),
        )
    try:
        plaintext = crypto.open_envelope(
            key, crypto.b85decode(str(document.get("data") or "")), aad + b":response",
        )
        result = json.loads(plaintext.decode("utf-8"))
    except (crypto.CryptoError, ValueError, UnicodeDecodeError) as exc:
        raise CloudError("云端响应解密失败", "bad_response") from exc
    if not isinstance(result, dict):
        raise CloudError("云端响应内容无效", "bad_response")
    if str(result.get("nonce") or "") != nonce:
        raise CloudError("云端响应与请求不匹配", "nonce_mismatch")
    if str(result.get("serial") or "") != serial:
        raise CloudError("云端响应的序列号与本机不一致", "serial_mismatch")
    return result


def preflight(entitlement=None) -> dict:
    if not enabled():
        return {"ok": True, "skipped": True}
    return _request("preflight", "/api/license/preflight", entitlement)


def consume(entitlement=None, idempotency_key: str = "") -> dict:
    if not enabled():
        return {"ok": True, "skipped": True}
    key = str(idempotency_key or "").strip() or secrets.token_hex(16)
    return _request(
        "consume", "/api/license/consume", entitlement,
        extra={"idempotency_key": key},
    )


def verify(entitlement=None) -> dict:
    if not enabled():
        return {"ok": True, "skipped": True}
    return _request("verify", "/api/license/verify", entitlement)


def apply_update(result: dict) -> bool:
    """Install a fresher signed document offered by verify/preflight."""
    document = result.get("document")
    if not isinstance(document, dict):
        return False
    trial_license.apply_document(document)
    return True


# ---------------------------------------------------------------------------
# watchdog
# ---------------------------------------------------------------------------


def start_watchdog(
    entitlement_provider: Callable[[], object],
    on_blocked: Callable[[dict], None],
    on_error: Callable[[Exception], None] | None = None,
    interval: tuple[int, int] = DEFAULT_INTERVAL,
) -> threading.Thread | None:
    """Randomised 1-5 minute heartbeat; any failure blocks the build path."""
    global _watchdog
    if not enabled():
        return None
    with _lock:
        if _watchdog is not None and _watchdog.is_alive():
            return _watchdog

    def loop() -> None:
        delay = 2.0  # first check right after startup
        while True:
            time.sleep(delay)
            if not enabled():
                return
            delay = float(random.randint(*interval))
            try:
                entitlement = entitlement_provider()
                if entitlement is None:
                    # Unlicensed installs are already gated by the build path.
                    continue
                result = verify(entitlement)
                if result.get("skipped"):
                    return
                status = str(result.get("status") or "")
                if status not in {"active", "update_required"}:
                    raise CloudError(f"云端返回了未知授权状态：{status}", "bad_status")
                if status == "update_required":
                    apply_update(result)
                _mark_ok()
                suggested = int(result.get("next_check_after") or 0)
                if suggested > 0:
                    delay = float(min(max(suggested, interval[0]), interval[1]))
            except Exception as exc:  # noqa: BLE001 - fail closed, keep looping
                code = getattr(exc, "code", "") or "cloud_error"
                mark_blocked(str(exc), code)
                try:
                    on_blocked(guard_state())
                except Exception:  # noqa: BLE001
                    pass
                if on_error is not None:
                    try:
                        on_error(exc)
                    except Exception:  # noqa: BLE001
                        pass

    thread = threading.Thread(target=loop, name="pyshield-cloud-check", daemon=True)
    with _lock:
        _watchdog = thread
    thread.start()
    return thread