from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from pyshield import __version__, crypto, license_admin, tool_license, trial_license

MACHINE = "a" * 64
LICENSE_ID = "PRO-PS-TEST"
ACTIVATION_KEY = "PSP-AAAA-BBBB-CCCC-DDDD"


class _Portal:
    """Minimal in-memory license portal used by the fake HTTP server."""

    def __init__(self, root_key: bytes, machine: str) -> None:
        self.root_key = root_key
        self.machine = machine
        self.serial = ""
        self.revision = 1
        self.fail_activate = False
        self.requests: list[tuple[str, dict]] = []

    def document(self, revision: int) -> dict:
        document = license_admin.issue_entitlement(
            self.root_key,
            license_id=LICENSE_ID,
            customer="测试客户",
            edition=tool_license.EDITION_PRO,
            features=[
                tool_license.FEATURE_GUI,
                tool_license.FEATURE_NATIVE_RUNTIME,
            ],
            max_builds_per_month=100,
            bind_mode=tool_license.BIND_FIXED,
            fingerprints=[self.machine],
            activation_required=False,
            expires=date.today() + timedelta(days=365),
            grace_days=14,
        )
        payload = dict(document["payload"])
        payload["revision"] = int(revision)
        return tool_license.sign_document(
            tool_license.TOOL_LICENSE_FORMAT, payload, self.root_key,
        )


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        portal: _Portal = self.server.portal  # type: ignore[attr-defined]
        portal.requests.append((self.path, payload))
        if self.path == "/api/license/activate":
            if portal.fail_activate:
                self._json(400, {"ok": False, "error": "激活码无效", "code": "invalid_key"})
                return
            if payload.get("serial") != portal.serial or payload.get("machine") != portal.machine:
                self._json(409, {"ok": False, "error": "serial/machine mismatch"})
                return
            self._json(200, {
                "ok": True,
                "status": "granted",
                "serial": portal.serial,
                "machine": portal.machine,
                "license_id": LICENSE_ID,
                "edition": tool_license.EDITION_PRO,
                "revision": portal.revision,
                "document": portal.document(portal.revision),
            })
            return
        if self.path == "/api/license/status":
            if payload.get("serial") != portal.serial:
                self._json(404, {"ok": False, "error": "not found"})
                return
            result = {
                "ok": True,
                "status": "active",
                "serial": portal.serial,
                "license_id": LICENSE_ID,
                "edition": tool_license.EDITION_PRO,
                "revision": portal.revision,
            }
            try:
                revision = int(payload.get("revision") or 0)
            except (TypeError, ValueError):
                revision = 0
            if (
                str(payload.get("license_id") or "") != LICENSE_ID
                or revision < portal.revision
            ):
                result["document"] = portal.document(portal.revision)
            self._json(200, result)
            return
        self._json(404, {"ok": False, "error": "unknown endpoint"})


class LicensePortalClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.key_path, _, self.public_key = license_admin.generate_root(self.root / "keys")
        self.root_key = license_admin.load_root_key(self.key_path)

        self.env = mock.patch.dict(os.environ, {
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
        })
        self.env.start()
        os.environ.pop(tool_license.ENV_LICENSE, None)
        os.environ.pop(tool_license.ENV_DEV, None)
        os.environ.pop(trial_license.ENV_TRIAL_SERVER, None)
        self.machine = mock.patch.object(trial_license, "_machine", return_value=MACHINE)
        self.machine.start()

        self.portal = _Portal(self.root_key, MACHINE)
        self.portal.serial = trial_license.install_state()["serial"]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.portal = self.portal  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.machine.stop()
        self.env.stop()
        self.temp.cleanup()

    def installed_document(self) -> dict:
        return json.loads(tool_license.tool_license_path().read_text(encoding="utf-8"))

    def test_activate_key_installs_pro_license(self) -> None:
        entitlement = trial_license.activate_key(self.base, ACTIVATION_KEY)

        self.assertEqual(entitlement.edition, tool_license.EDITION_PRO)
        self.assertEqual(entitlement.license_id, LICENSE_ID)
        self.assertTrue(entitlement.machine_allowed(MACHINE))

        path, payload = self.portal.requests[-1]
        self.assertEqual(path, "/api/license/activate")
        self.assertEqual(payload["key"], ACTIVATION_KEY)
        self.assertEqual(payload["serial"], self.portal.serial)
        self.assertEqual(payload["machine"], MACHINE)
        self.assertEqual(payload["version"], __version__)

        installed = self.installed_document()
        self.assertEqual(installed["format"], tool_license.TOOL_LICENSE_FORMAT)
        self.assertEqual(installed["payload"]["edition"], tool_license.EDITION_PRO)
        self.assertEqual(installed["payload"]["fingerprints"], [MACHINE])
        self.assertEqual(installed["payload"]["revision"], 1)

    def test_refresh_license_installs_newer_document(self) -> None:
        trial_license.activate_key(self.base, ACTIVATION_KEY)
        self.portal.revision = 2

        result = trial_license.refresh_license(self.base)

        self.assertTrue(result["ok"])
        self.assertTrue(result["updated"])
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["document"]["payload"]["revision"], 2)

        path, payload = self.portal.requests[-1]
        self.assertEqual(path, "/api/license/status")
        self.assertEqual(payload["serial"], self.portal.serial)
        self.assertEqual(payload["machine"], MACHINE)
        self.assertEqual(payload["license_id"], LICENSE_ID)
        self.assertEqual(payload["revision"], 1)

        installed = self.installed_document()
        self.assertEqual(installed["payload"]["revision"], 2)
        self.assertEqual(installed["payload"]["expires"],
                         self.portal.document(2)["payload"]["expires"])

    def test_refresh_license_reports_current_revision_without_document(self) -> None:
        trial_license.activate_key(self.base, ACTIVATION_KEY)

        result = trial_license.refresh_license(self.base)

        self.assertTrue(result["ok"])
        self.assertFalse(result["updated"])
        self.assertEqual(result["status"], "active")
        self.assertNotIn("document", result)
        self.assertEqual(self.installed_document()["payload"]["revision"], 1)

    def test_activate_key_http_error_raises_trial_error(self) -> None:
        self.portal.fail_activate = True

        with self.assertRaises(trial_license.TrialError):
            trial_license.activate_key(self.base, ACTIVATION_KEY)


if __name__ == "__main__":
    unittest.main()
