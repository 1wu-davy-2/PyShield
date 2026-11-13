"""Reference activation server for PyShield tool licenses.

This is a small, dependency-free service for development and on-premise
deployments. Put it behind a TLS reverse proxy and keep the root private key on
the server or in an HSM for production use.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import activation, ed25519, license_admin, tool_license

MAX_BODY = 1024 * 1024
HEARTBEAT_INTERVAL = timedelta(days=7)


class LicenseServerError(Exception):
    """Raised for controlled API errors."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LicenseStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.activations_path = self.data_dir / "activations.json"
        self.revocations_path = self.data_dir / "revocations.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self.activations_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise LicenseServerError(f"cannot read activation store: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        temp = self.activations_path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, self.activations_path)

    def register(self, entitlement: tool_license.Entitlement, machine: str) -> None:
        data = self._load()
        entry = data.setdefault(entitlement.license_id, {"machines": [], "last_seen": None})
        machines = [str(item) for item in entry.get("machines", [])]
        if machine not in machines:
            if len(machines) >= entitlement.max_activations:
                raise LicenseServerError(
                    f"activation limit reached ({len(machines)}/{entitlement.max_activations})"
                )
            machines.append(machine)
        entry["machines"] = machines
        entry["last_seen"] = _now()
        entry["customer"] = entitlement.customer
        data[entitlement.license_id] = entry
        self._save(data)

    def machines(self, license_id: str) -> list[str]:
        entry = self._load().get(license_id) or {}
        return [str(item) for item in entry.get("machines", [])]

    def touch(self, license_id: str, machine: str) -> None:
        data = self._load()
        entry = data.setdefault(license_id, {"machines": [], "last_seen": None})
        machines = [str(item) for item in entry.get("machines", [])]
        if machine and machine not in machines:
            machines.append(machine)
        entry["machines"] = machines
        entry["last_seen"] = _now()
        data[license_id] = entry
        self._save(data)


class LicenseHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        root_key: bytes,
        data_dir: str | Path,
        admin_token: str = "",
    ) -> None:
        self.root_key = root_key
        self.public_key = ed25519.public_key(root_key)
        self.store = LicenseStore(data_dir)
        self.admin_token = admin_token
        super().__init__(address, LicenseRequestHandler)


