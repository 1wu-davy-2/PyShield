"""Tests for license_portal.service (business logic only, no HTTP)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # .../PyShield
PORTAL = Path(__file__).resolve().parents[1]  # .../PyShield/license-portal
for _path in (ROOT, PORTAL):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from pyshield import tool_license

from license_portal import validation
from license_portal.captcha import NullCaptcha
from license_portal.config import Config
from license_portal.mailer import MemoryMailer
from license_portal.service import PortalService, ServiceError, create_service
from license_portal.signing import SigningKey

SECRET = b"x" * 32
SIGNING_PASSPHRASE = "test-signing-passphrase"
TEST_IP = "203.0.113.9"

MACHINE_A = "0123456789abcdef0123456789abcdef"
MACHINE_B = "ffffffffffffffffffffffffffffffff"
MACHINE_C = "11111111111111111111111111111111"

SERIAL_TRIAL = "PS-ABCD-EFGH-JKMN"
SERIAL_SECOND = "PS-GGGG-HHHH-JJJJ"
SERIAL_THIRD = "PS-MMMM-NNNN-PPPP"
SERIAL_FOURTH = "PS-QQQQ-RRRR-SSSS"
SERIAL_KEY = "PS-XXXX-YYYY-ZZZZ"
SERIAL_UNKNOWN = "PS-ZZZZ-9999-0000"

EMAIL = "trial@example.com"
PHONE = "13800138000"


def trial_payload(**overrides) -> dict:
    payload = {
        "serial": SERIAL_TRIAL,
        "machine": MACHINE_A,
        "email": EMAIL,
        "phone": PHONE,
        "name": "张三",
        "org_type": "individual",
        "consent": "on",
    }
    payload.update(overrides)
    return payload


def assert_service_error(testcase, status, code, func, *args, **kwargs):
    with testcase.assertRaises(ServiceError) as captured:
        func(*args, **kwargs)
    testcase.assertEqual(captured.exception.status, status, captured.exception.message)
    testcase.assertEqual(captured.exception.code, code, captured.exception.message)
    return captured.exception


class PortalServiceTestCase(unittest.TestCase):
    """Base class that gives every test an isolated database and signing key."""

    email_verification = False

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.data_dir = Path(self._tempdir.name)
        self.mailer = MemoryMailer()
        self.config = Config(
            data_dir=self.data_dir,
            email_verification=self.email_verification,
            dev=True,
            signing_passphrase=SIGNING_PASSPHRASE,
        )
        self.service: PortalService = create_service(
            self.config,
            secret=SECRET,
            mailer=self.mailer,
            captcha=NullCaptcha(),
        )
        # Cleanups run last-in-first-out, so the database is closed before the
        # temporary directory is removed (required on Windows).
        self.addCleanup(self.service.db.close)

    # -- helpers ---------------------------------------------------------

    def start_trial(self, **overrides) -> dict:
        return self.service.start_trial(trial_payload(**overrides), TEST_IP)

    @property
    def signing_key(self) -> SigningKey:
        return self.service.signing_key

    def verify_document(self, document: dict) -> dict:
        return tool_license.verify_document(
            document, self.signing_key.public_key, tool_license.TOOL_LICENSE_FORMAT,
        )

    def entitlement(self, document: dict) -> tool_license.Entitlement:
        return tool_license.parse_entitlement(self.verify_document(document))

    def issue_key(self, **overrides) -> dict:
        options = {
            "edition": "pro",
            "days": 365,
            "max_builds": 60,
            "quantity": 1,
        }
        options.update(overrides)
        return self.service.create_license_keys("admin", options)["keys"][0]

    def activate_key(self, key: str, serial: str = SERIAL_KEY,
                     machine: str = MACHINE_A) -> dict:
        return self.service.activate_key(key, serial, machine, TEST_IP)


class TrialFlowTests(PortalServiceTestCase):
    def test_start_trial_without_email_verification(self) -> None:
        result = self.start_trial()

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "granted")
        self.assertEqual(result["edition"], tool_license.EDITION_TRIAL)
        self.assertEqual(result["serial"], SERIAL_TRIAL)
        self.assertEqual(result["machine"], MACHINE_A)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["max_builds"], 3)
        self.assertTrue(result["trial_code"].startswith("PST-"))
        self.assertFalse(result["verification_required"])
        self.assertIn("pyshield://activate?", result["deep_link"])

        payload = self.verify_document(result["document"])
        entitlement = tool_license.parse_entitlement(payload)
        self.assertEqual(entitlement.edition, tool_license.EDITION_TRIAL)
        self.assertEqual(entitlement.max_builds_per_month, 3)
        self.assertEqual(entitlement.fingerprints, (MACHINE_A,))
        self.assertEqual(entitlement.bind_mode, tool_license.BIND_FIXED)
        self.assertFalse(entitlement.activation_required)
        self.assertTrue(entitlement.machine_allowed(MACHINE_A))
        self.assertEqual(list(entitlement.features), [tool_license.FEATURE_GUI])

    def test_trial_status_and_claim(self) -> None:
        started = self.start_trial()

        status = self.service.trial_status(SERIAL_TRIAL, MACHINE_A)
        self.assertEqual(status["status"], "granted")
        self.assertEqual(status["edition"], tool_license.EDITION_TRIAL)
        self.assertEqual(status["license_id"], started["license_id"])
        self.assertEqual(status["trial_code"], started["trial_code"])
        self.assertEqual(status["revision"], 1)

        claimed = self.service.claim_trial(SERIAL_TRIAL, MACHINE_A)
        self.assertEqual(claimed["serial"], SERIAL_TRIAL)
        self.assertEqual(claimed["edition"], tool_license.EDITION_TRIAL)
        self.assertEqual(claimed["license_id"], started["license_id"])
        self.assertEqual(claimed["document"], started["document"])
        self.verify_document(claimed["document"])

    def test_trial_status_rejects_wrong_machine(self) -> None:
        self.start_trial()
        assert_service_error(
            self, 403, "machine_mismatch",
            self.service.trial_status, SERIAL_TRIAL, MACHINE_B,
        )
        assert_service_error(
            self, 403, "machine_mismatch",
            self.service.claim_trial, SERIAL_TRIAL, MACHINE_B,
        )

    def test_trial_status_returns_unknown_for_unregistered_serial(self) -> None:
        status = self.service.trial_status(SERIAL_UNKNOWN, MACHINE_A)
        self.assertTrue(status["ok"])
        self.assertEqual(status["status"], "unknown")

    def test_trial_blocks_same_machine_with_another_serial(self) -> None:
        self.start_trial()
        assert_service_error(
            self, 409, "machine_trial_exists", self.start_trial,
            serial=SERIAL_SECOND,
            email="other@example.com",
            phone="13900139000",
        )

    def test_trial_blocks_repeated_email(self) -> None:
        self.start_trial()
        assert_service_error(
            self, 409, "customer_trial_exists", self.start_trial,
            serial=SERIAL_SECOND,
            machine=MACHINE_B,
            phone="13900139000",
        )

    def test_trial_blocks_repeated_phone(self) -> None:
        self.start_trial()
        assert_service_error(
            self, 409, "customer_trial_exists", self.start_trial,
            serial=SERIAL_THIRD,
            machine=MACHINE_C,
            email="other@example.com",
        )


class EmailVerificationFlowTests(PortalServiceTestCase):
    email_verification = True

    def test_start_trial_requires_email_verification(self) -> None:
        result = self.start_trial()

        self.assertTrue(result["verification_required"])
        self.assertGreater(result["application_id"], 0)
        self.assertNotIn("document", result)
        self.assertRegex(self.mailer.last_code, r"^\d{6}$")
        self.assertEqual(len(self.mailer.codes), 1)
        self.assertEqual(self.mailer.codes[0][0], EMAIL)
        self.assertEqual(self.mailer.codes[0][2], "trial")

    def test_verify_trial_wrong_code_increments_attempts(self) -> None:
        started = self.start_trial()
        application_id = started["application_id"]
        correct = self.mailer.last_code
        wrong = "000000" if correct != "000000" else "111111"

        assert_service_error(
            self, 400, "code_invalid",
            self.service.verify_trial, application_id, wrong, TEST_IP,
        )
        row = self.service.db.query_one(
            "SELECT attempts FROM applications WHERE id = ?", (application_id,),
        )
        self.assertEqual(row["attempts"], 1)

        assert_service_error(
            self, 400, "code_invalid",
            self.service.verify_trial, application_id, wrong, TEST_IP,
        )
        row = self.service.db.query_one(
            "SELECT attempts FROM applications WHERE id = ?", (application_id,),
        )
        self.assertEqual(row["attempts"], 2)

    def test_verify_trial_issues_license_and_is_idempotent(self) -> None:
        started = self.start_trial()
        application_id = started["application_id"]
        code = self.mailer.last_code

        issued = self.service.verify_trial(application_id, code, TEST_IP)
        self.assertEqual(issued["status"], "granted")
        self.assertEqual(issued["edition"], tool_license.EDITION_TRIAL)
        self.assertTrue(issued["trial_code"].startswith("PST-"))
        entitlement = self.entitlement(issued["document"])
        self.assertEqual(entitlement.edition, tool_license.EDITION_TRIAL)
        self.assertTrue(entitlement.machine_allowed(MACHINE_A))

        again = self.service.verify_trial(application_id, code, TEST_IP)
        self.assertEqual(again["status"], "granted")
        self.assertTrue(again.get("already"))
        self.assertEqual(again["document"], issued["document"])

        status = self.service.trial_status(SERIAL_TRIAL, MACHINE_A)
        self.assertEqual(status["status"], "granted")


class ActivationKeyTests(PortalServiceTestCase):
    def test_create_license_keys(self) -> None:
        result = self.service.create_license_keys(
            "admin", {"edition": "pro", "quantity": 2, "max_builds": 40},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["keys"]), 2)
        keys = [entry["key"] for entry in result["keys"]]
        self.assertEqual(len(set(keys)), 2)
        for key in keys:
            self.assertTrue(key.startswith("PSP-"))
            self.assertEqual(validation.normalize_license_key(key), key)
            self.assertEqual(len(key), 23)  # PSP- + four groups of four
        self.assertEqual(result["keys"][0]["edition"], "pro")
        self.assertEqual(result["keys"][0]["max_builds"], 40)

    def test_activate_key_and_idempotent_reactivation(self) -> None:
        key = self.issue_key(max_builds=50)["key"]

        result = self.activate_key(key)
        self.assertEqual(result["status"], "granted")
        self.assertEqual(result["edition"], "pro")
        self.assertEqual(result["license_id"], f"PRO-{SERIAL_KEY}")
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["max_builds"], 50)

        payload = self.verify_document(result["document"])
        entitlement = tool_license.parse_entitlement(payload)
        self.assertEqual(entitlement.edition, tool_license.EDITION_PRO)
        self.assertEqual(entitlement.max_builds_per_month, 50)
        self.assertEqual(entitlement.fingerprints, (MACHINE_A,))
        for feature in (
            tool_license.FEATURE_GUI,
            tool_license.FEATURE_NATIVE_RUNTIME,
            tool_license.FEATURE_LICENSE_ISSUANCE,
        ):
            self.assertIn(feature, entitlement.features)

        again = self.activate_key(key)
        self.assertEqual(again["status"], "granted")
        self.assertEqual(again["revision"], result["revision"])
        self.assertEqual(again["document"], result["document"])

    def test_activate_key_rejects_another_machine(self) -> None:
        key = self.issue_key()["key"]
        self.activate_key(key)
        assert_service_error(
            self, 409, "serial_bound",
            self.activate_key, key, SERIAL_KEY, MACHINE_B,
        )

    def test_activate_key_rejects_unknown_key(self) -> None:
        assert_service_error(
            self, 400, "invalid_key",
            self.activate_key, "PSP-0000-0000-0000-0000", SERIAL_SECOND, MACHINE_B,
        )

    def test_activate_key_rejects_revoked_key(self) -> None:
        issued = self.issue_key()
        self.service.revoke_license_key("admin", issued["id"], "test revocation")
        assert_service_error(
            self, 403, "key_revoked",
            self.activate_key, issued["key"], SERIAL_KEY, MACHINE_A,
        )

    def test_activate_key_enforces_activation_limit(self) -> None:
        issued = self.issue_key(max_activations=1)
        first = self.activate_key(issued["key"], SERIAL_SECOND, MACHINE_A)
        self.assertEqual(first["status"], "granted")

        assert_service_error(
            self, 409, "activation_limit",
            self.activate_key, issued["key"], SERIAL_THIRD, MACHINE_B,
        )


class RenewalAndUpgradeTests(PortalServiceTestCase):
    def test_renew_license_extends_expiry_and_bumps_revision(self) -> None:
        key = self.issue_key()["key"]
        first = self.activate_key(key)
        old_expiry = date.fromisoformat(first["expires"])

        renewed = self.service.renew_license("admin", SERIAL_KEY, {"days": 30})
        license = renewed["license"]
        self.assertTrue(renewed["ok"])
        self.assertEqual(license["license_id"], first["license_id"])
        self.assertEqual(license["revision"], first["revision"] + 1)
        self.assertEqual(
            date.fromisoformat(license["expires"]),
            old_expiry + timedelta(days=30),
        )

        payload = self.verify_document(license["document"])
        self.assertEqual(payload["revision"], license["revision"])
        self.assertEqual(payload["expires"], license["expires"])

    def test_upgrade_license_switches_edition_and_features(self) -> None:
        key = self.issue_key()["key"]
        self.activate_key(key)
        renewed = self.service.renew_license("admin", SERIAL_KEY, {"days": 30})
        before = renewed["license"]

        upgraded = self.service.upgrade_license(
            "admin", SERIAL_KEY, {"edition": "enterprise"},
        )
        license = upgraded["license"]
        self.assertTrue(upgraded["ok"])
        self.assertEqual(license["edition"], tool_license.EDITION_ENTERPRISE)
        self.assertEqual(license["revision"], before["revision"] + 1)
        self.assertTrue(license["license_id"].startswith("ENT-"))
        for feature in (
            tool_license.FEATURE_NATIVE_RUNTIME,
            tool_license.FEATURE_LICENSE_ISSUANCE,
            tool_license.FEATURE_CI,
        ):
            self.assertIn(feature, license["features"])

        entitlement = self.entitlement(license["document"])
        self.assertEqual(entitlement.edition, tool_license.EDITION_ENTERPRISE)
        self.assertTrue(entitlement.allows(tool_license.FEATURE_CI))
        self.assertTrue(entitlement.allows(tool_license.FEATURE_NATIVE_RUNTIME))

    def test_license_status_returns_new_document_for_stale_client(self) -> None:
        key = self.issue_key()["key"]
        first = self.activate_key(key)
        renewed = self.service.renew_license("admin", SERIAL_KEY, {"days": 30})
        renewed_license = renewed["license"]

        stale = self.service.license_status(
            SERIAL_KEY, MACHINE_A, first["license_id"], first["revision"],
        )
        self.assertEqual(stale["status"], "active")
        self.assertIn("document", stale)
        self.assertEqual(stale["revision"], renewed_license["revision"])
        payload = self.verify_document(stale["document"])
        self.assertEqual(payload["revision"], renewed_license["revision"])

        current = self.service.license_status(
            SERIAL_KEY, MACHINE_A,
            renewed_license["license_id"], renewed_license["revision"],
        )
        self.assertNotIn("document", current)

        upgraded = self.service.upgrade_license(
            "admin", SERIAL_KEY, {"edition": "enterprise"},
        )
        upgraded_license = upgraded["license"]
        stale = self.service.license_status(
            SERIAL_KEY, MACHINE_A,
            renewed_license["license_id"], renewed_license["revision"],
        )
        self.assertIn("document", stale)
        payload = self.verify_document(stale["document"])
        self.assertEqual(payload["edition"], tool_license.EDITION_ENTERPRISE)
        self.assertEqual(payload["revision"], upgraded_license["revision"])


class RevocationTests(PortalServiceTestCase):
    def test_revoke_license_updates_status_and_revocations_document(self) -> None:
        started = self.start_trial()
        license_id = started["license_id"]

        result = self.service.revoke_license("admin", SERIAL_TRIAL, "fraud")
        self.assertTrue(result["ok"])

        status = self.service.license_status(SERIAL_TRIAL, MACHINE_A)
        self.assertEqual(status["status"], "revoked")
        self.assertEqual(status["reason"], "fraud")
        assert_service_error(
            self, 403, "license_revoked",
            self.service.claim_trial, SERIAL_TRIAL, MACHINE_A,
        )

        document = self.service.revocations_document()
        payload = tool_license.verify_document(
            document,
            self.signing_key.public_key,
            tool_license.REVOCATION_FORMAT,
            tool_license.REVOCATION_SIGNATURE_PREFIX,
        )
        self.assertIn(license_id, payload["revoked"])


class AdminAuthenticationTests(PortalServiceTestCase):
    def test_bootstrap_admin_can_use_short_password(self) -> None:
        assert_service_error(
            self, 400, "error",
            self.service.create_admin, "bootstrap", "admin123@",
        )
        admin_id = self.service.create_admin(
            "bootstrap", "admin123@", allow_weak_password=True,
        )
        self.assertGreater(admin_id, 0)
        session = self.service.authenticate("bootstrap", "admin123@", TEST_IP)
        self.assertEqual(session["admin"]["username"], "bootstrap")

    def test_admin_login_session_and_logout(self) -> None:
        admin_id = self.service.create_admin("admin", "correct-horse-battery")
        self.assertGreater(admin_id, 0)
        self.assertTrue(self.service.has_admin())

        assert_service_error(
            self, 401, "login_failed",
            self.service.authenticate, "admin", "wrong-password", TEST_IP,
        )

        session = self.service.authenticate(
            "admin", "correct-horse-battery", TEST_IP, "unittest-agent",
        )
        self.assertTrue(session["token"])
        self.assertTrue(session["csrf"])
        self.assertTrue(session["expires"])
        self.assertEqual(session["admin"]["username"], "admin")
        self.assertEqual(session["admin"]["role"], "admin")

        resolved = self.service.resolve_session(session["token"])
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["admin_id"], admin_id)
        self.assertEqual(resolved["username"], "admin")
        self.assertEqual(resolved["csrf"], session["csrf"])

        self.assertIsNone(self.service.resolve_session("not-a-real-token"))
        self.assertIsNone(self.service.resolve_session(""))

        self.service.delete_session(session["token"])
        self.assertIsNone(self.service.resolve_session(session["token"]))


class DashboardTests(PortalServiceTestCase):
    def test_dashboard_counts(self) -> None:
        self.start_trial()
        keys = self.service.create_license_keys(
            "admin", {"edition": "pro", "quantity": 2, "max_builds": 40},
        )["keys"]
        self.activate_key(keys[0]["key"], SERIAL_SECOND, MACHINE_B)
        self.service.create_upgrade_request(
            {"edition": "enterprise", "serial": SERIAL_TRIAL, "contact": EMAIL},
            TEST_IP,
        )

        board = self.service.dashboard()
        counts = board["counts"]
        expected = {
            "licenses": 2,
            "trials": 1,
            "pro": 1,
            "enterprise": 0,
            "revoked": 0,
            "expired": 0,
            "customers": 1,
            "keys_unused": 1,
            "keys_active": 1,
            "orders_pending": 1,
        }
        for name, value in expected.items():
            with self.subTest(count=name):
                self.assertEqual(counts[name], value)
        self.assertEqual(counts["expiring_30d"], 1)
        self.assertEqual(board["signing_key"]["key_id"], self.signing_key.key_id)


if __name__ == "__main__":
    unittest.main()
