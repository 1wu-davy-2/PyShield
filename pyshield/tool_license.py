"""Commercial licensing for the PyShield tool itself.

This is deliberately separate from :mod:`pyshield.licensing`, which issues
licenses for protected outputs. Tool licenses are signed by an offline root key
and gate the builder, premium features, trial usage and build quotas.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import crypto, ed25519, fingerprint, secure_store

TOOL_LICENSE_FORMAT = "pyshield-tool-license/1"
TRIAL_STATE_FORMAT = "pyshield-trial/2"
TRIAL_STATE_FORMAT_V1 = "pyshield-trial/1"
QUOTA_STATE_FORMAT = "pyshield-quota/2"
QUOTA_STATE_FORMAT_V1 = "pyshield-quota/1"
REVOCATION_FORMAT = "pyshield-revocations/1"

TOOL_SIGNATURE_PREFIX = b"pyshield-tool-license-v1:"
REVOCATION_SIGNATURE_PREFIX = b"pyshield-revocations-v1:"
HEARTBEAT_SIGNATURE_PREFIX = b"pyshield-heartbeat-v1:"

FEATURE_GUI = "gui"
FEATURE_NATIVE_RUNTIME = "native-runtime"
FEATURE_OFFLINE_ACTIVATION = "offline-activation"
FEATURE_LICENSE_ISSUANCE = "license-issuance"
FEATURE_CI = "ci"
FEATURE_OEM = "oem"
ALL_FEATURES = (
    FEATURE_GUI,
    FEATURE_NATIVE_RUNTIME,
    FEATURE_OFFLINE_ACTIVATION,
    FEATURE_LICENSE_ISSUANCE,
    FEATURE_CI,
    FEATURE_OEM,
)

EDITION_TRIAL = "trial"
EDITION_PRO = "pro"
EDITION_ENTERPRISE = "enterprise"
EDITION_OEM = "oem"
EDITIONS = (EDITION_TRIAL, EDITION_PRO, EDITION_ENTERPRISE, EDITION_OEM)

BIND_ANY = "any"
BIND_FIXED = "fixed"
BIND_MODES = (BIND_ANY, BIND_FIXED)

TOOL_LICENSE_FILE = "tool-license.json"
TRIAL_FILE = "trial.json"
QUOTA_FILE = "quota.json"
REVOCATIONS_FILE = "revocations.json"

ENV_HOME = "PYSHELD_HOME"
ENV_LICENSE = "PYSHELD_TOOL_LICENSE"
ENV_PUBLIC_KEY = "PYSHELD_TOOL_PUBLIC_KEY"
ENV_DEV = "PYSHELD_DEV"
ENV_TRIAL_DAYS = "PYSHELD_TRIAL_DAYS"
ENV_TRIAL_BUILDS = "PYSHELD_TRIAL_BUILDS"

DEFAULT_TRIAL_DAYS = 14
DEFAULT_TRIAL_BUILDS = 10

# The trial edition is deliberately narrower than Pro: it only ships the
# dependency-free cipher and cannot claim the full integrity manifest.
TRIAL_CIPHERS = ("chacha-pure",)
TRIAL_INTEGRITY = ("protected", "off")
MAX_GRACE_DAYS = 3650


class ToolLicenseError(Exception):
    """Raised when a tool license, trial or quota cannot be used."""


@dataclass(frozen=True)
class Entitlement:
    license_id: str
    customer: str
    edition: str
    features: tuple[str, ...]
    seats: int
    max_activations: int
    max_builds_per_month: int
    bind_mode: str
    fingerprints: tuple[str, ...]
    activation_required: bool
    issued: date | None
    not_before: date | None
    expires: date | None
    grace_days: int
    revocation_epoch: int
    raw: dict = field(default_factory=dict, repr=False, compare=False)
    document: dict = field(default_factory=dict, repr=False, compare=False)

    def allows(self, feature: str | None) -> bool:
        if feature is None:
            return True
        return "*" in self.features or feature in self.features

    def machine_allowed(self, machine: str) -> bool:
        if self.bind_mode == BIND_ANY:
            return True
        return machine.lower() in {item.lower() for item in self.fingerprints}

    def active_on(self, day: date) -> tuple[bool, str]:
        if self.not_before and day < self.not_before:
            return False, f"license is not valid before {self.not_before.isoformat()}"
        if self.expires:
            if day > self.expires + timedelta(days=self.grace_days):
                return False, f"license expired on {self.expires.isoformat()}"
            if day > self.expires:
                return True, f"license expired on {self.expires.isoformat()} (grace period)"
        return True, "valid"

    def watermark(self) -> dict:
        return {
            "license_id": self.license_id,
            "customer": self.customer or None,
            "edition": self.edition,
            "trial": self.edition == EDITION_TRIAL,
            "builds_used": 0,
        }


@dataclass
class ToolLicenseStatus:
    mode: str
    valid: bool
    reason: str
    machine: str
    license_path: str
    public_key_configured: bool
    entitlement: Entitlement | None = None
    trial: dict | None = None
    quota: dict | None = None
    checked: str = ""
    code: str = ""

    def allows(self, feature: str | None) -> bool:
        if not self.valid:
            return False
        if self.mode == "development":
            return True
        if self.mode == "trial":
            return feature not in {
                FEATURE_NATIVE_RUNTIME, FEATURE_LICENSE_ISSUANCE, FEATURE_CI, FEATURE_OEM,
            }
        return self.entitlement is not None and self.entitlement.allows(feature)

    def watermark(self) -> dict:
        if self.mode == "development":
            return {
                "license_id": "development",
                "customer": None,
                "edition": "development",
                "trial": False,
                "builds_used": 0,
            }
        if self.mode == "trial":
            trial = self.trial or {}
            return {
                "license_id": "trial",
                "customer": None,
                "edition": EDITION_TRIAL,
                "trial": True,
                "builds_used": int(trial.get("builds", 0)),
            }
        if self.entitlement is not None:
            data = self.entitlement.watermark()
            if self.quota is not None:
                data["builds_used"] = int(self.quota.get("used", 0))
            return data
        return {"license_id": "unknown", "customer": None, "edition": "unknown", "trial": False}

    def to_dict(self) -> dict:
        entitlement = self.entitlement
        return {
            "mode": self.mode,
            "valid": self.valid,
            "reason": self.reason,
            "machine": self.machine,
            "license_path": self.license_path,
            "public_key_configured": self.public_key_configured,
            "license_id": entitlement.license_id if entitlement else None,
            "customer": entitlement.customer if entitlement else None,
            "edition": entitlement.edition if entitlement else (
                EDITION_TRIAL if self.mode == "trial" else None
            ),
            "features": list(entitlement.features) if entitlement else [],
            "expires": entitlement.expires.isoformat() if entitlement and entitlement.expires else None,
            "grace_days": entitlement.grace_days if entitlement else 0,
            "trial": self.trial,
            "quota": self.quota,
            "checked": self.checked,
            "code": self.code,
        }


@dataclass
class BuildReservation:
    token: str
    mode: str
    charged: bool
    duplicate: bool = False
    replayed: bool = False
    before_used: int = 0
    after_used: int = 0
    limit: int = 0
    license_id: str | None = None
    month: str | None = None
    machine: str = ""
    paid_by_credit: bool = False


@dataclass
class RefundResult:
    applied: bool
    already_applied: bool = False
    skipped_period_changed: bool = False
    before_used: int = 0
    after_used: int = 0
    limit: int = 0
    message: str = ""


@dataclass
class BuildSettlement:
    token: str
    state: str
    refund: RefundResult | None = None


_INTERNAL_TRIAL_KEYS = {
    "pending", "refunded", "completed", "idempotency", "idempotency_fingerprints",
}
_INTERNAL_QUOTA_KEYS = {
    "pending", "refunded", "completed", "idempotency",
    "idempotency_fingerprints", "credits",
}


def _empty_ledger() -> dict:
    return {
        "pending": {}, "refunded": {}, "completed": {},
        "idempotency": {}, "idempotency_fingerprints": {},
    }


def _upgrade_trial(payload: dict) -> dict:
    data = dict(payload)
    if data.get("format") == TRIAL_STATE_FORMAT_V1:
        data["format"] = TRIAL_STATE_FORMAT
    for key, value in _empty_ledger().items():
        data.setdefault(key, value)
    return data


def _upgrade_quota(payload: dict) -> dict:
    data = dict(payload)
    if data.get("format") == QUOTA_STATE_FORMAT_V1:
        data["format"] = QUOTA_STATE_FORMAT
    for key, value in _empty_ledger().items():
        data.setdefault(key, value)
    data.setdefault("credits", 0)
    return data


def _public_trial(payload: dict | None, days_left: int | None = None,
                  builds_left: int | None = None, not_started: bool = False) -> dict:
    data = dict(payload or {})
    public = {
        key: data.get(key)
        for key in ("started", "builds", "days", "max_builds", "last_build")
        if key in data
    }
    if days_left is not None:
        public["days_left"] = days_left
    if builds_left is not None:
        public["builds_left"] = builds_left
    if not_started:
        public["not_started"] = True
    return public


def _public_quota(payload: dict | None, limit: int | None = None) -> dict | None:
    if not payload:
        return None
    public = {
        key: payload.get(key)
        for key in ("license_id", "month", "used", "updated")
        if key in payload
    }
    if limit is not None:
        public["limit"] = limit
    return public


def ledger_lock_path() -> Path:
    return state_dir() / "ledger.lock"


def _append_ledger(event: dict) -> None:
    path = state_dir() / "build-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _prune(mapping: dict, keep: int = 1000) -> None:
    if len(mapping) <= keep:
        return
    ordered = sorted(
        mapping.items(),
        key=lambda item: str((item[1] or {}).get("at") or ""),
    )
    for key, _ in ordered[: len(mapping) - keep]:
        mapping.pop(key, None)


def _prune_idempotency(mapping: dict, keep: int = 1000) -> None:
    for key in list(mapping)[: max(0, len(mapping) - keep)]:
        mapping.pop(key, None)


def state_dir() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "PyShield"
    return Path.home() / ".pyshield"


def tool_license_path() -> Path:
    override = os.environ.get(ENV_LICENSE)
    if override:
        return Path(override).expanduser()
    return state_dir() / TOOL_LICENSE_FILE


def trial_path() -> Path:
    return state_dir() / TRIAL_FILE


def quota_path() -> Path:
    return state_dir() / QUOTA_FILE


def revocations_path() -> Path:
    return state_dir() / REVOCATIONS_FILE


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def active_public_key() -> bytes | None:
    """Load the root public key from the environment or the injected module."""
    value = os.environ.get(ENV_PUBLIC_KEY, "").strip()
    if value:
        if value.lower() in {"dev", "none", "off"}:
            return None
        try:
            return crypto.b85decode(value)
        except Exception as exc:
            raise ToolLicenseError("PYSHELD_TOOL_PUBLIC_KEY is not valid Base85") from exc
    try:
        from . import _tool_key  # type: ignore
    except ImportError:
        return None
    encoded = str(getattr(_tool_key, "PUBLIC_KEY_B85", "") or "").strip()
    if not encoded:
        return None
    try:
        return crypto.b85decode(encoded)
    except Exception as exc:
        raise ToolLicenseError("pyshield/_tool_key.py contains an invalid public key") from exc


def sign_document(
    format_name: str,
    payload: dict,
    signing_key: bytes,
    prefix: bytes = TOOL_SIGNATURE_PREFIX,
) -> dict:
    signature = ed25519.sign(signing_key, crypto.signed_message(prefix, payload))
    return {
        "format": format_name,
        "payload": payload,
        "signature": crypto.b85encode(signature),
    }


def verify_document(
    document: dict,
    public_key: bytes,
    expected_format: str,
    prefix: bytes = TOOL_SIGNATURE_PREFIX,
) -> dict:
    if not isinstance(document, dict):
        raise ToolLicenseError("license document must be a JSON object")
    if document.get("format") != expected_format:
        raise ToolLicenseError(
            f"unsupported license format {document.get('format')!r}; expected {expected_format!r}"
        )
    payload = document.get("payload")
    signature = document.get("signature")
    if not isinstance(payload, dict):
        raise ToolLicenseError("license document has no payload")
    if not isinstance(signature, str) or not signature:
        raise ToolLicenseError("license document is not signed")
    try:
        raw = crypto.b85decode(signature)
    except Exception as exc:
        raise ToolLicenseError("license signature is not valid Base85") from exc
    if not ed25519.verify(public_key, crypto.signed_message(prefix, payload), raw):
        raise ToolLicenseError("license signature verification failed")
    return payload


def _parse_date(value, field_name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ToolLicenseError(f"invalid {field_name} date {value!r}") from exc


def _as_int(value, field_name: str, default: int, minimum: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolLicenseError(f"invalid {field_name} {value!r}") from exc
    if number < minimum:
        raise ToolLicenseError(f"{field_name} must be at least {minimum}")
    return number


def parse_entitlement(payload: dict) -> Entitlement:
    license_id = str(payload.get("license_id") or "").strip()
    if not license_id:
        raise ToolLicenseError("tool license has no license_id")
    features = payload.get("features") or []
    if not isinstance(features, list):
        raise ToolLicenseError("tool license features must be a list")
    normalized_features = tuple(
        dict.fromkeys(str(item).strip() for item in features if str(item).strip())
    )
    bind_mode = str(payload.get("bind_mode") or BIND_ANY).strip().lower()
    if bind_mode not in BIND_MODES:
        raise ToolLicenseError(f"unknown bind_mode {bind_mode!r}")
    fingerprints = tuple(
        dict.fromkeys(
            str(item).strip().lower()
            for item in (payload.get("fingerprints") or [])
            if str(item).strip()
        )
    )
    if bind_mode == BIND_FIXED and not fingerprints:
        raise ToolLicenseError("fixed tool license requires at least one fingerprint")
    grace_days = _as_int(payload.get("grace_days"), "grace_days", 0)
    if grace_days > MAX_GRACE_DAYS:
        raise ToolLicenseError(f"grace_days must not exceed {MAX_GRACE_DAYS}")
    return Entitlement(
        license_id=license_id,
        customer=str(payload.get("customer") or "").strip(),
        edition=str(payload.get("edition") or EDITION_PRO).strip().lower(),
        features=normalized_features,
        seats=_as_int(payload.get("seats"), "seats", 1, 1),
        max_activations=_as_int(payload.get("max_activations"), "max_activations", 1, 1),
        max_builds_per_month=_as_int(payload.get("max_builds_per_month"), "max_builds_per_month", 0),
        bind_mode=bind_mode,
        fingerprints=fingerprints,
        activation_required=bool(payload.get("activation_required", bind_mode != BIND_FIXED)),
        issued=_parse_date(payload.get("issued"), "issued"),
        not_before=_parse_date(payload.get("not_before"), "not_before"),
        expires=_parse_date(payload.get("expires"), "expires"),
        grace_days=grace_days,
        revocation_epoch=_as_int(payload.get("revocation_epoch"), "revocation_epoch", 0),
        raw=dict(payload),
    )


def load_entitlement(
    path: str | Path | None = None,
    public_key: bytes | None = None,
    verify: bool = True,
) -> Entitlement | None:
    target = Path(path) if path is not None else tool_license_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ToolLicenseError(f"cannot read tool license {target}: {exc}") from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ToolLicenseError(f"tool license {target} is not valid JSON") from exc
    if not verify:
        if not isinstance(document, dict) or document.get("format") != TOOL_LICENSE_FORMAT:
            raise ToolLicenseError(f"tool license {target} has an unsupported format")
        payload = document.get("payload")
        if not isinstance(payload, dict):
            raise ToolLicenseError(f"tool license {target} has no payload")
    else:
        key = public_key if public_key is not None else active_public_key()
        if key is None:
            raise ToolLicenseError("tool public key is not configured")
        payload = verify_document(document, key, TOOL_LICENSE_FORMAT)
    return replace(parse_entitlement(payload), document=document)


def install_entitlement(path: str | Path | None = None) -> Entitlement:
    """Validate the license at *path* and copy it to the active license path."""
    source = Path(path) if path is not None else tool_license_path()
    entitlement = load_entitlement(source)
    if entitlement is None:
        raise ToolLicenseError(f"tool license not found: {source}")
    data = source.read_bytes()
    target = tool_license_path()
    secure_store.write_private(target, data)
    return entitlement


def remove_entitlement() -> bool:
    return secure_store.remove_private(tool_license_path())


def _revocations(public_key: bytes | None = None) -> set[str]:
    path = revocations_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise ToolLicenseError(f"cannot read revocation list {path}: {exc}") from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ToolLicenseError("revocation list is not valid JSON") from exc
    key = public_key if public_key is not None else active_public_key()
    if key is None:
        return set()
    payload = verify_document(document, key, REVOCATION_FORMAT, REVOCATION_SIGNATURE_PREFIX)
    revoked = payload.get("revoked") or []
    if not isinstance(revoked, list):
        raise ToolLicenseError("revocation list is malformed")
    return {str(item) for item in revoked if str(item).strip()}


def install_revocations(
    document: dict,
    public_key: bytes | None = None,
    path: str | Path | None = None,
) -> int:
    key = public_key if public_key is not None else active_public_key()
    if key is None:
        raise ToolLicenseError("tool public key is not configured")
    payload = verify_document(document, key, REVOCATION_FORMAT, REVOCATION_SIGNATURE_PREFIX)
    revoked = payload.get("revoked") or []
    if not isinstance(revoked, list):
        raise ToolLicenseError("revocation list is malformed")
    target = Path(path) if path is not None else revocations_path()
    secure_store.write_private(
        target,
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n",
    )
    return len(revoked)


def _trial_limits() -> tuple[int, int]:
    try:
        days = int(os.environ.get(ENV_TRIAL_DAYS, DEFAULT_TRIAL_DAYS))
    except ValueError:
        days = DEFAULT_TRIAL_DAYS
    try:
        builds = int(os.environ.get(ENV_TRIAL_BUILDS, DEFAULT_TRIAL_BUILDS))
    except ValueError:
        builds = DEFAULT_TRIAL_BUILDS
    return max(1, min(days, 365)), max(1, min(builds, 10000))


def _trial_state(machine: str, today: date, initialize: bool = True) -> dict:
    path = trial_path()
    data = secure_store.read_private(path)
    if data is None:
        days, builds = _trial_limits()
        payload = {
            "format": TRIAL_STATE_FORMAT,
            "started": today.isoformat() if initialize else None,
            "builds": 0,
            "days": days,
            "max_builds": builds,
            "machine": machine,
            "last_build": None,
            **_empty_ledger(),
        }
        if initialize:
            secure_store.write_private(path, secure_store.seal_state(payload, machine, "trial"))
        return payload
    try:
        payload = secure_store.open_state(data, machine, "trial")
    except secure_store.SecureStoreError as exc:
        raise ToolLicenseError("trial state is damaged or was modified") from exc
    if payload.get("format") not in {TRIAL_STATE_FORMAT, TRIAL_STATE_FORMAT_V1}:
        raise ToolLicenseError("trial state has an unsupported format")
    if str(payload.get("machine") or "").lower() != machine.lower():
        raise ToolLicenseError("trial state belongs to another machine")
    return _upgrade_trial(payload)


def _trial_days_left(payload: dict, today: date) -> int:
    started_raw = payload.get("started")
    if not started_raw:
        return int(payload.get("days") or DEFAULT_TRIAL_DAYS)
    try:
        started = date.fromisoformat(str(started_raw))
    except ValueError as exc:
        raise ToolLicenseError("trial state has an invalid start date") from exc
    if today < started:
        raise ToolLicenseError("system clock appears to be before the trial start date")
    days = int(payload.get("days") or DEFAULT_TRIAL_DAYS)
    return max(0, (started + timedelta(days=days) - today).days)


def _quota_state(entitlement: Entitlement, machine: str, today: date,
                 initialize: bool = True) -> dict:
    if entitlement.max_builds_per_month <= 0:
        return {}
    path = quota_path()
    data = secure_store.read_private(path)
    month = today.strftime("%Y-%m")
    previous: dict | None = None
    if data is not None:
        try:
            payload = secure_store.open_state(data, machine, "quota")
        except secure_store.SecureStoreError as exc:
            raise ToolLicenseError("build quota state is damaged or was modified") from exc
        if payload.get("format") not in {QUOTA_STATE_FORMAT, QUOTA_STATE_FORMAT_V1}:
            raise ToolLicenseError("build quota state has an unsupported format")
        payload = _upgrade_quota(payload)
        if payload.get("license_id") == entitlement.license_id and payload.get("month") == month:
            return payload
        if payload.get("license_id") == entitlement.license_id:
            previous = payload
    payload = {
        "format": QUOTA_STATE_FORMAT,
        "license_id": entitlement.license_id,
        "month": month,
        "used": 0,
        "machine": machine,
        "updated": today.isoformat(),
        "credits": int((previous or {}).get("credits", 0)),
        "pending": dict((previous or {}).get("pending", {})),
        "refunded": dict((previous or {}).get("refunded", {})),
        "completed": dict((previous or {}).get("completed", {})),
        "idempotency": dict((previous or {}).get("idempotency", {})),
    }
    if initialize:
        secure_store.write_private(path, secure_store.seal_state(payload, machine, "quota"))
    return payload


def _save_quota(payload: dict, machine: str) -> None:
    secure_store.write_private(
        quota_path(),
        secure_store.seal_state(payload, machine, "quota"),
    )


def _save_trial(payload: dict, machine: str) -> None:
    secure_store.write_private(
        trial_path(),
        secure_store.seal_state(payload, machine, "trial"),
    )


def _invalid(
    reason: str,
    machine: str,
    license_path: Path,
    public_key_configured: bool,
    entitlement: Entitlement | None = None,
    trial: dict | None = None,
    code: str = "",
) -> ToolLicenseStatus:
    return ToolLicenseStatus(
        mode="invalid",
        valid=False,
        reason=reason,
        machine=machine,
        license_path=str(license_path),
        public_key_configured=public_key_configured,
        entitlement=entitlement,
        trial=trial,
        checked=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        code=code,
    )


def check(
    features: tuple[str, ...] | list[str] = (),
    machine: str | None = None,
    today: date | None = None,
    license_path: str | Path | None = None,
    public_key: bytes | None = None,
    initialize_trial: bool = True,
) -> ToolLicenseStatus:
    """Return the current tool license state without consuming a build.

    ``initialize_trial=False`` makes status/validate calls truly read-only: a
    missing trial or quota state is reported without creating it.
    """
    machine = (machine or fingerprint.machine_fingerprint()).lower()
    today = today or date.today()
    path = Path(license_path) if license_path is not None else tool_license_path()
    key = public_key if public_key is not None else active_public_key()
    configured = key is not None
    checked = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if _env_flag(ENV_DEV):
        return ToolLicenseStatus(
            mode="development",
            valid=True,
            reason="development mode: PYSHELD_DEV is set",
            machine=machine,
            license_path=str(path),
            public_key_configured=configured,
            checked=checked,
            code="development",
        )

    unverified = not configured
    entitlement: Entitlement | None = None
    if unverified:
        # Source runs ship no injected root key. Trust a locally installed
        # license so the trial flow can be exercised end to end; packaged
        # builds always embed the public key and verify signatures.
        if path.is_file():
            try:
                entitlement = load_entitlement(path, verify=False)
            except ToolLicenseError:
                entitlement = None
        if entitlement is None:
            return ToolLicenseStatus(
                mode="unlicensed",
                valid=False,
                reason="尚未激活：请先在「版本与试用」中申请试用或导入授权",
                machine=machine,
                license_path=str(path),
                public_key_configured=False,
                checked=checked,
                code="not_activated",
            )
    else:
        try:
            entitlement = load_entitlement(path, public_key=key)
        except ToolLicenseError as exc:
            return _invalid(str(exc), machine, path, configured, code="license_invalid")

    if entitlement is not None:
        trial_edition = entitlement.edition == EDITION_TRIAL
        active, reason = entitlement.active_on(today)
        if not active:
            code = "trial_expired" if trial_edition else "expired"
            return _invalid(reason, machine, path, configured, entitlement, code=code)
        if entitlement.activation_required and entitlement.bind_mode != BIND_FIXED:
            return _invalid(
                "tool license must be activated on this machine first",
                machine, path, configured, entitlement, code="activation_required",
            )
        if not entitlement.machine_allowed(machine):
            return _invalid(
                f"tool license is bound to another machine; this machine is {machine}",
                machine, path, configured, entitlement, code="machine_mismatch",
            )
        try:
            revoked = _revocations(key)
        except ToolLicenseError as exc:
            return _invalid(str(exc), machine, path, configured, entitlement, code="revoked")
        if entitlement.license_id in revoked:
            return _invalid(
                "tool license has been revoked", machine, path, configured, entitlement,
                code="revoked",
            )
        missing = [feature for feature in features if not entitlement.allows(feature)]
        if missing:
            return _invalid(
                f"tool license does not include: {', '.join(missing)}",
                machine, path, configured, entitlement, code="feature_missing",
            )
        try:
            quota = _quota_state(entitlement, machine, today, initialize=initialize_trial)
        except ToolLicenseError as exc:
            return _invalid(str(exc), machine, path, configured, entitlement, code="license_invalid")
        used = int(quota.get("used", 0)) if quota else 0
        credits = int(quota.get("credits", 0)) if quota else 0
        limit = entitlement.max_builds_per_month
        if limit and used >= limit and credits <= 0:
            if trial_edition:
                message = (
                    f"trial build limit reached ({used}/{limit}); "
                    "purchase a Pro license to keep building"
                )
            else:
                message = f"monthly build quota exhausted ({used}/{limit})"
            return _invalid(
                message, machine, path, configured, entitlement, code="quota_exhausted",
            )
        if quota:
            quota = _public_quota(quota, limit)
            if credits > 0:
                reason = f"{reason}; {credits} carried-over credit(s) available"
            elif "grace period" in reason:
                reason = f"{reason}; {limit - used} builds left this month"
            else:
                reason = f"valid; {limit - used} builds left this month"
        if unverified:
            reason = f"{reason} (local license, signature not verified)"
        return ToolLicenseStatus(
            mode="licensed",
            valid=True,
            reason=reason,
            machine=machine,
            license_path=str(path),
            public_key_configured=configured,
            entitlement=entitlement,
            quota=quota or None,
            checked=checked,
            code="trial" if trial_edition else "ok",
        )

    try:
        trial = _trial_state(machine, today, initialize=initialize_trial)
        days_left = _trial_days_left(trial, today)
    except ToolLicenseError as exc:
        return _invalid(str(exc), machine, path, configured, code="license_invalid")
    used = int(trial.get("builds") or 0)
    limit = int(trial.get("max_builds") or DEFAULT_TRIAL_BUILDS)
    not_started = not trial.get("started")
    if days_left <= 0:
        return _invalid(
            "trial period has expired", machine, path, configured,
            trial=_public_trial(trial, days_left, max(0, limit - used)),
            code="trial_expired",
        )
    if used >= limit:
        return _invalid(
            f"trial build limit reached ({used}/{limit})",
            machine, path, configured,
            trial=_public_trial(trial, days_left, max(0, limit - used)),
            code="quota_exhausted",
        )
    blocked = [feature for feature in features if feature in {
        FEATURE_NATIVE_RUNTIME, FEATURE_LICENSE_ISSUANCE, FEATURE_CI, FEATURE_OEM,
    }]
    if blocked:
        return _invalid(
            f"trial does not include: {', '.join(blocked)}",
            machine, path, configured,
            trial=_public_trial(trial, days_left, max(0, limit - used), not_started),
            code="feature_missing",
        )
    reason = f"trial: {days_left} days left, {limit - used} builds left"
    if not_started:
        reason = f"trial not started: {days_left} days, {limit - used} builds available"
    return ToolLicenseStatus(
        mode="trial",
        valid=True,
        reason=reason,
        machine=machine,
        license_path=str(path),
        public_key_configured=configured,
        trial=_public_trial(trial, days_left, max(0, limit - used), not_started),
        checked=checked,
        code="trial",
    )


def trial_policy() -> dict:
    """Return the limits that apply to the trial edition."""
    return {
        "ciphers": list(TRIAL_CIPHERS),
        "integrity": list(TRIAL_INTEGRITY),
        "features": [FEATURE_GUI],
    }


def trial_build_problems(
    cipher: str | None,
    integrity: str | None,
    customer: str | None = None,
) -> list[str]:
    """Return human readable reasons why a build exceeds the trial limits."""
    problems: list[str] = []
    if cipher and cipher not in TRIAL_CIPHERS:
        problems.append("试用版只能使用 ChaCha20 纯 Python（--cipher chacha-pure）")
    if integrity and integrity not in TRIAL_INTEGRITY:
        problems.append("试用版不支持完整校验清单（--integrity full）")
    if customer and str(customer).strip():
        problems.append("试用版不能在产物许可证中写入客户名称")
    return problems


def require(
    features: tuple[str, ...] | list[str] = (),
    **kwargs,
) -> ToolLicenseStatus:
    status = check(features=features, **kwargs)
    if not status.valid:
        raise ToolLicenseError(status.reason)
    return status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _candidate_states(machine: str, today: date) -> list[tuple[str, dict, Entitlement | None]]:
    """Load trial and quota ledgers without creating or charging anything."""
    candidates: list[tuple[str, dict, Entitlement | None]] = []
    try:
        trial = _trial_state(machine, today, initialize=False)
    except ToolLicenseError:
        trial = None
    if trial is not None:
        candidates.append(("trial", _upgrade_trial(trial), None))
    try:
        key = active_public_key()
        entitlement = load_entitlement(public_key=key, verify=key is not None)
    except ToolLicenseError:
        entitlement = None
    if entitlement is not None:
        try:
            quota = _quota_state(entitlement, machine, today, initialize=False)
        except ToolLicenseError:
            quota = None
        if quota:
            candidates.append(("licensed", _upgrade_quota(quota), entitlement))
    return candidates


def _reservation_from_info(info: dict, mode: str, machine: str, bucket: str) -> BuildReservation:
    return BuildReservation(
        token=str(info.get("token") or ""),
        mode=mode,
        charged=False,
        duplicate=True,
        replayed=bucket != "pending",
        before_used=int(info.get("before_used", 0)),
        after_used=int(info.get("after_used", 0)),
        limit=int(info.get("limit", 0)),
        license_id=info.get("license_id"),
        month=info.get("month"),
        machine=machine,
        paid_by_credit=bool(info.get("paid_by_credit", False)),
    )


def _find_existing_reservation(
    token: str,
    idempotency_key: str | None,
    machine: str,
    today: date,
    idempotency_fingerprint: str | None = None,
) -> BuildReservation | None:
    key_seen = False
    for mode, payload, _entitlement in _candidate_states(machine, today):
        lookup = token
        if idempotency_key and idempotency_key in payload.get("idempotency", {}):
            key_seen = True
            stored = payload.get("idempotency_fingerprints", {}).get(idempotency_key)
            if (
                idempotency_fingerprint
                and stored
                and str(stored) != str(idempotency_fingerprint)
            ):
                raise ToolLicenseError(
                    "idempotency key was used with different build parameters"
                )
            lookup = str(payload["idempotency"][idempotency_key])
        for bucket in ("pending", "completed", "refunded"):
            info = payload.get(bucket, {}).get(lookup)
            if info:
                return _reservation_from_info(info, mode, machine, bucket)
    if key_seen:
        raise ToolLicenseError("idempotency key refers to an unknown build")
    return None


def find_reservation(
    token: str | None = None,
    idempotency_key: str | None = None,
    today: date | None = None,
    timeout: float = 10.0,
    machine: str | None = None,
    idempotency_fingerprint: str | None = None,
) -> BuildReservation | None:
    """Look up an existing reservation without charging anything."""
    machine = (machine or fingerprint.machine_fingerprint()).lower()
    today = today or date.today()
    with secure_store.exclusive_lock(ledger_lock_path(), timeout=timeout):
        return _find_existing_reservation(
            str(token or ""), idempotency_key, machine, today,
            idempotency_fingerprint=idempotency_fingerprint,
        )


def _begin_trial(token: str, machine: str, today: date,
                 idempotency_key: str | None,
                 idempotency_fingerprint: str | None = None) -> BuildReservation:
    payload = _upgrade_trial(_trial_state(machine, today, initialize=True))
    ledger = payload
    used = int(ledger.get("builds", 0))
    limit = int(ledger.get("max_builds") or DEFAULT_TRIAL_BUILDS)
    if used >= limit:
        raise ToolLicenseError(f"trial build limit reached ({used}/{limit})")
    info = {
        "token": token,
        "mode": "trial",
        "charged": True,
        "before_used": used,
        "after_used": used + 1,
        "limit": limit,
        "license_id": None,
        "month": None,
        "paid_by_credit": False,
        "at": _now_iso(),
    }
    ledger["builds"] = used + 1
    ledger["last_build"] = today.isoformat()
    ledger["pending"][token] = info
    if idempotency_key:
        ledger["idempotency"][idempotency_key] = token
        if idempotency_fingerprint:
            ledger["idempotency_fingerprints"][idempotency_key] = idempotency_fingerprint
        _prune_idempotency(ledger["idempotency"])
        _prune_idempotency(ledger["idempotency_fingerprints"])
    _save_trial(ledger, machine)
    _append_ledger({
        "event": "begin", "token": token, "mode": "trial",
        "charged": True, "before": used, "after": used + 1,
        "limit": limit, "at": info["at"],
    })
    return BuildReservation(
        token=token, mode="trial", charged=True, before_used=used,
        after_used=used + 1, limit=limit, machine=machine,
    )


def _begin_licensed(token: str, machine: str, today: date,
                    entitlement: Entitlement, idempotency_key: str | None,
                    idempotency_fingerprint: str | None = None) -> BuildReservation:
    if entitlement.max_builds_per_month <= 0:
        return BuildReservation(
            token=token, mode="licensed", charged=False, limit=0,
            license_id=entitlement.license_id, month=today.strftime("%Y-%m"),
            machine=machine,
        )
    payload = _upgrade_quota(_quota_state(entitlement, machine, today, initialize=True))
    ledger = payload
    used = int(ledger.get("used", 0))
    credits = int(ledger.get("credits", 0))
    limit = entitlement.max_builds_per_month
    if used >= limit and credits <= 0:
        raise ToolLicenseError(f"monthly build quota exhausted ({used}/{limit})")
    before = used
    paid_by_credit = credits > 0
    if paid_by_credit:
        credits -= 1
    else:
        used += 1
    info = {
        "token": token,
        "mode": "licensed",
        "charged": True,
        "before_used": before,
        "after_used": used,
        "limit": limit,
        "license_id": entitlement.license_id,
        "month": ledger.get("month"),
        "paid_by_credit": paid_by_credit,
        "at": _now_iso(),
    }
    ledger["used"] = used
    ledger["credits"] = credits
    ledger["updated"] = today.isoformat()
    ledger["pending"][token] = info
    if idempotency_key:
        ledger["idempotency"][idempotency_key] = token
        if idempotency_fingerprint:
            ledger["idempotency_fingerprints"][idempotency_key] = idempotency_fingerprint
        _prune_idempotency(ledger["idempotency"])
        _prune_idempotency(ledger["idempotency_fingerprints"])
    _save_quota(ledger, machine)
    _append_ledger({
        "event": "begin", "token": token, "mode": "licensed",
        "charged": True, "before": before, "after": used,
        "limit": limit, "credit": paid_by_credit, "at": info["at"],
    })
    return BuildReservation(
        token=token, mode="licensed", charged=True, before_used=before,
        after_used=used, limit=limit, license_id=entitlement.license_id,
        month=info["month"], machine=machine, paid_by_credit=paid_by_credit,
    )


def begin_build(
    features: tuple[str, ...] | list[str] = (),
    token: str | None = None,
    idempotency_key: str | None = None,
    today: date | None = None,
    timeout: float = 10.0,
    machine: str | None = None,
    idempotency_fingerprint: str | None = None,
) -> BuildReservation:
    """Atomically consume one build and record a pending reservation.

    The license is re-checked while holding the ledger lock, so two processes
    cannot both observe the last remaining build and charge it twice.
    """
    token = str(token or secrets.token_hex(16))
    today = today or date.today()
    machine = (machine or fingerprint.machine_fingerprint()).lower()
    with secure_store.exclusive_lock(ledger_lock_path(), timeout=timeout):
        existing = _find_existing_reservation(
            token, idempotency_key, machine, today,
            idempotency_fingerprint=idempotency_fingerprint,
        )
        if existing is not None:
            return existing
        status = check(features=features, machine=machine, today=today, initialize_trial=True)
        if not status.valid:
            raise ToolLicenseError(status.reason)
        if status.mode == "development":
            return BuildReservation(
                token=token, mode="development", charged=False, machine=machine,
            )
        if status.mode == "trial":
            return _begin_trial(
                token, machine, today, idempotency_key, idempotency_fingerprint,
            )
        if status.mode == "licensed" and status.entitlement is not None:
            return _begin_licensed(
                token, machine, today, status.entitlement, idempotency_key,
                idempotency_fingerprint,
            )
    raise ToolLicenseError(f"unsupported license mode: {status.mode}")


def _load_ledger_for_reservation(reservation: BuildReservation, today: date) -> tuple[dict, Entitlement | None]:
    if reservation.mode == "trial":
        return _upgrade_trial(_trial_state(reservation.machine, today, initialize=False)), None
    if reservation.mode == "licensed":
        try:
            key = active_public_key()
            entitlement = load_entitlement(public_key=key, verify=key is not None)
        except ToolLicenseError:
            entitlement = None
        if entitlement is None:
            raise ToolLicenseError("tool license is no longer available")
        payload = _quota_state(entitlement, reservation.machine, today, initialize=False)
        if not payload:
            raise ToolLicenseError("build quota state is unavailable")
        return _upgrade_quota(payload), entitlement
    raise ToolLicenseError(f"cannot load ledger for mode {reservation.mode}")


def refund_build(
    reservation: BuildReservation,
    failure_class: str,
    code: str,
    message: str,
    evidence: str = "",
    today: date | None = None,
    timeout: float = 10.0,
) -> RefundResult:
    """Refund a reservation exactly once, under the ledger lock."""
    today = today or date.today()
    if reservation.mode == "development":
        return RefundResult(applied=False, message="development mode does not consume quota")
    with secure_store.exclusive_lock(ledger_lock_path(), timeout=timeout):
        ledger, entitlement = _load_ledger_for_reservation(reservation, today)
        token = reservation.token
        if token in ledger.get("refunded", {}):
            info = ledger["refunded"][token]
            return RefundResult(
                applied=True, already_applied=True,
                before_used=int(info.get("before_used", 0)),
                after_used=int(info.get("after_used", 0)),
                limit=int(info.get("limit", 0)),
                message="quota was already refunded for this build",
            )
        info = ledger.get("pending", {}).pop(token, None)
        if info is None:
            if token in ledger.get("completed", {}):
                return RefundResult(applied=False, message="build already completed; not refundable")
            return RefundResult(applied=False, message="unknown build reservation")
        before = int(info.get("before_used", 0))
        limit = int(info.get("limit", 0))
        if reservation.mode == "trial":
            current = int(ledger.get("builds", 0))
            ledger["builds"] = max(0, current - 1)
            after = ledger["builds"]
        else:
            current_used = int(ledger.get("used", 0))
            current_month = ledger.get("month")
            current_license = ledger.get("license_id")
            if info.get("paid_by_credit"):
                ledger["credits"] = int(ledger.get("credits", 0)) + 1
                after = current_used
            elif info.get("month") == current_month and info.get("license_id") == current_license:
                ledger["used"] = max(0, current_used - 1)
                after = ledger["used"]
            else:
                ledger["credits"] = int(ledger.get("credits", 0)) + 1
                after = current_used
            ledger["updated"] = today.isoformat()
        refunded = {
            **info,
            "refunded_at": _now_iso(),
            "failure_class": failure_class,
            "code": code,
            "message": message,
            "evidence": str(evidence or "")[:2000],
        }
        ledger["refunded"][token] = refunded
        _prune(ledger["refunded"])
        if reservation.mode == "trial":
            _save_trial(ledger, reservation.machine)
        else:
            _save_quota(ledger, reservation.machine)
        _append_ledger({
            "event": "refund", "token": token, "mode": reservation.mode,
            "failure_class": failure_class, "code": code,
            "before": before, "after": after, "limit": limit,
            "at": refunded["refunded_at"],
        })
        return RefundResult(
            applied=True, before_used=before, after_used=after, limit=limit,
            message="quota refunded",
        )


def finalize_build(
    reservation: BuildReservation,
    refund: bool = False,
    reason_code: str = "",
    message: str = "",
    evidence: str = "",
    today: date | None = None,
    timeout: float = 10.0,
) -> BuildSettlement:
    """Close a pending reservation, optionally refunding it first."""
    if refund:
        result = refund_build(
            reservation, "PROJECT_CODE", reason_code, message, evidence,
            today=today, timeout=timeout,
        )
        state = "refunded" if result.applied or result.already_applied else "refund_failed"
        return BuildSettlement(token=reservation.token, state=state, refund=result)
    if reservation.mode == "development":
        return BuildSettlement(token=reservation.token, state="completed")
    today = today or date.today()
    with secure_store.exclusive_lock(ledger_lock_path(), timeout=timeout):
        ledger, _entitlement = _load_ledger_for_reservation(reservation, today)
        token = reservation.token
        if token in ledger.get("completed", {}):
            return BuildSettlement(token=token, state="completed")
        if token in ledger.get("refunded", {}):
            return BuildSettlement(token=token, state="refunded")
        info = ledger.get("pending", {}).pop(token, None)
        if info is None:
            return BuildSettlement(token=token, state="missing")
        completed = {**info, "completed_at": _now_iso(), "reason": reason_code}
        ledger["completed"][token] = completed
        _prune(ledger["completed"])
        if reservation.mode == "trial":
            _save_trial(ledger, reservation.machine)
        else:
            _save_quota(ledger, reservation.machine)
        _append_ledger({
            "event": "complete", "token": token, "mode": reservation.mode,
            "reason": reason_code, "at": completed["completed_at"],
        })
        return BuildSettlement(token=token, state="completed")


def record_build(status: ToolLicenseStatus, today: date | None = None) -> None:
    """Compatibility wrapper for CLI/GUI: consume one build atomically.

    New callers should use :func:`begin_build` / :func:`finalize_build` so the
    reservation can be refunded when the target project itself is broken.
    """
    today = today or date.today()
    if not status.valid or status.mode == "development":
        return
    with secure_store.exclusive_lock(ledger_lock_path()):
        token = "legacy-" + secrets.token_hex(8)
        if status.mode == "trial":
            payload = _upgrade_trial(_trial_state(status.machine, today, initialize=True))
            used = int(payload.get("builds", 0))
            limit = int(payload.get("max_builds") or DEFAULT_TRIAL_BUILDS)
            payload["builds"] = used + 1
            payload["last_build"] = today.isoformat()
            _save_trial(payload, status.machine)
            status.trial = _public_trial(
                payload, _trial_days_left(payload, today), max(0, limit - used - 1),
            )
            _append_ledger({
                "event": "legacy_charge", "token": token, "mode": "trial",
                "before": used, "after": used + 1, "at": _now_iso(),
            })
            return
        if status.mode == "licensed" and status.entitlement is not None:
            entitlement = status.entitlement
            if entitlement.max_builds_per_month <= 0:
                return
            payload = _upgrade_quota(
                _quota_state(entitlement, status.machine, today, initialize=True)
            )
            used = int(payload.get("used", 0))
            credits = int(payload.get("credits", 0))
            if credits > 0:
                payload["credits"] = credits - 1
            else:
                used += 1
                payload["used"] = used
            payload["updated"] = today.isoformat()
            _save_quota(payload, status.machine)
            status.quota = _public_quota(payload, entitlement.max_builds_per_month)
            _append_ledger({
                "event": "legacy_charge", "token": token, "mode": "licensed",
                "before": used - (0 if credits > 0 else 1), "after": used,
                "at": _now_iso(),
            })
            return


def status_dict(**kwargs) -> dict:
    status = check(**kwargs)
    return status.to_dict()
