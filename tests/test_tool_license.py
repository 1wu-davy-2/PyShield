from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from pyshield import activation, crypto, fingerprint, license_admin, secure_store, tool_license


class ToolLicenseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.key_path, self.public_path, self.public_key = license_admin.generate_root(
            self.root / "keys"
        )
        self.root_key = license_admin.load_root_key(self.key_path)
        self.patch = mock.patch.dict(os.environ, {
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
        })
        self.patch.start()
        os.environ.pop(tool_license.ENV_DEV, None)
        os.environ.pop(tool_license.ENV_LICENSE, None)

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def issue(self, **kwargs) -> Path:
        options = {
            "license_id": "PS-TEST-001",
            "customer": "ACME",
            "edition": tool_license.EDITION_PRO,
            "max_builds_per_month": 100,
        }
        options.update(kwargs)
        document = license_admin.issue_entitlement(self.root_key, **options)
        return license_admin.write_document(document, self.root / "incoming-license.json")

    def install_fixed(self, **kwargs) -> Path:
        options = {
            "bind_mode": tool_license.BIND_FIXED,
            "fingerprints": ["machine-a"],
            "activation_required": False,
        }
        options.update(kwargs)
        path = self.issue(**options)
        activation.install_license(path, machine="machine-a")
        return path

    def test_read_only_check_does_not_create_trial_state(self) -> None:
        trial_file = tool_license.trial_path()
        status = tool_license.check(machine="machine-a", initialize_trial=False)
        self.assertEqual(status.mode, "trial")
        self.assertTrue(status.trial.get("not_started"))
        self.assertFalse(trial_file.exists())

        started = tool_license.check(machine="machine-a")
        self.assertEqual(started.mode, "trial")
        self.assertFalse(started.trial.get("not_started"))
        self.assertTrue(trial_file.exists())

    def test_unlicensed_without_public_key_or_license(self) -> None:
        with mock.patch.dict(os.environ, {tool_license.ENV_PUBLIC_KEY: "dev"}):
            status = tool_license.check()
        self.assertEqual(status.mode, "unlicensed")
        self.assertFalse(status.valid)
        self.assertEqual(status.code, "not_activated")
        self.assertFalse(status.allows(tool_license.FEATURE_OEM))

    def test_development_mode_requires_explicit_flag(self) -> None:
        with mock.patch.dict(os.environ, {
            tool_license.ENV_PUBLIC_KEY: "dev",
            tool_license.ENV_DEV: "1",
        }):
            status = tool_license.check()
        self.assertEqual(status.mode, "development")
        self.assertTrue(status.valid)

    def test_fresh_install_starts_trial(self) -> None:
        status = tool_license.check(machine="machine-a")
        self.assertEqual(status.mode, "trial")
        self.assertTrue(status.valid)
        self.assertEqual(status.trial["days_left"], tool_license.DEFAULT_TRIAL_DAYS)
        self.assertEqual(status.trial["builds_left"], tool_license.DEFAULT_TRIAL_BUILDS)

        tool_license.record_build(status)
        again = tool_license.check(machine="machine-a")
        self.assertEqual(again.trial["builds_left"], tool_license.DEFAULT_TRIAL_BUILDS - 1)

    def test_trial_blocks_premium_features(self) -> None:
        status = tool_license.check(features=[tool_license.FEATURE_NATIVE_RUNTIME], machine="machine-a")
        self.assertFalse(status.valid)
        self.assertIn("native-runtime", status.reason)

    def test_unactivated_license_is_invalid(self) -> None:
        path = self.issue()
        status = tool_license.check(machine="machine-a", license_path=path)
        self.assertFalse(status.valid)
        self.assertIn("activated", status.reason)

    def test_fixed_license_builds_and_binds(self) -> None:
        self.install_fixed()
        status = tool_license.check(machine="machine-a")
        self.assertEqual(status.mode, "licensed")
        self.assertTrue(status.valid)
        self.assertEqual(status.entitlement.customer, "ACME")

        other = tool_license.check(machine="machine-b")
        self.assertFalse(other.valid)
        self.assertIn("another machine", other.reason)

    def test_offline_activation_roundtrip(self) -> None:
        path = self.issue()
        request = activation.create_offline_request(
            path, self.root / "request.json", machine="machine-a",
        )
        response = self.root / "response.json"
        license_admin.activation_response(request, self.root_key, response)
        entitlement = activation.apply_offline_response(response, machine="machine-a")
        self.assertEqual(entitlement.bind_mode, tool_license.BIND_FIXED)
        self.assertEqual(entitlement.fingerprints, ("machine-a",))

        status = tool_license.check(machine="machine-a")
        self.assertTrue(status.valid)
        self.assertEqual(status.entitlement.license_id, "PS-TEST-001")

    def test_monthly_quota_is_enforced(self) -> None:
        self.install_fixed(max_builds_per_month=1)
        status = tool_license.check(machine="machine-a")
        self.assertTrue(status.valid)
        self.assertEqual(status.quota["limit"], 1)
        tool_license.record_build(status, today=date.today())

        exhausted = tool_license.check(machine="machine-a")
        self.assertFalse(exhausted.valid)
        self.assertIn("quota exhausted", exhausted.reason)

    def test_revoked_license_is_rejected(self) -> None:
        self.install_fixed()
        revocations = self.root / "revocations.json"
        license_admin.revoke(self.root_key, "PS-TEST-001", revocations)
        document = json.loads(revocations.read_text(encoding="utf-8"))
        tool_license.install_revocations(document)

        status = tool_license.check(machine="machine-a")
        self.assertFalse(status.valid)
        self.assertIn("revoked", status.reason)

    def test_tampered_license_fails_signature(self) -> None:
        path = self.issue()
        document = json.loads(path.read_text(encoding="utf-8"))
        document["payload"]["customer"] = "ATTACKER"
        tampered = self.root / "tampered.json"
        tampered.write_text(json.dumps(document), encoding="utf-8")
        status = tool_license.check(machine="machine-a", license_path=tampered)
        self.assertFalse(status.valid)
        self.assertIn("signature", status.reason)

    def test_status_dict_shape(self) -> None:
        self.install_fixed()
        data = tool_license.status_dict(machine="machine-a")
        self.assertEqual(data["mode"], "licensed")
        self.assertEqual(data["license_id"], "PS-TEST-001")
        self.assertEqual(data["features"], license_admin.EDITION_FEATURES["pro"])
        self.assertIn("quota", data)


