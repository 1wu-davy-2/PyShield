"""Small local state files protected with Windows DPAPI or POSIX permissions.

The HMAC layer is a tamper deterrent for trial and quota counters. It is not a
secret vault: a determined attacker can patch the process. It exists so casual
editing of the JSON files fails closed instead of silently granting builds.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator

_DPAPI_PREFIX = b"DPAPI1\n"
_PLAIN_PREFIX = b"PLAIN1\n"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_RETRY_ATTEMPTS = 20
_RETRY_BASE_DELAY = 0.01
_RETRY_MAX_DELAY = 0.15


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


def _is_transient_oserror(exc: OSError) -> bool:
    """Return True for sharing violations that can clear on retry."""
    if getattr(exc, "winerror", None) in {5, 32, 33}:
        return True
    return exc.errno in {errno.EACCES, errno.EPERM, errno.EBUSY, errno.EAGAIN}


def _retry_oserror(action):
    last: OSError | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return action()
        except OSError as exc:
            if not _is_transient_oserror(exc) or attempt + 1 >= _RETRY_ATTEMPTS:
                raise
            last = exc
            time.sleep(min(_RETRY_BASE_DELAY * (attempt + 1), _RETRY_MAX_DELAY))
    assert last is not None
    raise last


@contextlib.contextmanager
def exclusive_lock(path: Path | str, timeout: float = 10.0) -> Iterator[None]:
    """Hold an inter-process exclusive lock on ``path``.

    The lock file is intentionally separate from the state file: atomic writes
    replace the state file's inode, so locking it directly would not serialize
    two read-modify-write cycles.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = open(target, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        deadline = time.monotonic() + max(0.0, float(timeout))
        delay = 0.01
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SecureStoreError(f"timed out waiting for lock: {target}")
                time.sleep(delay)
                delay = min(delay * 2, 0.2)
        try:
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        handle.close()


def write_private(path: Path | str, data: bytes) -> None:
    """Atomically write a state file.

    A unique temp file avoids two processes sharing a deterministic ``.tmp``
    path. On Windows, ``os.replace`` can still hit a sharing violation while
    another process is reading the target, so the replace is retried briefly.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent),
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        _retry_oserror(lambda: os.replace(temp, target))
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def read_private(path: Path | str) -> bytes | None:
    try:
        return _retry_oserror(lambda: Path(path).read_bytes())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SecureStoreError(f"cannot read {path}: {exc}") from exc


def remove_private(path: Path | str) -> bool:
    try:
        _retry_oserror(lambda: Path(path).unlink())
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SecureStoreError(f"cannot remove {path}: {exc}") from exc


def platform_name() -> str:
    if os.name == "nt":
        return "windows-dpapi"
    return "posix-0600" if sys.platform != "win32" else "windows"
