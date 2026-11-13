"""Online and offline activation client for PyShield tool licenses."""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

from . import __version__, fingerprint, secure_store, tool_license

ACTIVATION_REQUEST_FORMAT = "pyshield-activation-request/1"
HEARTBEAT_REQUEST_FORMAT = "pyshield-heartbeat-request/1"
HEARTBEAT_RESPONSE_FORMAT = "pyshield-heartbeat/1"
HEARTBEAT_FILE = "heartbeat.json"
ENV_SERVER = "PYSHELD_ACTIVATION_SERVER"
ENV_ALLOW_HTTP = "PYSHELD_ALLOW_HTTP"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ActivationError(Exception):
    """Raised when activation, heartbeat or offline exchange fails."""


def default_server() -> str:
    return os.environ.get(ENV_SERVER, "").strip().rstrip("/")


def _validate_server(server: str) -> str:
    value = (server or "").strip().rstrip("/")
    if not value:
        raise ActivationError("activation server is not configured; use --server or PYSHELD_ACTIVATION_SERVER")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ActivationError(f"invalid activation server URL: {value}")
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    allow_http = str(os.environ.get(ENV_ALLOW_HTTP, "")).lower() in {"1", "true", "yes"}
    if parsed.scheme != "https" and not local and not allow_http:
        raise ActivationError("activation server must use HTTPS (set PYSHELD_ALLOW_HTTP=1 only for testing)")
    return value


