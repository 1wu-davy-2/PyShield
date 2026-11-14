from __future__ import annotations

import json
import unittest
from datetime import date, timedelta

from pyshield import crypto, ed25519, licensing


class LicensingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.master = crypto.random_bytes(32)
        self.build_id = "0123456789abcdef"
        self.build_key = licensing.build_key_for(self.master, self.build_id)
        self.signing = crypto.random_bytes(32)
        self.public = ed25519.public_key(self.signing)

    def make(self, **kwargs) -> bytes:
        return licensing.create_license(
            self.master, self.build_id, "chacha", "3.12", self.signing, **kwargs
        )

    def test_round_trip(self) -> None:
        expires = date.today() + timedelta(days=30)
        sealed = self.make(expires=expires, fingerprints=["ABC123"], customer="ACME")
        payload = licensing.open_license(self.master, self.build_id, sealed, self.public)
        self.assertEqual(payload["build_id"], self.build_id)
        self.assertEqual(payload["expires"], expires.isoformat())
        self.assertEqual(payload["bind_mode"], licensing.BIND_FIXED)
        self.assertEqual(payload["fingerprints"], ["abc123"])
        self.assertEqual(payload["customer"], "ACME")
        self.assertNotIn("build_key", payload)
        self.assertEqual(
            licensing.build_key_for(self.master, self.build_id), self.build_key
        )
        self.assertTrue(licensing.verify_payload(payload, self.public))

    def test_wrong_master_key(self) -> None:
        sealed = self.make()
        with self.assertRaises(licensing.LicenseError):
            licensing.open_license(crypto.random_bytes(32), self.build_id, sealed, self.public)

    def test_wrong_build(self) -> None:
        sealed = self.make()
        with self.assertRaises(licensing.LicenseError):
            licensing.open_license(self.master, "fedcba9876543210", sealed, self.public)

    def test_wrong_public_key(self) -> None:
        sealed = self.make()
        with self.assertRaisesRegex(licensing.LicenseError, "signature"):
            licensing.open_license(
                self.master, self.build_id, sealed, ed25519.public_key(crypto.random_bytes(32))
            )

    def test_forged_payload_rejected(self) -> None:
        sealed = self.make(expires=date.today() + timedelta(days=1))
        message_key = licensing.license_key(self.master, self.build_id)
        data = crypto.open_envelope(message_key, sealed, licensing.license_aad(self.build_id))
        payload = json.loads(data.decode("utf-8"))
        payload["expires"] = None
        forged = crypto.seal(
            message_key,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            licensing.license_aad(self.build_id),
            cipher="chacha",
        )
        with self.assertRaisesRegex(licensing.LicenseError, "signature"):
            licensing.open_license(self.master, self.build_id, forged, self.public)

    def test_legacy_license_rejected(self) -> None:
        payload = licensing.build_payload(
            self.build_id, "chacha", "3.12", None, [], None
        )
        payload["format"] = 1
        message_key = licensing.license_key(self.master, self.build_id)
        sealed = crypto.seal(
            message_key,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            licensing.license_aad(self.build_id),
            cipher="chacha",
        )
        with self.assertRaisesRegex(licensing.LicenseError, "unsupported license format"):
            licensing.open_license(self.master, self.build_id, sealed, self.public)

    def test_legacy_format_two_license_still_opens(self) -> None:
        sealed = licensing.create_license(
            self.master, self.build_id, "chacha", "3.12", self.signing,
            license_format=licensing.LEGACY_FORMAT, build_key=self.build_key,
        )
        payload = licensing.open_license(self.master, self.build_id, sealed, self.public)
        self.assertEqual(payload["format"], licensing.LEGACY_FORMAT)
        self.assertEqual(crypto.b85decode(payload["build_key"]), self.build_key)

    def test_parse_expire(self) -> None:
        today = date(2026, 1, 1)
        self.assertEqual(licensing.parse_expire("30", today), date(2026, 1, 31))
        self.assertEqual(licensing.parse_expire("2027-06-30", today), date(2027, 6, 30))
        self.assertIsNone(licensing.parse_expire(None, today))
        with self.assertRaises(licensing.LicenseError):
            licensing.parse_expire("soon", today)

    def test_parse_grace_days(self) -> None:
        self.assertEqual(licensing.parse_grace_days(None), 0)
        self.assertEqual(licensing.parse_grace_days("30"), 30)
        with self.assertRaises(licensing.LicenseError):
            licensing.parse_grace_days(-1)
        with self.assertRaises(licensing.LicenseError):
            licensing.parse_grace_days(licensing.MAX_GRACE_DAYS + 1)

    def test_not_before_is_reported(self) -> None:
        today = date(2026, 5, 1)
        sealed = self.make(not_before=today + timedelta(days=3))
        payload = licensing.open_license(self.master, self.build_id, sealed, self.public)
        self.assertEqual(payload["not_before"], "2026-05-04")
        problems = licensing.validate_payload(payload, today=today)
        self.assertTrue(any("issue date" in item for item in problems))
        self.assertEqual(
            licensing.validate_payload(payload, today=today + timedelta(days=3)), []
        )

    def test_grace_period(self) -> None:
        today = date(2026, 5, 10)
        sealed = self.make(
            expires=today - timedelta(days=2), grace_days=7, not_before=today
        )
        payload = licensing.open_license(self.master, self.build_id, sealed, self.public)
        self.assertEqual(payload["grace_days"], 7)
        self.assertEqual(licensing.validate_payload(payload, today=today), [])
        self.assertEqual(
            licensing.validate_payload(payload, today=today + timedelta(days=5)), []
        )
        problems = licensing.validate_payload(payload, today=today + timedelta(days=6))
        self.assertTrue(any("grace period" in item for item in problems))

    def test_validate_fingerprints(self) -> None:
        sealed = self.make(fingerprints=["ABC"])
        payload = licensing.open_license(self.master, self.build_id, sealed, self.public)
        self.assertEqual(licensing.validate_payload(payload, machine_fingerprint="abc"), [])
        self.assertTrue(licensing.validate_payload(payload, machine_fingerprint="zzz"))

    def test_fingerprint_normalization(self) -> None:
        values = licensing.normalize_fingerprints([" ABC ", "abc", "", "DEF"])
        self.assertEqual(values, ["abc", "def"])

    def test_first_run_binding(self) -> None:
        sealed = self.make(bind_mode=licensing.BIND_FIRST_RUN, fingerprints=["abc"])
        payload = licensing.open_license(self.master, self.build_id, sealed, self.public)
        self.assertEqual(payload["bind_mode"], licensing.BIND_FIRST_RUN)
        self.assertEqual(payload["fingerprints"], [])
        self.assertEqual(licensing.validate_payload(payload, machine_fingerprint="zzz"), [])
        self.assertEqual(licensing.bind_mode_of(payload), licensing.BIND_FIRST_RUN)

    def test_unbound_license(self) -> None:
        sealed = self.make(bind_mode=licensing.BIND_ANY)
        payload = licensing.open_license(self.master, self.build_id, sealed, self.public)
        self.assertEqual(payload["bind_mode"], licensing.BIND_ANY)
        self.assertEqual(licensing.validate_payload(payload, machine_fingerprint="zzz"), [])

    def test_fixed_binding_requires_fingerprint(self) -> None:
        with self.assertRaises(licensing.LicenseError):
            self.make(bind_mode=licensing.BIND_FIXED)

    def test_legacy_license_is_fixed(self) -> None:
        sealed = self.make(fingerprints=["abc"])
        payload = licensing.open_license(self.master, self.build_id, sealed, self.public)
        payload.pop("bind_mode", None)
        self.assertEqual(licensing.bind_mode_of(payload), licensing.BIND_FIXED)
        self.assertTrue(licensing.validate_payload(payload, machine_fingerprint="zzz"))

    def test_secret_round_trip(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "build.shield-secret.json"
            licensing.write_secret(path, {
                "format": licensing.FORMAT,
                "build_id": self.build_id,
                "master_key": crypto.b85encode(self.master),
                "build_key": crypto.b85encode(self.build_key),
                "signing_key": crypto.b85encode(self.signing),
                "signing_public_key": crypto.b85encode(self.public),
            })
            secret = licensing.read_secret(path)
            self.assertEqual(secret["build_id"], self.build_id)
            self.assertEqual(crypto.b85decode(secret["master_key"]), self.master)
            self.assertEqual(licensing.secret_public_key(secret), self.public)

    def test_invalid_secret(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.shield-secret.json"
            path.write_text('{"format": 1}', encoding="utf-8")
            with self.assertRaises(licensing.LicenseError):
                licensing.read_secret(path)


if __name__ == "__main__":
    unittest.main()
