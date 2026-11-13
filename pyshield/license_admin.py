"""Offline administration for PyShield commercial tool licenses.

Keep the root private key offline. This module is intentionally not imported by
the runtime or the builder; it is only used by the license issuer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import activation, crypto, ed25519, secure_store, tool_license

ROOT_KEY_FORMAT = "pyshield-root-key/1"

EDITION_FEATURES = {
    tool_license.EDITION_TRIAL: [],
    tool_license.EDITION_PRO: [
        tool_license.FEATURE_GUI,
        tool_license.FEATURE_NATIVE_RUNTIME,
        tool_license.FEATURE_LICENSE_ISSUANCE,
    ],
    tool_license.EDITION_ENTERPRISE: [
        tool_license.FEATURE_GUI,
        tool_license.FEATURE_NATIVE_RUNTIME,
        tool_license.FEATURE_OFFLINE_ACTIVATION,
        tool_license.FEATURE_LICENSE_ISSUANCE,
        tool_license.FEATURE_CI,
    ],
    tool_license.EDITION_OEM: list(tool_license.ALL_FEATURES),
}


class LicenseAdminError(Exception):
    """Raised when a root key, entitlement or revocation operation fails."""


def generate_root(out_dir: str | Path, force: bool = False) -> tuple[Path, Path, bytes]:
    target = Path(out_dir).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    key_path = target / "root.key"
    public_path = target / "root.pub"
    if key_path.exists() and not force:
        raise LicenseAdminError(f"{key_path} already exists; pass --force to replace it")
    seed = os.urandom(32)
    public = ed25519.public_key(seed)
    document = {
        "format": ROOT_KEY_FORMAT,
        "seed": crypto.b85encode(seed),
        "public_key": crypto.b85encode(public),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    secure_store.write_private(
        key_path,
        json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n",
    )
    public_path.write_text(crypto.b85encode(public) + "\n", encoding="utf-8")
    return key_path, public_path, public


def load_root_key(path: str | Path) -> bytes:
    target = Path(path).expanduser()
    try:
        raw = target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LicenseAdminError(f"cannot read root key {target}: {exc}") from exc
    try:
        document = json.loads(raw)
    except ValueError:
        document = None
    if isinstance(document, dict) and document.get("format") == ROOT_KEY_FORMAT:
        encoded = str(document.get("seed") or "")
    else:
        encoded = raw
    try:
        seed = crypto.b85decode(encoded)
    except Exception as exc:
        raise LicenseAdminError("root key is not valid Base85") from exc
    if len(seed) != 32:
        raise LicenseAdminError("root key must contain a 32-byte Ed25519 seed")
    return seed


def root_public_key(path: str | Path) -> bytes:
    return ed25519.public_key(load_root_key(path))


def _parse_date(value: str | None, field_name: str, default: date | None = None) -> date | None:
    if value in (None, ""):
        return default
    text = str(value).strip()
    if text.isdigit():
        return date.today() + timedelta(days=int(text))
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise LicenseAdminError(f"invalid {field_name} {value!r}; use days or YYYY-MM-DD") from exc


def _features(value: str | None, edition: str) -> list[str]:
    if value is None or not str(value).strip():
        return list(EDITION_FEATURES.get(edition, []))
    items = [item.strip() for item in str(value).replace(";", ",").split(",") if item.strip()]
    unknown = [item for item in items if item not in tool_license.ALL_FEATURES and item != "*"]
    if unknown:
        raise LicenseAdminError(f"unknown features: {', '.join(unknown)}")
    return list(dict.fromkeys(items))


def issue_entitlement(
    signing_key: bytes,
    license_id: str,
    customer: str = "",
    edition: str = tool_license.EDITION_PRO,
    features: list[str] | None = None,
    seats: int = 1,
    max_activations: int = 1,
    max_builds_per_month: int = 100,
    bind_mode: str = tool_license.BIND_ANY,
    fingerprints: list[str] | None = None,
    activation_required: bool | None = None,
    expires: str | date | None = None,
    grace_days: int = 14,
    not_before: str | date | None = None,
    notes: str = "",
) -> dict:
    edition = str(edition or tool_license.EDITION_PRO).strip().lower()
    if edition not in tool_license.EDITIONS:
        raise LicenseAdminError(f"unknown edition {edition!r}")
    fingerprints = [str(item).strip().lower() for item in (fingerprints or []) if str(item).strip()]
    bind_mode = str(bind_mode or tool_license.BIND_ANY).strip().lower()
    if bind_mode not in tool_license.BIND_MODES:
        raise LicenseAdminError(f"unknown bind mode {bind_mode!r}")
    if fingerprints and bind_mode == tool_license.BIND_ANY:
        bind_mode = tool_license.BIND_FIXED
    if bind_mode == tool_license.BIND_FIXED and not fingerprints:
        raise LicenseAdminError("fixed binding requires at least one fingerprint")
    if activation_required is None:
        activation_required = bind_mode != tool_license.BIND_FIXED
    if isinstance(expires, str):
        expires = _parse_date(expires, "expires")
    if expires is None:
        expires = date.today() + timedelta(days=365)
    if isinstance(not_before, str):
        not_before = _parse_date(not_before, "not_before")
    payload = {
        "license_id": str(license_id).strip(),
        "customer": str(customer or "").strip(),
        "edition": edition,
        "features": list(features) if features is not None else _features(None, edition),
        "seats": int(seats),
        "max_activations": int(max_activations),
        "max_builds_per_month": int(max_builds_per_month),
        "bind_mode": bind_mode,
        "fingerprints": fingerprints,
        "activation_required": bool(activation_required),
        "issued": date.today().isoformat(),
        "not_before": (not_before or date.today()).isoformat(),
        "expires": expires.isoformat() if expires else None,
        "grace_days": int(grace_days),
        "revocation_epoch": 0,
        "notes": str(notes or ""),
    }
    tool_license.parse_entitlement(payload)
    return tool_license.sign_document(tool_license.TOOL_LICENSE_FORMAT, payload, signing_key)


def write_document(document: dict, path: str | Path) -> Path:
    target = Path(path).expanduser()
    secure_store.write_private(
        target,
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n",
    )
    return target


def activation_response(
    request_path: str | Path,
    root_key: bytes,
    out_path: str | Path,
    public_key: bytes | None = None,
) -> dict:
    try:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LicenseAdminError(f"cannot read activation request: {exc}") from exc
    if not isinstance(request, dict) or request.get("format") != activation.ACTIVATION_REQUEST_FORMAT:
        request_format = request.get("format") if isinstance(request, dict) else None
        raise LicenseAdminError(f"unsupported activation request format: {request_format!r}")
    license_document = request.get("license")
    machine = str(request.get("machine") or "").strip().lower()
    if not isinstance(license_document, dict) or not machine:
        raise LicenseAdminError("activation request is missing the license or machine")
    key = public_key if public_key is not None else ed25519.public_key(root_key)
    payload = tool_license.verify_document(
        license_document, key, tool_license.TOOL_LICENSE_FORMAT,
    )
    entitlement = tool_license.parse_entitlement(payload)
    if not entitlement.active_on(date.today())[0]:
        raise LicenseAdminError(entitlement.active_on(date.today())[1])
    response = dict(payload)
    response.update({
        "bind_mode": tool_license.BIND_FIXED,
        "fingerprints": [machine],
        "activation_required": False,
        "activated": machine,
        "activated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    response.pop("signature", None)
    document = tool_license.sign_document(tool_license.TOOL_LICENSE_FORMAT, response, root_key)
    write_document(document, out_path)
    return document


def revoke(
    root_key: bytes,
    license_id: str,
    path: str | Path,
    reason: str = "",
    public_key: bytes | None = None,
) -> dict:
    target = Path(path).expanduser()
    payload = {
        "format": tool_license.REVOCATION_FORMAT,
        "issued": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "epoch": 1,
        "revoked": [],
        "reason": reason or "",
    }
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            key = public_key if public_key is not None else ed25519.public_key(root_key)
            previous = tool_license.verify_document(
                existing, key, tool_license.REVOCATION_FORMAT,
                tool_license.REVOCATION_SIGNATURE_PREFIX,
            )
            payload["revoked"] = list(previous.get("revoked") or [])
            payload["epoch"] = int(previous.get("epoch") or 1) + 1
        except (OSError, ValueError, tool_license.ToolLicenseError) as exc:
            raise LicenseAdminError(f"cannot read existing revocation list: {exc}") from exc
    if license_id not in payload["revoked"]:
        payload["revoked"].append(license_id)
    payload["revoked"].sort()
    document = tool_license.sign_document(
        tool_license.REVOCATION_FORMAT, payload, root_key,
        tool_license.REVOCATION_SIGNATURE_PREFIX,
    )
    write_document(document, target)
    return document


def show_license(path: str | Path, public_key: bytes) -> dict:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LicenseAdminError(f"cannot read license: {exc}") from exc
    payload = tool_license.verify_document(document, public_key, tool_license.TOOL_LICENSE_FORMAT)
    entitlement = tool_license.parse_entitlement(payload)
    return {
        "license_id": entitlement.license_id,
        "customer": entitlement.customer,
        "edition": entitlement.edition,
        "features": list(entitlement.features),
        "bind_mode": entitlement.bind_mode,
        "fingerprints": list(entitlement.fingerprints),
        "activation_required": entitlement.activation_required,
        "issued": entitlement.issued.isoformat() if entitlement.issued else None,
        "not_before": entitlement.not_before.isoformat() if entitlement.not_before else None,
        "expires": entitlement.expires.isoformat() if entitlement.expires else None,
        "grace_days": entitlement.grace_days,
        "seats": entitlement.seats,
        "max_activations": entitlement.max_activations,
        "max_builds_per_month": entitlement.max_builds_per_month,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyshield-license-admin", description="Issue PyShield tool licenses.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="generate an offline root key pair")
    init.add_argument("--out", required=True, help="output directory for root.key and root.pub")
    init.add_argument("--force", action="store_true", help="replace an existing root.key")

    issue = subparsers.add_parser("issue", help="issue a signed tool entitlement")
    issue.add_argument("--key", required=True, help="root.key path")
    issue.add_argument("--license-id", required=True)
    issue.add_argument("--customer", default="")
    issue.add_argument("--edition", choices=tool_license.EDITIONS, default=tool_license.EDITION_PRO)
    issue.add_argument("--features", help="comma-separated feature list; defaults to the edition preset")
    issue.add_argument("--seats", type=int, default=1)
    issue.add_argument("--max-activations", type=int, default=1)
    issue.add_argument("--max-builds", type=int, default=100, help="monthly build limit; 0 means unlimited")
    issue.add_argument("--bind-mode", choices=tool_license.BIND_MODES, default=tool_license.BIND_ANY)
    issue.add_argument("--fingerprint", action="append", default=[], help="machine fingerprint; repeatable")
    issue.add_argument("--activation-required", dest="activation_required", action="store_true", default=None)
    issue.add_argument("--no-activation-required", dest="activation_required", action="store_false")
    issue.add_argument("--expires", default=None, help="days or YYYY-MM-DD; defaults to one year")
    issue.add_argument("--grace-days", type=int, default=14)
    issue.add_argument("--not-before", default=None)
    issue.add_argument("--notes", default="")
    issue.add_argument("--out", required=True)

    response = subparsers.add_parser("activation-response", help="sign an offline activation response")
    response.add_argument("--key", required=True)
    response.add_argument("--request", required=True)
    response.add_argument("--out", required=True)

    revoke = subparsers.add_parser("revoke", help="add a license to the signed revocation list")
    revoke.add_argument("--key", required=True)
    revoke.add_argument("--license-id", required=True)
    revoke.add_argument("--revocations", required=True)
    revoke.add_argument("--reason", default="")

    show = subparsers.add_parser("show", help="verify and print an entitlement")
    show.add_argument("--license", required=True)
    show.add_argument("--public-key", required=True, help="Base85 root public key")

    public = subparsers.add_parser("public-key", help="print the public key for a root key")
    public.add_argument("--key", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "init":
            key_path, public_path, public = generate_root(args.out, args.force)
            print(f"root key:    {key_path}")
            print(f"public key:  {public_path}")
            print(f"PYSHELD_TOOL_PUBLIC_KEY={crypto.b85encode(public)}")
            print("Keep root.key offline. Never ship it with the builder.")
        elif args.command == "issue":
            key = load_root_key(args.key)
            document = issue_entitlement(
                key,
                license_id=args.license_id,
                customer=args.customer,
                edition=args.edition,
                features=_features(args.features, args.edition),
                seats=args.seats,
                max_activations=args.max_activations,
                max_builds_per_month=args.max_builds,
                bind_mode=args.bind_mode,
                fingerprints=args.fingerprint,
                activation_required=args.activation_required,
                expires=args.expires,
                grace_days=args.grace_days,
                not_before=args.not_before,
                notes=args.notes,
            )
            print(f"license written: {write_document(document, args.out)}")
        elif args.command == "activation-response":
            key = load_root_key(args.key)
            document = activation_response(args.request, key, args.out)
            print(f"activation response written: {args.out}")
            print(json.dumps(document["payload"], indent=2, sort_keys=True, ensure_ascii=False))
        elif args.command == "revoke":
            key = load_root_key(args.key)
            document = revoke(key, args.license_id, args.revocations, args.reason)
            print(f"revocation list updated: {args.revocations} ({len(document['payload']['revoked'])} entries)")
        elif args.command == "show":
            print(json.dumps(show_license(args.license, crypto.b85decode(args.public_key)),
                             indent=2, sort_keys=True, ensure_ascii=False))
        elif args.command == "public-key":
            print(crypto.b85encode(root_public_key(args.key)))
    except (LicenseAdminError, tool_license.ToolLicenseError, crypto.CryptoError, OSError) as exc:
        print(f"[pyshield-license-admin] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
