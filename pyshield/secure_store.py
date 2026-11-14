"""Small local state files protected with Windows DPAPI or POSIX permissions.

The HMAC layer is a tamper deterrent for trial and quota counters. It is not a
secret vault: a determined attacker can patch the process. It exists so casual
editing of the JSON files fails closed instead of silently granting builds.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

_DPAPI_PREFIX = b"DPAPI1\n"
_PLAIN_PREFIX = b"PLAIN1\n"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecureStoreError(Exception):
    """Raised when protected local state cannot be read or verified."""


if os.name == "nt":
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    value = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return value, buffer


def _dpapi(data: bytes, protect: bool, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI is only available on Windows")
    in_blob, in_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(entropy)
    out_blob = _DataBlob()
    if protect:
        ok = _crypt32.CryptProtectData(
            ctypes.byref(in_blob), "PyShield", ctypes.byref(entropy_blob),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
        )
    else:
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, ctypes.byref(entropy_blob),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
        )
    # Keep the input buffers alive until the API call returns.
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def protect(data: bytes, entropy: bytes = b"pyshield-state-v1") -> bytes:
    """Protect bytes with DPAPI when available, otherwise mark them as plain."""
    if os.name == "nt":
        try:
            return _DPAPI_PREFIX + _dpapi(data, True, entropy)
        except OSError:
            pass
    return _PLAIN_PREFIX + data


def unprotect(data: bytes, entropy: bytes = b"pyshield-state-v1") -> bytes:
    """Reverse :func:`protect`."""
    if data.startswith(_DPAPI_PREFIX):
        if os.name != "nt":
            raise SecureStoreError("DPAPI-protected state cannot be read on this platform")
        try:
            return _dpapi(data[len(_DPAPI_PREFIX):], False, entropy)
        except OSError as exc:
            raise SecureStoreError(f"cannot decrypt local state: {exc}") from exc
    if data.startswith(_PLAIN_PREFIX):
        return data[len(_PLAIN_PREFIX):]
    return data


def state_key(machine: str, purpose: str, secret: bytes | None = None) -> bytes:
    material = f"pyshield-state-key-v1|{purpose}|{machine}".encode("utf-8")
    if secret is not None:
        return hmac.new(secret, material, hashlib.sha256).digest()
    return hashlib.sha256(material).digest()


def seal_state(
    payload: dict,
    machine: str,
    purpose: str,
    secret: bytes | None = None,
) -> bytes:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    tag = hmac.new(state_key(machine, purpose, secret), body, hashlib.sha256).hexdigest()
    document = json.dumps(
        {"payload": payload, "signature": tag},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return protect(document, entropy=f"pyshield-{purpose}".encode("utf-8"))


def open_state(
    data: bytes,
    machine: str,
    purpose: str,
    secret: bytes | None = None,
) -> dict:
    try:
        document = json.loads(unprotect(data, entropy=f"pyshield-{purpose}".encode("utf-8")).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise SecureStoreError("invalid local state") from exc
    if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
        raise SecureStoreError("invalid local state")
    body = json.dumps(
        document["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    expected = hmac.new(state_key(machine, purpose, secret), body, hashlib.sha256).hexdigest()
    actual = str(document.get("signature") or "")
    if not hmac.compare_digest(expected, actual):
        raise SecureStoreError("local state signature mismatch")
    return document["payload"]


def write_private(path: Path | str, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, target)


def read_private(path: Path | str) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SecureStoreError(f"cannot read {path}: {exc}") from exc


def remove_private(path: Path | str) -> bool:
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SecureStoreError(f"cannot remove {path}: {exc}") from exc


def platform_name() -> str:
    if os.name == "nt":
        return "windows-dpapi"
    return "posix-0600" if sys.platform != "win32" else "windows"