def _request(method: str, server: str, path: str, payload: dict | None, timeout: float) -> dict:
    base = _validate_server(server)
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": f"PyShield/{__version__}",
    }
    if payload is not None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise ActivationError(f"activation server returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ActivationError(f"cannot reach activation server: {exc}") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ActivationError("activation server response is too large")
    try:
        document = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ActivationError("activation server returned invalid JSON") from exc
    if not isinstance(document, dict):
        raise ActivationError("activation server returned an invalid document")
    return document


def _machine(machine: str | None) -> str:
    return (machine or fingerprint.machine_fingerprint()).lower()


def _read_document(path: str | Path) -> dict:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ActivationError(f"license file not found: {path}") from exc
    except (OSError, ValueError) as exc:
        raise ActivationError(f"cannot read license file {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ActivationError("license file must contain a JSON object")
    return document


def _verify_entitlement(
    document: dict,
    public_key: bytes | None = None,
    require_machine: str | None = None,
) -> tool_license.Entitlement:
    key = public_key if public_key is not None else tool_license.active_public_key()
    if key is None:
        raise ActivationError("tool public key is not configured")
    payload = tool_license.verify_document(document, key, tool_license.TOOL_LICENSE_FORMAT)
    entitlement = tool_license.parse_entitlement(payload)
    if require_machine:
        if entitlement.bind_mode != tool_license.BIND_FIXED:
            raise ActivationError("activation response is not bound to a machine")
        if not entitlement.machine_allowed(require_machine):
            raise ActivationError("activation response is bound to a different machine")
        if entitlement.activation_required:
            raise ActivationError("activation response still requires activation")
    return replace(entitlement, document=document)


def activation_request(
    license_document: dict,
    machine: str | None = None,
) -> dict:
    return {
        "format": ACTIVATION_REQUEST_FORMAT,
        "license": license_document,
        "machine": _machine(machine),
        "product": "pyshield",
        "version": __version__,
        "nonce": secrets.token_hex(16),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def activate(
    license_path: str | Path,
    server: str | None = None,
    machine: str | None = None,
    timeout: float = 20.0,
    public_key: bytes | None = None,
) -> tool_license.Entitlement:
    target_machine = _machine(machine)
    document = _read_document(license_path)
    request = activation_request(document, target_machine)
    response = _request("POST", server or default_server(), "/v1/activate", request, timeout)
    entitlement = _verify_entitlement(response, public_key, require_machine=target_machine)
    _install_document(response)
    return entitlement


def _install_document(document: dict) -> Path:
    data = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    target = tool_license.tool_license_path()
    secure_store.write_private(target, data)
    return target


def install_license(
    license_path: str | Path,
    machine: str | None = None,
    public_key: bytes | None = None,
) -> tool_license.Entitlement:
    document = _read_document(license_path)
    target_machine = _machine(machine)
    entitlement = _verify_entitlement(document, public_key)
    if entitlement.bind_mode == tool_license.BIND_FIXED and not entitlement.machine_allowed(target_machine):
        raise ActivationError("license is bound to a different machine")
    if entitlement.activation_required and entitlement.bind_mode != tool_license.BIND_FIXED:
        raise ActivationError("license requires activation; use license activate or offline exchange")
    _install_document(document)
    return entitlement


def deactivate() -> bool:
    return tool_license.remove_entitlement()


def create_offline_request(
    license_path: str | Path,
    out_path: str | Path,
    machine: str | None = None,
) -> Path:
    document = _read_document(license_path)
    request = activation_request(document, machine)
    target = Path(out_path)
    secure_store.write_private(
        target,
        json.dumps(request, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n",
    )
    return target


def apply_offline_response(
    response_path: str | Path,
    machine: str | None = None,
    public_key: bytes | None = None,
) -> tool_license.Entitlement:
    document = _read_document(response_path)
    target_machine = _machine(machine)
    entitlement = _verify_entitlement(document, public_key, require_machine=target_machine)
    _install_document(document)
    return entitlement


def heartbeat_request(entitlement: tool_license.Entitlement, machine: str, used_builds: int = 0) -> dict:
    if not entitlement.document:
        raise ActivationError("license document is not loaded; reinstall the tool license")
    return {
        "format": HEARTBEAT_REQUEST_FORMAT,
        "license_id": entitlement.license_id,
        "license": entitlement.document,
        "machine": machine,
        "used_builds": int(used_builds),
        "nonce": secrets.token_hex(16),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def heartbeat_path() -> Path:
    return tool_license.state_dir() / HEARTBEAT_FILE


def read_heartbeat(public_key: bytes | None = None) -> dict | None:
    path = heartbeat_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ActivationError(f"cannot read heartbeat state: {exc}") from exc
    key = public_key if public_key is not None else tool_license.active_public_key()
    if key is None:
        return None
    return tool_license.verify_document(
        document, key, HEARTBEAT_RESPONSE_FORMAT, tool_license.HEARTBEAT_SIGNATURE_PREFIX,
    )


def heartbeat(
    entitlement: tool_license.Entitlement,
    server: str | None = None,
    machine: str | None = None,
    used_builds: int = 0,
    timeout: float = 15.0,
    public_key: bytes | None = None,
) -> dict:
    target_machine = _machine(machine)
    request = heartbeat_request(entitlement, target_machine, used_builds)
    response = _request("POST", server or default_server(), "/v1/heartbeat", request, timeout)
    key = public_key if public_key is not None else tool_license.active_public_key()
    if key is None:
        raise ActivationError("tool public key is not configured")
    payload = tool_license.verify_document(
        response, key, HEARTBEAT_RESPONSE_FORMAT, tool_license.HEARTBEAT_SIGNATURE_PREFIX,
    )
    if str(payload.get("license_id") or "") != entitlement.license_id:
        raise ActivationError("heartbeat response is for a different license")
    if str(payload.get("status") or "") != "active":
        raise ActivationError(str(payload.get("message") or "license is no longer active"))
    secure_store.write_private(
        heartbeat_path(),
        json.dumps(response, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n",
    )
    return payload


def fetch_revocations(
    server: str | None = None,
    timeout: float = 15.0,
    public_key: bytes | None = None,
) -> int:
    response = _request("GET", server or default_server(), "/v1/revocations", None, timeout)
    return tool_license.install_revocations(response, public_key=public_key)
