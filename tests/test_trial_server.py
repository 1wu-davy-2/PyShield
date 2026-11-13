from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from pyshield import crypto, license_admin, tool_license, trial_license, trial_server


class TrialServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.key_path, _, self.public_key = license_admin.generate_root(self.root / "keys")
        self.root_key = license_admin.load_root_key(self.key_path)
        self.server = trial_server.create_server(
            "127.0.0.1", 0, self.root_key, self.root / "trial-data",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.url = f"http://{host}:{port}"
        self.patch = mock.patch.dict(os.environ, {
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
        })
        self.patch.start()
        os.environ.pop(tool_license.ENV_DEV, None)
        os.environ.pop(tool_license.ENV_LICENSE, None)
        self.state = trial_license.install_state()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.server.store.close()
        self.patch.stop()
        self.temp.cleanup()

    def form_post(self, path: str, fields: dict) -> tuple[int, str]:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(
            self.url + path, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def json_get(self, path: str) -> dict:
        with urllib.request.urlopen(self.url + path, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def apply_trial(self) -> tuple[int, str]:
        return self.form_post("/trial/apply", {
            "serial": self.state["serial"],
            "machine": self.state["machine"],
            "edition": "trial",
        })

    def test_apply_returns_deep_link_and_claim_installs_license(self) -> None:
        status, body = self.apply_trial()
        self.assertEqual(status, 200)
        self.assertIn("试用已开通", body)
        self.assertIn(f"pyshield://activate?serial={self.state['serial']}", body)

        remote = self.json_get(f"/api/trial/status?serial={self.state['serial']}")
        self.assertEqual(remote["status"], "granted")
        self.assertEqual(remote["max_builds"], 3)

        document = trial_license.claim(self.url)
        entitlement = trial_license.apply_document(document, public_key=self.public_key)
        self.assertEqual(entitlement.edition, tool_license.EDITION_TRIAL)
        self.assertEqual(entitlement.max_builds_per_month, 3)
        self.assertEqual(entitlement.expires, date.today() + timedelta(days=14))

        status = tool_license.check()
        self.assertTrue(status.valid)
        self.assertEqual(status.quota["limit"], 3)
        self.assertFalse(status.allows(tool_license.FEATURE_NATIVE_RUNTIME))
        self.assertFalse(status.allows(tool_license.FEATURE_LICENSE_ISSUANCE))

    def test_repeat_apply_returns_same_signed_document(self) -> None:
        self.apply_trial()
        first = trial_license.claim(self.url)
        self.apply_trial()
        second = trial_license.claim(self.url)
        self.assertEqual(first, second)
        self.assertEqual(first["payload"]["license_id"], f"TRIAL-{self.state['serial']}")

    def test_claim_rejects_another_machine(self) -> None:
        self.apply_trial()
        with self.assertRaises(trial_license.TrialError):
            trial_license.claim(self.url, machine="0" * 64)

    def test_pro_edition_requires_contact(self) -> None:
        status, body = self.form_post("/trial/apply", {
            "serial": self.state["serial"],
            "machine": self.state["machine"],
            "edition": "pro",
        })
        self.assertEqual(status, 200)
        self.assertIn("需要商务确认", body)
        remote = self.json_get(f"/api/trial/status?serial={self.state['serial']}")
        self.assertEqual(remote["status"], "pending")

    def test_status_of_unknown_serial(self) -> None:
        other = trial_license.serial_from("c" * 64, "salt")
        remote = self.json_get(f"/api/trial/status?serial={other}")
        self.assertEqual(remote["status"], "unknown")

    def test_activate_page_registers_pending_request(self) -> None:
        with urllib.request.urlopen(
            f"{self.url}/activate?serial={self.state['serial']}&machine={self.state['machine']}",
            timeout=10,
        ) as response:
            body = response.read().decode("utf-8")
        self.assertIn(self.state["serial"], body)
        self.assertIn("申请 14 天免费试用", body)
        remote = self.json_get(f"/api/trial/status?serial={self.state['serial']}")
        self.assertEqual(remote["status"], "pending")

    def test_tampered_trial_document_is_rejected(self) -> None:
        self.apply_trial()
        document = trial_license.claim(self.url)
        payload = document["payload"]
        payload["expires"] = "2020-01-01"
        payload.pop("signature", None)
        with self.assertRaises(tool_license.ToolLicenseError):
            tool_license.verify_document(
                {"format": tool_license.TOOL_LICENSE_FORMAT, "payload": payload,
                 "signature": document["signature"]},
                self.public_key, tool_license.TOOL_LICENSE_FORMAT,
            )


if __name__ == "__main__":
    unittest.main()