if __name__ == "__main__":
    unittest.main()


class TrialEditionTests(unittest.TestCase):
    """Trial entitlements must be stricter than Pro and survive a missing key."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.key_path, _, self.public_key = license_admin.generate_root(self.root / "keys")
        self.root_key = license_admin.load_root_key(self.key_path)
        self.patch = mock.patch.dict(os.environ, {
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
        })
        self.patch.start()
        os.environ.pop(tool_license.ENV_DEV, None)
        os.environ.pop(tool_license.ENV_LICENSE, None)

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def install_trial(self, days: int = 14, builds: int = 3) -> tool_license.Entitlement:
        machine = fingerprint.machine_fingerprint()
        document = license_admin.issue_entitlement(
            self.root_key,
            license_id="TRIAL-TEST-001",
            edition=tool_license.EDITION_TRIAL,
            features=[tool_license.FEATURE_GUI],
            max_builds_per_month=builds,
            bind_mode=tool_license.BIND_FIXED,
            fingerprints=[machine],
            activation_required=False,
            expires=date.today() + timedelta(days=days),
            grace_days=0,
        )
        secure_store.write_private(
            tool_license.tool_license_path(),
            json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        return tool_license.load_entitlement()

    def test_trial_policy_is_narrower_than_pro(self) -> None:
        policy = tool_license.trial_policy()
        self.assertEqual(policy["ciphers"], ["chacha-pure"])
        self.assertNotIn("full", policy["integrity"])

    def test_trial_build_problems(self) -> None:
        self.assertEqual(tool_license.trial_build_problems("chacha-pure", "protected"), [])
        self.assertEqual(tool_license.trial_build_problems("chacha-pure", "off"), [])
        self.assertTrue(tool_license.trial_build_problems("aesgcm", "protected"))
        self.assertTrue(tool_license.trial_build_problems("chacha-pure", "full"))
        self.assertTrue(tool_license.trial_build_problems("chacha-pure", "protected", "ACME"))

    def test_trial_status_reports_code_and_watermark(self) -> None:
        self.install_trial()
        status = tool_license.check()
        self.assertTrue(status.valid)
        self.assertEqual(status.code, "trial")
        self.assertEqual(status.quota["limit"], 3)
        self.assertTrue(status.watermark()["trial"])
        self.assertEqual(status.watermark()["edition"], "trial")

    def test_quota_exhaustion_returns_specific_code(self) -> None:
        self.install_trial(builds=1)
        status = tool_license.check()
        tool_license.record_build(status)
        exhausted = tool_license.check()
        self.assertFalse(exhausted.valid)
        self.assertEqual(exhausted.code, "quota_exhausted")
        self.assertEqual(exhausted.entitlement.edition, tool_license.EDITION_TRIAL)
        self.assertIn("purchase", exhausted.reason)

    def test_expired_trial_returns_specific_code(self) -> None:
        self.install_trial(days=-1)
        expired = tool_license.check()
        self.assertFalse(expired.valid)
        self.assertEqual(expired.code, "trial_expired")

    def test_local_license_is_used_without_public_key(self) -> None:
        self.install_trial()
        with mock.patch.dict(os.environ, {tool_license.ENV_PUBLIC_KEY: "off"}):
            status = tool_license.check()
        self.assertTrue(status.valid)
        self.assertEqual(status.mode, "licensed")
        self.assertEqual(status.entitlement.edition, tool_license.EDITION_TRIAL)
        self.assertIn("not verified", status.reason)

    def test_without_any_license_is_unlicensed(self) -> None:
        with mock.patch.dict(os.environ, {tool_license.ENV_PUBLIC_KEY: "off"}):
            status = tool_license.check()
        self.assertEqual(status.mode, "unlicensed")
        self.assertFalse(status.valid)
        self.assertEqual(status.code, "not_activated")
