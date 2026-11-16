from __future__ import annotations

import os
import shutil
import time
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from pyshield import crypto, devsite, license_admin, tool_license, trial_license


class DevSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.patch = mock.patch.dict(os.environ, {tool_license.ENV_HOME: str(self.home)})
        self.patch.start()
        os.environ.pop(tool_license.ENV_PUBLIC_KEY, None)
        os.environ.pop(tool_license.ENV_DEV, None)
        devsite.stop()

    def tearDown(self) -> None:
        devsite.stop()
        self.patch.stop()
        try:
            self.temp.cleanup()
        except OSError:
            time.sleep(0.3)
            shutil.rmtree(self.temp.name, ignore_errors=True)

    def test_enabled_without_root_key(self) -> None:
        self.assertTrue(devsite.enabled())

    def test_disabled_when_root_key_is_injected(self) -> None:
        _, _, public = license_admin.generate_root(self.root / "keys")
        with mock.patch.dict(os.environ, {tool_license.ENV_PUBLIC_KEY: crypto.b85encode(public)}):
            self.assertFalse(devsite.enabled())

    def test_ensure_is_idempotent(self) -> None:
        first = devsite.ensure(port=0)
        second = devsite.ensure(port=0)
        self.assertEqual(first, second)
        self.assertTrue(devsite.running())
        self.assertEqual(first, devsite.url())

    def test_registration_flow_against_local_site(self) -> None:
        url = devsite.ensure(port=0)
        with urllib.request.urlopen(f"{url}/health", timeout=10) as response:
            self.assertIn(b'"ok": true', response.read())

        state = trial_license.install_state()
        payload = urllib.parse.urlencode({
            "serial": state["serial"],
            "machine": state["machine"],
            "edition": "trial",
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{url}/trial/apply", data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertIn("试用已开通", response.read().decode("utf-8"))

        public_key = devsite.public_key()
        self.assertIsNotNone(public_key)
        document = trial_license.claim(url)
        entitlement = trial_license.apply_document(document, public_key=public_key)
        self.assertEqual(entitlement.edition, tool_license.EDITION_TRIAL)
        self.assertEqual(entitlement.max_builds_per_month, 3)

        status = tool_license.check()
        self.assertTrue(status.valid)
        self.assertEqual(status.code, "trial")
        self.assertEqual(status.quota["limit"], 3)
        self.assertFalse(status.allows(tool_license.FEATURE_NATIVE_RUNTIME))


if __name__ == "__main__":
    unittest.main()
