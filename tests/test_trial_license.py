from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from pyshield import crypto, license_admin, tool_license, trial_license

SERIAL_RE = re.compile(r"^PS-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")


class TrialLicenseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.patch = mock.patch.dict(os.environ, {tool_license.ENV_HOME: str(self.home)})
        self.patch.start()
        os.environ.pop(tool_license.ENV_DEV, None)
        os.environ.pop(tool_license.ENV_LICENSE, None)
        os.environ.pop(trial_license.ENV_TRIAL_SERVER, None)
        self.key_path, _, self.public_key = license_admin.generate_root(self.root / "keys")
        self.root_key = license_admin.load_root_key(self.key_path)

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def trial_document(self, machine: str | None = None, days: int = 14, builds: int = 3) -> dict:
        target = machine or trial_license.install_state()["machine"]
        return license_admin.issue_entitlement(
            self.root_key,
            license_id="TRIAL-PS-TEST",
            customer="试用用户",
            edition=tool_license.EDITION_TRIAL,
            features=[tool_license.FEATURE_GUI],
            max_activations=1,
            max_builds_per_month=builds,
            bind_mode=tool_license.BIND_FIXED,
            fingerprints=[target],
            activation_required=False,
            expires=date.today() + timedelta(days=days),
            grace_days=0,
        )

    def test_serial_is_stable_and_formatted(self) -> None:
        first = trial_license.install_state()
        self.assertRegex(first["serial"], SERIAL_RE)
        self.assertTrue(trial_license.install_path().is_file())

        second = trial_license.install_state()
        self.assertEqual(first["serial"], second["serial"])
        self.assertEqual(first["salt"], second["salt"])

    def test_serial_depends_on_machine_and_salt(self) -> None:
        machine = "a" * 64
        one = trial_license.serial_from(machine, "salt-one")
        another = trial_license.serial_from(machine, "salt-two")
        other_machine = trial_license.serial_from("b" * 64, "salt-one")
        self.assertRegex(one, SERIAL_RE)
        self.assertNotEqual(one, another)
        self.assertNotEqual(one, other_machine)

    def test_normalize_serial_accepts_loose_input(self) -> None:
        canonical = "PS-4RD6-H29F-CJ5T"
        self.assertEqual(trial_license.normalize_serial("ps4rd6h29fcj5t"), canonical)
        self.assertEqual(trial_license.normalize_serial("PS-4RD6-H29F-CJ5T"), canonical)
        with self.assertRaises(trial_license.TrialError):
            trial_license.normalize_serial("PS-1234")
        with self.assertRaises(trial_license.TrialError):
            trial_license.normalize_serial("PS-4RD6-H29F-CJ5U")  # U is not in the alphabet

    def test_handshake_url_carries_serial(self) -> None:
        state = trial_license.install_state()
        url = trial_license.handshake_url("https://license.example.com/", edition="pro")
        self.assertTrue(url.startswith("https://license.example.com/activate?"))
        self.assertIn(state["serial"], url)
        self.assertIn("edition=pro", url)
        self.assertIn("machine=", url)
        with self.assertRaises(trial_license.TrialError):
            trial_license.handshake_url("http://example.com")

    def test_apply_document_rejects_other_machine(self) -> None:
        document = self.trial_document(machine="f" * 64)
        with self.assertRaises(trial_license.TrialError):
            trial_license.apply_document(document, public_key=self.public_key)

    def test_apply_document_installs_trial_entitlement(self) -> None:
        document = self.trial_document()
        entitlement = trial_license.apply_document(document, public_key=self.public_key)
        self.assertEqual(entitlement.edition, tool_license.EDITION_TRIAL)
        self.assertEqual(entitlement.features, (tool_license.FEATURE_GUI,))
        self.assertEqual(entitlement.max_builds_per_month, 3)
        self.assertTrue(tool_license.tool_license_path().is_file())

        installed = json.loads(tool_license.tool_license_path().read_text(encoding="utf-8"))
        self.assertEqual(installed["payload"]["edition"], "trial")

    def test_status_reports_quota_and_days(self) -> None:
        trial_license.apply_document(self.trial_document(), public_key=self.public_key)
        with mock.patch.dict(os.environ, {
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
        }):
            state = trial_license.status("https://license.example.com")
        self.assertTrue(state["trial"])
        self.assertTrue(state["active"])
        self.assertEqual(state["edition"], tool_license.EDITION_TRIAL)
        self.assertEqual(state["builds_left"], 3)
        self.assertEqual(state["build_limit"], 3)
        self.assertEqual(state["days_left"], 14)
        self.assertEqual(state["serial"], trial_license.local_serial())

    def test_status_without_license_is_unlicensed(self) -> None:
        state = trial_license.status("https://license.example.com")
        self.assertFalse(state["trial"])
        self.assertFalse(state["active"])
        self.assertRegex(state["serial"], SERIAL_RE)
        self.assertIsNone(state["days_left"])


if __name__ == "__main__":
    unittest.main()
