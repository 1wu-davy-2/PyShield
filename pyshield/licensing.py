"""License creation and inspection for PyShield builds.

Format 3 licenses are PSX1 envelopes sealed with a key derived from the
build's master key. They carry only authorization data: expiry, binding mode
and customer metadata. The module-encryption key is derived from the master
key and build id instead of being copied into the license, so a stolen or
leaked license no longer contains the key needed to decrypt module payloads.

Older format 2 licenses are still readable for renewal of existing builds, but
new builds always use format 3. The payload is signed with the build's Ed25519
signing key, so a license cannot be forged even when the runtime's symmetric
master key is extracted.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import crypto, ed25519

LICENSE_FILENAME = "pyshield.lic"
SECRET_SUFFIX = ".shield-secret.json"
FORMAT = 3
LEGACY_FORMAT = 2
MAX_GRACE_DAYS = 3650

BIND_ANY = "any"
BIND_FIRST_RUN = "first-run"
BIND_FIXED = "fixed"
BIND_MODES = (BIND_ANY, BIND_FIRST_RUN, BIND_FIXED)
BINDING_FILE_NAME = ".pyshield-binding.json"
BINDING_FILE_ENV = "PYSHELD_BINDING_FILE"

LICENSE_SALT = b"pyshield-license-v3"
LICENSE_AAD_PREFIX = b"pyshield-license-v3:"
LEGACY_LICENSE_SALT = b"pyshield-license-v1"
LEGACY_LICENSE_AAD_PREFIX = b"pyshield-license:"
BUILD_KEY_SALT = b"pyshield-build-key-v1"


class LicenseError(Exception):
    """Raised when a license or build secret cannot be used."""


def build_key_for(master_key: bytes, build_id: str) -> bytes:
    """Derive the module key deterministically from the build master key.

    Keeping this value out of the license means a customer who copies
    ``pyshield.lic`` does not copy the decryption key with it. The protected
    runtime can derive it locally because it already holds the master key.
    """
    return crypto.hkdf_sha256(
        master_key, salt=BUILD_KEY_SALT, info=build_id.encode("utf-8"), length=32
    )


def license_key(
    master_key: bytes, build_id: str, license_format: int = FORMAT
) -> bytes:
    if license_format == LEGACY_FORMAT:
        salt = LEGACY_LICENSE_SALT
    elif license_format == FORMAT:
        salt = LICENSE_SALT
    else:
        raise LicenseError(f"unsupported license format {license_format!r}")
    return crypto.hkdf_sha256(master_key, salt=salt, info=build_id.encode("utf-8"), length=32)


def license_aad(build_id: str, license_format: int = FORMAT) -> bytes:
    prefix = LEGACY_LICENSE_AAD_PREFIX if license_format == LEGACY_FORMAT else LICENSE_AAD_PREFIX
    if license_format not in (FORMAT, LEGACY_FORMAT):
        raise LicenseError(f"unsupported license format {license_format!r}")
    return prefix + build_id.encode("utf-8")


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


def _signature_prefix(license_format: int) -> bytes:
    if license_format == FORMAT:
        return crypto.LICENSE_SIGNATURE_PREFIX
    if license_format == LEGACY_FORMAT:
        return crypto.LEGACY_LICENSE_SIGNATURE_PREFIX
    raise LicenseError(f"unsupported license format {license_format!r}")


def sign_payload(
    payload: dict, signing_key: bytes, license_format: int = FORMAT
) -> dict:
    signed = dict(payload)
    signed.pop("signature", None)
    signature = ed25519.sign(
        signing_key, crypto.license_signing_message(signed, _signature_prefix(license_format))
    )
    signed["signature"] = crypto.b85encode(signature)
    return signed


def verify_payload(
    payload: dict, public_key: bytes, license_format: int = FORMAT
) -> bool:
    signature = payload.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    try:
        raw = crypto.b85decode(signature)
    except Exception:
        return False
    return ed25519.verify(
        public_key, crypto.license_signing_message(payload, _signature_prefix(license_format)), raw
    )


def secret_public_key(secret: dict) -> bytes:
    return ed25519.public_key(crypto.b85decode(secret["signing_key"]))


def build_payload(
    build_id: str,
    cipher: str,
    python_version: str,
    expires: date | None,
    fingerprints: list[str],
    customer: str | None,
    not_before: date | None = None,
    grace_days: int | str | None = 0,
    bind_mode: str = "",
    license_format: int = FORMAT,
    build_key: bytes | None = None,
) -> dict:
    if license_format not in (FORMAT, LEGACY_FORMAT):
        raise LicenseError(f"unsupported license format {license_format!r}")
    mode = normalize_bind_mode(bind_mode, fingerprints)
    if mode != BIND_FIXED:
        fingerprints = []
    payload = {
        "format": license_format,
        "build_id": build_id,
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
    if license_format == LEGACY_FORMAT:
        if build_key is None:
            raise LicenseError("format 2 licenses require a build key")
        payload["build_key"] = crypto.b85encode(build_key)
    return payload


def create_license(
    master_key: bytes,
    build_id: str,
    cipher: str,
    python_version: str,
    signing_key: bytes,
    expires: date | None = None,
    fingerprints: list[str] | tuple[str, ...] = (),
    customer: str | None = None,
    not_before: date | None = None,
    grace_days: int | str | None = 0,
    bind_mode: str = "",
    license_format: int = FORMAT,
    build_key: bytes | None = None,
) -> bytes:
    normalized = normalize_fingerprints(fingerprints)
    mode = normalize_bind_mode(bind_mode, normalized)
    if mode == BIND_FIXED and not normalized:
        raise LicenseError("fixed device binding requires at least one fingerprint")
    payload = sign_payload(build_payload(
        build_id, cipher, python_version, expires,
        normalized, customer, not_before, grace_days, mode,
        license_format=license_format, build_key=build_key,
    ), signing_key, license_format=license_format)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        return crypto.seal(
            license_key(master_key, build_id, license_format),
            data,
            license_aad(build_id, license_format),
            cipher=cipher,
        )
    except ValueError as exc:
        raise LicenseError(str(exc)) from exc


def open_license(master_key: bytes, build_id: str, sealed: bytes, public_key: bytes) -> dict:
    payload: dict | None = None
    found_format = FORMAT
    for candidate in (FORMAT, LEGACY_FORMAT):
        try:
            data = crypto.open_envelope(
                license_key(master_key, build_id, candidate),
                sealed,
                license_aad(build_id, candidate),
            )
        except crypto.CryptoError:
            continue
        except ValueError as exc:
            raise LicenseError(str(exc)) from exc
        try:
            decoded = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LicenseError("license payload is not valid JSON") from exc
        if isinstance(decoded, dict):
            value = decoded.get("format")
            if value not in (FORMAT, LEGACY_FORMAT):
                raise LicenseError("unsupported license format; rebuild with a current PyShield")
            if value == candidate:
                payload = decoded
                found_format = candidate
                break
    if payload is None:
        raise LicenseError("license cannot be decrypted with this build secret")
    if not verify_payload(payload, public_key, found_format):
        raise LicenseError("license signature is invalid")
    if payload.get("build_id") != build_id:
        raise LicenseError("license does not belong to this build")
    if found_format == LEGACY_FORMAT:
        try:
            build_key = crypto.b85decode(payload["build_key"])
        except Exception as exc:
            raise LicenseError("license is missing a valid build key") from exc
        if len(build_key) != 32:
            raise LicenseError("license contains an invalid build key")
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
    secret_format = data.get("format")
    if secret_format not in (FORMAT, LEGACY_FORMAT):
        raise LicenseError("unsupported build secret format; rebuild with a current PyShield")
    for field in ("build_id", "master_key", "signing_key"):
        if not data.get(field):
            raise LicenseError(f"build secret is missing {field!r}")
    try:
        master = crypto.b85decode(data["master_key"])
        signing = crypto.b85decode(data["signing_key"])
    except Exception as exc:
        raise LicenseError("build secret contains invalid keys") from exc
    if len(master) != 32 or len(signing) != 32:
        raise LicenseError("build secret keys have an invalid length")

    if secret_format == LEGACY_FORMAT:
        if not data.get("build_key"):
            raise LicenseError("build secret is missing 'build_key'")
        try:
            build = crypto.b85decode(data["build_key"])
        except Exception as exc:
            raise LicenseError("build secret contains invalid keys") from exc
        if len(build) != 32:
            raise LicenseError("build secret keys have an invalid length")
    else:
        build = build_key_for(master, str(data["build_id"]))
        stored = data.get("build_key")
        if stored:
            try:
                stored_build = crypto.b85decode(stored)
            except Exception as exc:
                raise LicenseError("build secret contains invalid keys") from exc
            if stored_build != build:
                raise LicenseError("build secret build key does not match its master key")

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
    data["build_key"] = crypto.b85encode(build)
    data["license_format"] = secret_format
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
