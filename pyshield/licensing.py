"""License creation and inspection for PyShield builds.

A license is a PSX1 envelope sealed with a key derived from the build's master
key. It carries the build key (needed to decrypt modules), an optional expiry
date and an optional list of licensed machine fingerprints. The payload is
signed with the build's Ed25519 signing key, so a license cannot be forged
even when the runtime's symmetric master key is extracted.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import crypto, ed25519

LICENSE_FILENAME = "pyshield.lic"
SECRET_SUFFIX = ".shield-secret.json"
FORMAT = 2
MAX_GRACE_DAYS = 3650

BIND_ANY = "any"
BIND_FIRST_RUN = "first-run"
BIND_FIXED = "fixed"
BIND_MODES = (BIND_ANY, BIND_FIRST_RUN, BIND_FIXED)
BINDING_FILE_NAME = ".pyshield-binding.json"
BINDING_FILE_ENV = "PYSHELD_BINDING_FILE"

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


def parse_grace_days(value: int | str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise LicenseError(f"invalid grace days {value!r}") from exc
    if days < 0 or days > MAX_GRACE_DAYS:
        raise LicenseError(f"grace days must be between 0 and {MAX_GRACE_DAYS}")
    return days


def normalize_fingerprints(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text and text not in seen:
            seen.append(text)
    return seen


def normalize_bind_mode(value: str | None, fingerprints: list[str] | tuple[str, ...] = ()) -> str:
    """Resolve the binding mode, deriving it from fingerprints for old secrets."""
    text = str(value or "").strip().lower()
    if text in BIND_MODES:
        return text
    if not text:
        return BIND_FIXED if fingerprints else BIND_ANY
    raise LicenseError(f"unknown bind mode {value!r}; use one of: {', '.join(BIND_MODES)}")


def bind_mode_of(payload: dict) -> str:
    """Return the binding mode of a license payload, including legacy licenses."""
    fingerprints = payload.get("fingerprints") or []
    try:
        return normalize_bind_mode(payload.get("bind_mode"), fingerprints)
    except LicenseError:
        return BIND_ANY


def sign_payload(payload: dict, signing_key: bytes) -> dict:
    signed = dict(payload)
    signed.pop("signature", None)
    signature = ed25519.sign(signing_key, crypto.license_signing_message(signed))
    signed["signature"] = crypto.b85encode(signature)
    return signed


def verify_payload(payload: dict, public_key: bytes) -> bool:
    signature = payload.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    try:
        raw = crypto.b85decode(signature)
    except Exception:
        return False
    return ed25519.verify(public_key, crypto.license_signing_message(payload), raw)


def secret_public_key(secret: dict) -> bytes:
    return ed25519.public_key(crypto.b85decode(secret["signing_key"]))


def build_payload(
    build_id: str,
    build_key: bytes,
    cipher: str,
    python_version: str,
    expires: date | None,
    fingerprints: list[str],
    customer: str | None,
    not_before: date | None = None,
    grace_days: int | str | None = 0,
    bind_mode: str = "",
) -> dict:
    mode = normalize_bind_mode(bind_mode, fingerprints)
    if mode != BIND_FIXED:
        fingerprints = []
    return {
        "format": FORMAT,
        "build_id": build_id,
        "build_key": crypto.b85encode(build_key),
        "cipher": cipher,
        "python": python_version,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "not_before": (not_before or date.today()).isoformat(),
        "expires": expires.isoformat() if expires else None,
        "grace_days": parse_grace_days(grace_days),
        "bind_mode": mode,
        "fingerprints": list(fingerprints),
        "customer": customer or None,
    }


def create_license(
    master_key: bytes,
    build_id: str,
    build_key: bytes,
    cipher: str,
    python_version: str,
    signing_key: bytes,
    expires: date | None = None,
    fingerprints: list[str] | tuple[str, ...] = (),
    customer: str | None = None,
    not_before: date | None = None,
    grace_days: int | str | None = 0,
    bind_mode: str = "",
) -> bytes:
    normalized = normalize_fingerprints(fingerprints)
    mode = normalize_bind_mode(bind_mode, normalized)
    if mode == BIND_FIXED and not normalized:
        raise LicenseError("fixed device binding requires at least one fingerprint")
    payload = sign_payload(build_payload(
        build_id, build_key, cipher, python_version, expires,
        normalized, customer, not_before, grace_days, mode,
    ), signing_key)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        return crypto.seal(license_key(master_key, build_id), data, license_aad(build_id), cipher=cipher)
    except ValueError as exc:
        raise LicenseError(str(exc)) from exc


def open_license(master_key: bytes, build_id: str, sealed: bytes, public_key: bytes) -> dict:
    try:
        data = crypto.open_envelope(license_key(master_key, build_id), sealed, license_aad(build_id))
    except crypto.CryptoError as exc:
        raise LicenseError("license cannot be decrypted with this build secret") from exc
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LicenseError("license payload is not valid JSON") from exc
    if payload.get("format") != FORMAT:
        raise LicenseError("unsupported license format; rebuild with a current PyShield")
    if not verify_payload(payload, public_key):
        raise LicenseError("license signature is invalid")
    if payload.get("build_id") != build_id:
        raise LicenseError("license does not belong to this build")
    try:
        crypto.b85decode(payload["build_key"])
    except Exception as exc:
        raise LicenseError("license is missing a valid build key") from exc
    return payload


def validate_payload(
    payload: dict,
    today: date | None = None,
    machine_fingerprint: str | None = None,
) -> list[str]:
    """Return human-readable problems without raising; used by vendor verification."""
    today = today or date.today()
    problems: list[str] = []

    not_before = payload.get("not_before")
    if not_before:
        try:
            issued = date.fromisoformat(str(not_before))
        except ValueError:
            problems.append("license has an invalid issue date")
        else:
            if today + timedelta(days=1) < issued:
                problems.append(f"system clock is earlier than the license issue date {issued}")

    try:
        grace = parse_grace_days(payload.get("grace_days"))
    except LicenseError as exc:
        problems.append(str(exc))
        grace = 0

    expires = payload.get("expires")
    if expires:
        try:
            expires_date = date.fromisoformat(str(expires))
        except ValueError:
            problems.append("license has an invalid expiry date")
        else:
            if today > expires_date + timedelta(days=grace):
                if grace:
                    problems.append(f"license expired on {expires} (grace period ended)")
                else:
                    problems.append(f"license expired on {expires}")

    mode = bind_mode_of(payload)
    fingerprints = [str(item).lower() for item in payload.get("fingerprints") or []]
    if mode == BIND_FIXED and not fingerprints:
        problems.append("license uses fixed binding but lists no fingerprints")
    if machine_fingerprint and mode == BIND_FIXED and fingerprints:
        if machine_fingerprint.lower() not in fingerprints:
            problems.append(f"machine {machine_fingerprint} is not licensed")
    return problems


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
        raise LicenseError("unsupported build secret format; rebuild with a current PyShield")
    for field in ("build_id", "master_key", "build_key", "signing_key"):
        if not data.get(field):
            raise LicenseError(f"build secret is missing {field!r}")
    try:
        master = crypto.b85decode(data["master_key"])
        build = crypto.b85decode(data["build_key"])
        signing = crypto.b85decode(data["signing_key"])
    except Exception as exc:
        raise LicenseError("build secret contains invalid keys") from exc
    if len(master) != 32 or len(build) != 32 or len(signing) != 32:
        raise LicenseError("build secret keys have an invalid length")
    public = ed25519.public_key(signing)
    stored_public = data.get("signing_public_key")
    if stored_public:
        try:
            matches = crypto.b85decode(stored_public) == public
        except Exception as exc:
            raise LicenseError("build secret contains an invalid signing public key") from exc
        if not matches:
            raise LicenseError("build secret signing keys do not match")
    data["signing_public_key"] = crypto.b85encode(public)
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
        f"not before:  {payload.get('not_before') or '?'}",
        f"expires:     {payload.get('expires') or 'never'}",
        f"grace days:  {payload.get('grace_days', 0)}",
        f"customer:    {payload.get('customer') or '-'}",
    ]
    mode = bind_mode_of(payload)
    fingerprints = payload.get("fingerprints") or []
    if mode == BIND_FIRST_RUN:
        lines.append("binding:     first-run (binds to the first machine that starts it)")
    elif mode == BIND_FIXED:
        lines.append("binding:     fixed")
        lines.extend(f"  - {item}" for item in fingerprints)
    else:
        lines.append("binding:     any machine")
    return "\n".join(lines)
