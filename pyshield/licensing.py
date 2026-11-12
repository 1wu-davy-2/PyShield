"""License creation and inspection for PyShield builds.

A license is a PSX1 envelope sealed with a key derived from the build's master
key. It carries the build key (needed to decrypt modules), an optional expiry
date and an optional list of licensed machine fingerprints.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import crypto

LICENSE_FILENAME = "pyshield.lic"
SECRET_SUFFIX = ".shield-secret.json"
FORMAT = 1

LICENSE_SALT = b"pyshield-license-v1"
LICENSE_AAD_PREFIX = b"pyshield-license:"


class LicenseError(Exception):
    """Raised when a license or build secret cannot be used."""


def license_key(master_key: bytes, build_id: str) -> bytes:
    return crypto.hkdf_sha256(master_key, salt=LICENSE_SALT, info=build_id.encode("utf-8"), length=32)


def license_aad(build_id: str) -> bytes:
    return LICENSE_AAD_PREFIX + build_id.encode("utf-8")


def parse_expire(value: str | None, today: date | None = None) -> date | None:
    if value is None or value == "":
        return None
    today = today or date.today()
    text = str(value).strip()
    if text.isdigit():
        return today + timedelta(days=int(text))
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise LicenseError(f"invalid expire value {value!r}; use days or YYYY-MM-DD") from exc


def normalize_fingerprints(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text and text not in seen:
            seen.append(text)
    return seen


def build_payload(
    build_id: str,
    build_key: bytes,
    cipher: str,
    python_version: str,
    expires: date | None,
    fingerprints: list[str],
    customer: str | None,
) -> dict:
    return {
        "format": FORMAT,
        "build_id": build_id,
        "build_key": crypto.b85encode(build_key),
        "cipher": cipher,
        "python": python_version,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "expires": expires.isoformat() if expires else None,
        "fingerprints": list(fingerprints),
        "customer": customer or None,
    }


def create_license(
    master_key: bytes,
    build_id: str,
    build_key: bytes,
    cipher: str,
    python_version: str,
    expires: date | None = None,
    fingerprints: list[str] | tuple[str, ...] = (),
    customer: str | None = None,
) -> bytes:
    payload = build_payload(
        build_id, build_key, cipher, python_version, expires,
        normalize_fingerprints(fingerprints), customer,
    )
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        return crypto.seal(license_key(master_key, build_id), data, license_aad(build_id), cipher=cipher)
    except ValueError as exc:
        raise LicenseError(str(exc)) from exc


def open_license(master_key: bytes, build_id: str, sealed: bytes) -> dict:
    try:
        data = crypto.open_envelope(license_key(master_key, build_id), sealed, license_aad(build_id))
    except crypto.CryptoError as exc:
        raise LicenseError("license cannot be decrypted with this build secret") from exc
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LicenseError("license payload is not valid JSON") from exc
    if payload.get("format") != FORMAT:
        raise LicenseError("unsupported license format")
    if payload.get("build_id") != build_id:
        raise LicenseError("license does not belong to this build")
    try:
        crypto.b85decode(payload["build_key"])
    except Exception as exc:
        raise LicenseError("license is missing a valid build key") from exc
    return payload


def write_license(path: Path, sealed: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(sealed)
    os.replace(temporary, path)


def read_secret(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LicenseError(f"cannot read build secret {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LicenseError(f"build secret {path} is not valid JSON") from exc
    if data.get("format") != FORMAT:
        raise LicenseError("unsupported build secret format")
    for field in ("build_id", "master_key", "build_key"):
        if not data.get(field):
            raise LicenseError(f"build secret is missing {field!r}")
    try:
        master = crypto.b85decode(data["master_key"])
        build = crypto.b85decode(data["build_key"])
    except Exception as exc:
        raise LicenseError("build secret contains invalid keys") from exc
    if len(master) != 32 or len(build) != 32:
        raise LicenseError("build secret keys have an invalid length")
    return data


def write_secret(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def secret_path_for(output: Path) -> Path:
    return output.parent / (output.name + SECRET_SUFFIX)


def describe(payload: dict) -> str:
    lines = [
        f"build id:    {payload['build_id']}",
        f"cipher:      {payload.get('cipher', '?')}",
        f"python:      {payload.get('python', '?')}",
        f"created:     {payload.get('created', '?')}",
        f"expires:     {payload.get('expires') or 'never'}",
        f"customer:    {payload.get('customer') or '-'}",
    ]
    fingerprints = payload.get("fingerprints") or []
    lines.append("fingerprints:" if fingerprints else "fingerprints: any")
    lines.extend(f"  - {item}" for item in fingerprints)
    return "\n".join(lines)