class LicenseRequestHandler(BaseHTTPRequestHandler):
    server: LicenseHTTPServer
    server_version = "PyShieldLicenseServer/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[license-server] {self.address_string()} {fmt % args}\n")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/v1/health":
                self._json(HTTPStatus.OK, {"ok": True, "service": "pyshield-license", "time": _now()})
            elif path == "/v1/revocations":
                self._json(HTTPStatus.OK, self._revocations_document())
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except LicenseServerError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the reference server alive
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/v1/activate":
                self._json(HTTPStatus.OK, self._activate(payload))
            elif path == "/v1/heartbeat":
                self._json(HTTPStatus.OK, self._heartbeat(payload))
            elif path == "/v1/issue":
                self._require_admin()
                self._json(HTTPStatus.OK, self._issue(payload))
            elif path == "/v1/revoke":
                self._require_admin()
                self._json(HTTPStatus.OK, self._revoke(payload))
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except LicenseServerError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except tool_license.ToolLicenseError as exc:
            self._error(HTTPStatus.FORBIDDEN, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _license_from_request(self, payload: dict) -> tuple[tool_license.Entitlement, str]:
        document = payload.get("license")
        machine = str(payload.get("machine") or "").strip().lower()
        if not isinstance(document, dict):
            raise LicenseServerError("request is missing the signed license")
        if not machine:
            raise LicenseServerError("request is missing the machine fingerprint")
        signed = tool_license.verify_document(
            document, self.server.public_key, tool_license.TOOL_LICENSE_FORMAT,
        )
        entitlement = tool_license.parse_entitlement(signed)
        if entitlement.license_id in self._revoked_ids():
            raise LicenseServerError("license has been revoked")
        return entitlement, machine

    def _activate(self, payload: dict) -> dict:
        entitlement, machine = self._license_from_request(payload)
        active, reason = entitlement.active_on(date.today())
        if not active:
            raise LicenseServerError(reason)
        if entitlement.bind_mode == tool_license.BIND_FIXED and not entitlement.machine_allowed(machine):
            raise LicenseServerError("license is bound to a different machine")
        self.server.store.register(entitlement, machine)
        response = dict(entitlement.raw)
        response.update({
            "bind_mode": tool_license.BIND_FIXED,
            "fingerprints": [machine],
            "activation_required": False,
            "activated": machine,
            "activated_at": _now(),
        })
        response.pop("signature", None)
        return tool_license.sign_document(tool_license.TOOL_LICENSE_FORMAT, response, self.server.root_key)

    def _heartbeat(self, payload: dict) -> dict:
        entitlement, machine = self._license_from_request(payload)
        if entitlement.bind_mode == tool_license.BIND_FIXED:
            if not entitlement.machine_allowed(machine):
                raise LicenseServerError("license is bound to a different machine")
            if machine not in self.server.store.machines(entitlement.license_id):
                raise LicenseServerError("machine is not activated for this license")
        active, reason = entitlement.active_on(date.today())
        status = "active" if active else "expired"
        message = "ok" if active else reason
        self.server.store.touch(entitlement.license_id, machine)
        response = {
            "license_id": entitlement.license_id,
            "machine": machine,
            "status": status,
            "message": message,
            "checked": _now(),
            "next_check": (datetime.now(timezone.utc) + HEARTBEAT_INTERVAL).isoformat(timespec="seconds"),
            "used_builds": int(payload.get("used_builds") or 0),
            "expires": entitlement.expires.isoformat() if entitlement.expires else None,
        }
        return tool_license.sign_document(
            activation.HEARTBEAT_RESPONSE_FORMAT,
            response,
            self.server.root_key,
            tool_license.HEARTBEAT_SIGNATURE_PREFIX,
        )

    def _revoked_ids(self) -> set[str]:
        document = self._revocations_document()
        signed = tool_license.verify_document(
            document, self.server.public_key, tool_license.REVOCATION_FORMAT,
            tool_license.REVOCATION_SIGNATURE_PREFIX,
        )
        return {str(item) for item in signed.get("revoked") or []}

    def _revocations_document(self) -> dict:
        path = self.server.store.revocations_path
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            document = tool_license.sign_document(
                tool_license.REVOCATION_FORMAT,
                {"issued": _now(), "epoch": 0, "revoked": [], "reason": ""},
                self.server.root_key,
                tool_license.REVOCATION_SIGNATURE_PREFIX,
            )
        except (OSError, ValueError) as exc:
            raise LicenseServerError(f"cannot read revocation list: {exc}") from exc
        tool_license.verify_document(
            document, self.server.public_key, tool_license.REVOCATION_FORMAT,
            tool_license.REVOCATION_SIGNATURE_PREFIX,
        )
        return document

    def _issue(self, payload: dict) -> dict:
        license_id = str(payload.get("license_id") or "").strip()
        if not license_id:
            raise LicenseServerError("license_id is required")
        edition = str(payload.get("edition") or tool_license.EDITION_PRO)
        features = payload.get("features")
        if features is not None and not isinstance(features, list):
            raise LicenseServerError("features must be a list")
        return license_admin.issue_entitlement(
            self.server.root_key,
            license_id=license_id,
            customer=str(payload.get("customer") or ""),
            edition=edition,
            features=features,
            seats=int(payload.get("seats") or 1),
            max_activations=int(payload.get("max_activations") or 1),
            max_builds_per_month=int(payload.get("max_builds_per_month") or 0),
            bind_mode=str(payload.get("bind_mode") or tool_license.BIND_ANY),
            fingerprints=list(payload.get("fingerprints") or []),
            activation_required=payload.get("activation_required"),
            expires=payload.get("expires"),
            grace_days=int(payload.get("grace_days") or 0),
            notes=str(payload.get("notes") or ""),
        )

    def _revoke(self, payload: dict) -> dict:
        license_id = str(payload.get("license_id") or "").strip()
        if not license_id:
            raise LicenseServerError("license_id is required")
        return license_admin.revoke(
            self.server.root_key,
            license_id,
            self.server.store.revocations_path,
            reason=str(payload.get("reason") or ""),
            public_key=self.server.public_key,
        )

    def _require_admin(self) -> None:
        expected = self.server.admin_token
        if not expected:
            raise LicenseServerError("admin API is disabled (no admin token configured)")
        header = self.headers.get("Authorization", "")
        actual = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(actual, expected):
            raise LicenseServerError("invalid admin token")

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise LicenseServerError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY:
            raise LicenseServerError("request body is empty or too large")
        try:
            document = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LicenseServerError("request body is not valid JSON") from exc
        if not isinstance(document, dict):
            raise LicenseServerError("request body must be a JSON object")
        return document

    def _json(self, status: HTTPStatus, document: dict) -> None:
        body = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": str(message)})


def create_server(
    host: str,
    port: int,
    root_key: bytes,
    data_dir: str | Path,
    admin_token: str = "",
) -> LicenseHTTPServer:
    return LicenseHTTPServer((host, port), root_key, data_dir, admin_token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pyshield-license-server", description="PyShield activation server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--root-key", required=True, help="offline root.key")
    parser.add_argument("--data-dir", default="license-data")
    parser.add_argument("--admin-token", default=os.environ.get("PYSHELD_LICENSE_ADMIN_TOKEN", ""))
    args = parser.parse_args(argv)
    try:
        root_key = license_admin.load_root_key(args.root_key)
        server = create_server(args.host, args.port, root_key, args.data_dir, args.admin_token)
    except (license_admin.LicenseAdminError, OSError) as exc:
        print(f"[pyshield-license-server] ERROR: {exc}", file=sys.stderr)
        return 1
    host, port = server.server_address[:2]
    print(f"PyShield license server listening on http://{host}:{port}", flush=True)
    if not args.admin_token:
        print("admin API disabled: pass --admin-token to enable /v1/issue and /v1/revoke", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
