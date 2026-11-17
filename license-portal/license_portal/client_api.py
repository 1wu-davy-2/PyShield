"""Encrypted transport for the desktop cloud-guard endpoints.

Endpoints (POST, JSON):

* ``/api/license/preflight`` — may this install start a build?
* ``/api/license/consume``   — charge one build unit (idempotent).
* ``/api/license/verify``    — periodic 1-5 minute binding check.

Wire format::

    request  {"serial": "PS-...", "data": "<base85 PSX1 envelope>"}
    response {"ok": true, "data": "<base85 PSX1 envelope>"}

The envelope is :func:`pyshield.crypto.seal` with the per-license guard key
(:func:`license_portal.security.derive_guard_key`). Associated data binds the
ciphertext to the endpoint and serial; responses use a ``:response`` suffix.
The inner plaintext carries identity fields plus ``nonce`` and ``client_time``
for replay protection. Errors are plain JSON ``{"ok": false, "code", "error"}``
so the client can fail closed without key material.
"""

from __future__ import annotations

import json

from pyshield import crypto

from . import security, validation
from .config import Config
from .service import PortalService, ServiceError

ENDPOINTS = {
    "/api/license/preflight": "preflight",
    "/api/license/consume": "consume",
    "/api/license/verify": "verify",
}

MAX_INNER_BYTES = 16 * 1024


def request_aad(name: str, serial: str) -> bytes:
    return f"pyshield-cloud:{name}:{serial}".encode("utf-8")


def response_aad(name: str, serial: str) -> bytes:
    return request_aad(name, serial) + b":response"


def handle(
    service: PortalService,
    config: Config,
    path: str,
    payload: dict,
    remote_ip: str,
) -> tuple[int, dict]:
    name = ENDPOINTS[path]
    serial = validation.normalize_serial(str(payload.get("serial") or ""))
    encoded = str(payload.get("data") or "")
    if not encoded:
        service.log_guard_failure(serial, name, "missing_envelope", remote_ip)
        raise ServiceError("缺少加密请求体", 400, "missing_envelope")

    material = service.guard_key_material(serial)
    if material is None:
        service.log_guard_failure(serial, name, "license_not_found", remote_ip)
        raise ServiceError("没有找到该序列号的授权记录", 404, "license_not_found")

    key = security.derive_guard_key(
        str(material["serial"]), str(material["machine"]), str(material["display_code"]),
    )
    try:
        envelope = crypto.b85decode(encoded)
        plaintext = crypto.open_envelope(key, envelope, request_aad(name, serial))
    except (crypto.CryptoError, ValueError):
        service.log_guard_failure(serial, name, "bad_envelope", remote_ip)
        raise ServiceError("请求体解密失败", 400, "bad_envelope") from None
    if len(plaintext) > MAX_INNER_BYTES:
        service.log_guard_failure(serial, name, "payload_too_large", remote_ip)
        raise ServiceError("请求体过大", 413, "payload_too_large")
    try:
        inner = json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        service.log_guard_failure(serial, name, "bad_payload", remote_ip)
        raise ServiceError("请求体不是合法 JSON", 400, "bad_payload") from None
    if not isinstance(inner, dict):
        service.log_guard_failure(serial, name, "bad_payload", remote_ip)
        raise ServiceError("请求体必须是 JSON 对象", 400, "bad_payload")

    method = getattr(service, f"client_{name}")
    result = method(serial, inner, remote_ip)

    sealed = crypto.seal(
        key,
        json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        response_aad(name, serial),
        cipher="chacha-pure",
    )
    return 200, {"ok": True, "data": crypto.b85encode(sealed)}