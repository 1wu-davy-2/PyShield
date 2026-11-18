from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PORTAL))

from license_portal.captcha import NullCaptcha
from license_portal.config import Config
from license_portal.mailer import MemoryMailer
from license_portal.server import create_server
from license_portal.service import create_service
from pyshield import trial_license


class ApiBase:
    """Browser-style JSON client bound to a live portal instance."""

    email_verification = False

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(
            data_dir=Path(self.temp.name),
            host="127.0.0.1",
            port=0,
            dev=True,
            site_name="测试授权中心",
            contact_wechat="pyshield-support",
            email_verification=self.email_verification,
        )
        self.mailer = MemoryMailer()
        self.service = create_service(self.config, mailer=self.mailer, captcha=NullCaptcha())
        self.service.create_admin("admin", "password12345")
        self.server = create_server(self.config, self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.machine = "e" * 32
        self.serial = trial_license.serial_from(self.machine, "api-test-salt")
        self.csrf = ""

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.service.db.close()
        self.temp.cleanup()

    # -- transport --------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        headers: dict | None = None,
        csrf: bool = True,
    ) -> tuple[int, dict]:
        data = None
        request_headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if csrf and self.csrf:
            request_headers["X-CSRF-Token"] = self.csrf
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.base + path, data=data, headers=request_headers, method=method,
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"raw": raw}

    def get(self, path: str, **kwargs) -> tuple[int, dict]:
        return self.request("GET", path, None, **kwargs)

    def post(self, path: str, payload: dict | None = None, **kwargs) -> tuple[int, dict]:
        return self.request("POST", path, payload if payload is not None else {}, **kwargs)

    # -- fixtures ---------------------------------------------------------

    def trial_payload(self, **overrides) -> dict:
        payload = {
            "serial": self.serial,
            "machine": self.machine,
            "edition": "trial",
            "email": "browser@example.com",
            "phone": "13800138123",
            "name": "浏览器用户",
            "org_type": "individual",
            "company_name": "",
            "consent": True,
        }
        payload.update(overrides)
        return payload

    def login(self) -> None:
        status, payload = self.post("/api/admin/login", {
            "username": "admin", "password": "password12345",
        })
        self.assertEqual(status, 200, payload)
        self.csrf = payload["csrf_token"]

    def create_key(self, **overrides) -> str:
        body = {
            "edition": "pro",
            "days": 365,
            "max_builds": 100,
            "seats": 1,
            "max_activations": 1,
            "grace_days": 14,
            "quantity": 1,
            "redemption_days": 30,
            "batch_name": "api-test",
            "notes": "api test key",
        }
        body.update(overrides)
        self.login()
        status, payload = self.post("/api/admin/keys", body)
        self.assertEqual(status, 201, payload)
        return payload["keys"][0]["key"]


class PublicApiTests(ApiBase, unittest.TestCase):
    def test_config_exposes_trial_limits(self) -> None:
        status, payload = self.get("/api/public/config")
        self.assertEqual(status, 200)
        self.assertEqual(payload["site_name"], "测试授权中心")
        self.assertEqual(payload["trial_days"], 14)
        self.assertEqual(payload["trial_builds"], 3)
        self.assertEqual(payload["contact_wechat"], "pyshield-support")
        self.assertFalse(payload["email_verification"])

    def test_trial_apply_returns_code_and_deep_link(self) -> None:
        status, payload = self.post("/api/public/trial/apply", self.trial_payload())
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["verification_required"])
        self.assertEqual(payload["status"], "granted")
        self.assertEqual(payload["edition"], "trial")
        self.assertRegex(payload["trial_code"], r"^PST-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
        self.assertTrue(payload["deep_link"].startswith("pyshield://activate?"))
        self.assertIn("serial=", payload["deep_link"])
        self.assertIn("code=", payload["deep_link"])
        self.assertNotIn("document", payload)

        status, payload = self.get(f"/api/public/license?serial={self.serial}&machine={self.machine}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["edition"], "trial")

    def test_trial_apply_validates_fields(self) -> None:
        status, payload = self.post("/api/public/trial/apply", self.trial_payload(
            email="not-an-email", phone="123", consent=False,
        ))
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "validation_failed")
        for field in ("email", "phone", "consent"):
            self.assertIn(field, payload["errors"])

    def test_trial_apply_rejects_honeypot(self) -> None:
        status, payload = self.post("/api/public/trial/apply", self.trial_payload(
            website="http://spam.example",
        ))
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "spam_rejected")

    def test_company_requires_company_name(self) -> None:
        status, payload = self.post("/api/public/trial/apply", self.trial_payload(
            org_type="company", company_name="",
        ))
        self.assertEqual(status, 400)
        self.assertIn("company_name", payload["errors"])

    def test_duplicate_trial_is_rejected(self) -> None:
        status, _ = self.post("/api/public/trial/apply", self.trial_payload())
        self.assertEqual(status, 200)
        status, payload = self.post("/api/public/trial/apply", self.trial_payload(
            email="other@example.com", phone="13800138999",
        ))
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload.get("already"))

    def test_redeem_key_binds_licence(self) -> None:
        key = self.create_key()
        status, payload = self.post("/api/public/redeem", {
            "key": key, "serial": self.serial, "machine": self.machine,
        })
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["edition"], "pro")
        self.assertGreaterEqual(payload["revision"], 1)

    def test_upgrade_request_creates_order(self) -> None:
        status, payload = self.post("/api/public/upgrade", {
            "edition": "pro",
            "contact": "wx-12345",
            "notes": "希望本月上线",
        })
        self.assertEqual(status, 200, payload)
        self.assertGreater(payload["order_id"], 0)
        self.assertEqual(payload["contact_wechat"], "pyshield-support")

    def test_origin_is_checked_on_write(self) -> None:
        status, payload = self.post(
            "/api/public/trial/apply", self.trial_payload(),
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "origin_rejected")

    def test_unknown_public_endpoint(self) -> None:
        status, payload = self.get("/api/public/nope")
        self.assertEqual(status, 404)


class EmailVerificationApiTests(ApiBase, unittest.TestCase):
    email_verification = True

    def test_email_code_flow(self) -> None:
        status, payload = self.post("/api/public/trial/apply", self.trial_payload())
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["verification_required"])
        application_id = payload["application_id"]
        self.assertEqual(payload["email"], "b*****r@example.com")

        status, meta = self.get(f"/api/public/trial/application?id={application_id}")
        self.assertEqual(status, 200)
        self.assertEqual(meta["status"], "pending_email")

        status, payload = self.post("/api/public/trial/verify", {
            "application_id": application_id, "code": "000000",
        })
        self.assertEqual(status, 400)

        status, payload = self.post("/api/public/trial/verify", {
            "application_id": application_id, "code": self.mailer.last_code,
        })
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], "granted")
        self.assertTrue(payload["deep_link"].startswith("pyshield://"))

    def test_resend_code_and_finish_verification(self) -> None:
        status, payload = self.post("/api/public/trial/apply", self.trial_payload())
        self.assertEqual(status, 200, payload)
        application_id = payload["application_id"]

        status, payload = self.post("/api/public/trial/resend", {
            "application_id": application_id,
        })
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["verification_required"])
        self.assertEqual(payload["application_id"], application_id)
        self.assertEqual(payload["resend_after"], 60)

        status, payload = self.post("/api/public/trial/verify", {
            "application_id": application_id, "code": self.mailer.last_code,
        })
        self.assertEqual(status, 200, payload)

        status, payload = self.post("/api/public/trial/resend", {
            "application_id": application_id,
        })
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "application_verified")

        status, payload = self.post("/api/public/trial/resend", {
            "application_id": 0,
        })
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "validation_failed")


class AdminApiTests(ApiBase, unittest.TestCase):
    def test_login_me_logout(self) -> None:
        self.login()
        status, payload = self.get("/api/admin/me")
        self.assertEqual(status, 200)
        self.assertEqual(payload["admin"]["username"], "admin")
        self.assertEqual(payload["csrf_token"], self.csrf)

        status, payload = self.post("/api/admin/logout", {})
        self.assertEqual(status, 200)
        self.csrf = ""
        status, payload = self.get("/api/admin/me")
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "unauthorized")

    def test_login_rejects_bad_password(self) -> None:
        status, payload = self.post("/api/admin/login", {
            "username": "admin", "password": "wrong-password",
        })
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "login_failed")

    def test_reads_require_session(self) -> None:
        for path in ("/api/admin/dashboard", "/api/admin/licenses", "/api/admin/keys"):
            status, payload = self.get(path)
            self.assertEqual(status, 401, path)
            self.assertEqual(payload["code"], "unauthorized")

    def test_writes_require_csrf_header(self) -> None:
        self.login()
        csrf = self.csrf
        self.csrf = "deadbeef"
        status, payload = self.post("/api/admin/keys", {"edition": "pro"})
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "csrf_failed")
        self.csrf = csrf

    def test_dashboard_and_signing(self) -> None:
        self.login()
        status, payload = self.get("/api/admin/dashboard")
        self.assertEqual(status, 200)
        self.assertIn("counts", payload)
        self.assertIn("signing_key", payload)
        self.assertIn("email", payload)
        self.assertFalse(payload["email"]["configured"])
        self.assertFalse(payload["email"]["verification"])
        status, payload = self.get("/api/admin/signing")
        self.assertEqual(status, 200)
        self.assertTrue(payload["signing_key"]["public_key"])
        self.assertIn("build_commercial.py", payload["build_command"])

    def test_key_lifecycle(self) -> None:
        self.login()
        status, payload = self.post("/api/admin/keys", {
            "edition": "enterprise", "days": 180, "quantity": 2,
            "max_builds": 50, "seats": 3, "max_activations": 5,
            "grace_days": 7, "redemption_days": 15, "batch_name": "batch-a",
        })
        self.assertEqual(status, 201, payload)
        self.assertEqual(len(payload["keys"]), 2)
        key_id = payload["keys"][0]["id"]
        plaintext = payload["keys"][0]["key"]
        self.assertTrue(plaintext.startswith("PSE-"))

        status, payload = self.get("/api/admin/keys")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 2)
        self.assertTrue(all("key" not in item for item in payload["items"]))

        status, payload = self.get(f"/api/admin/keys/{key_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["key"]["edition"], "enterprise")
        self.assertNotIn("key_encrypted", payload["key"])

        status, payload = self.post(f"/api/admin/keys/{key_id}/reveal", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["key"], plaintext)

        status, payload = self.post(f"/api/admin/keys/{key_id}/revoke", {"reason": "测试吊销"})
        self.assertEqual(status, 200)
        status, payload = self.get(f"/api/admin/keys/{key_id}")
        self.assertEqual(payload["key"]["effective_status"], "revoked")

    def test_license_renew_upgrade_revoke(self) -> None:
        key = self.create_key()
        status, redeemed = self.post("/api/public/redeem", {
            "key": key, "serial": self.serial, "machine": self.machine,
        })
        self.assertEqual(status, 200, redeemed)
        revision = redeemed["revision"]

        self.login()
        status, payload = self.get(f"/api/admin/licenses?q={self.serial}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        self.assertNotIn("document", payload["items"][0])

        status, payload = self.get(f"/api/admin/licenses/{self.serial}")
        self.assertEqual(status, 200)
        self.assertIn("events", payload["license"])
        self.assertTrue(payload["license"]["has_document"])

        status, payload = self.post(f"/api/admin/licenses/{self.serial}/renew", {
            "days": 30, "notes": "api renew",
        })
        self.assertEqual(status, 200, payload)
        self.assertGreater(payload["license"]["revision"], revision)

        status, payload = self.post(f"/api/admin/licenses/{self.serial}/upgrade", {
            "edition": "enterprise", "days": 365, "seats": 5,
        })
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["license"]["edition"], "enterprise")
        self.assertEqual(payload["license"]["seats"], 5)

        status, payload = self.post(f"/api/admin/licenses/{self.serial}/revoke", {
            "reason": "api revoke",
        })
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["license"]["effective_status"], "revoked")

    def test_customers_orders_audit(self) -> None:
        self.post("/api/public/trial/apply", self.trial_payload())
        self.post("/api/public/upgrade", {
            "edition": "pro", "contact": "wx-999", "notes": "报价咨询",
        })
        self.login()

        status, payload = self.get("/api/admin/customers")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        customer_id = payload["items"][0]["id"]
        self.assertEqual(payload["items"][0]["email"], "browser@example.com")

        status, payload = self.get(f"/api/admin/customers/{customer_id}")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["customer"]["email"], "browser@example.com")
        self.assertEqual(len(payload["licenses"]), 1)
        self.assertEqual(payload["licenses"][0]["serial"], self.serial)
        self.assertEqual(len(payload["applications"]), 1)
        self.assertNotIn("code_hash", payload["applications"][0])

        status, payload = self.get("/api/admin/customers/999999")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "customer_not_found")

        status, payload = self.get("/api/admin/orders")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        order_id = payload["items"][0]["id"]

        status, payload = self.post(f"/api/admin/orders/{order_id}/status", {
            "status": "paid", "amount": "699",
        })
        self.assertEqual(status, 200, payload)
        status, payload = self.get("/api/admin/orders?status=paid")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["amount"], "699")

        status, payload = self.get("/api/admin/audit?action=order_update")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["total"], 1)
        self.assertIsInstance(payload["items"][0]["detail"], dict)

        status, payload = self.get("/api/admin/filters")
        self.assertEqual(status, 200)
        self.assertIn("order_update", payload["audit_actions"])

    def test_license_404(self) -> None:
        self.login()
        status, payload = self.get("/api/admin/licenses/PS-0000-0000-0000")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "license_not_found")


class LicenseDeleteTests(ApiBase, unittest.TestCase):
    def grant_trial(self, **overrides) -> dict:
        status, payload = self.post("/api/public/trial/apply", self.trial_payload(**overrides))
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], "granted")
        return payload

    def seed_guard_rows(self, serial: str) -> None:
        row = self.service.db.query_one("SELECT * FROM licenses WHERE serial = ?", (serial,))
        assert row is not None
        license_id = row["license_id"]
        application = self.service.db.query_one(
            "SELECT id FROM applications WHERE serial = ?", (serial,),
        )
        assert application is not None
        now = "2026-11-18T00:00:00+00:00"
        self.service.db.execute(
            "INSERT INTO email_codes (application_id, email, code_hash, purpose,"
            " expires_at, attempts, consumed_at, created_at)"
            " VALUES (?, 'x@example.com', 'h', 'trial', ?, 0, NULL, ?)",
            (application["id"], now, now),
        )
        self.service.db.execute(
            "INSERT INTO client_checks (at, serial, license_id, kind, ok, code, ip_hash, detail)"
            " VALUES (?, ?, ?, 'verify', 0, 'machine_mismatch', '', '')",
            (now, serial, license_id),
        )
        self.service.db.execute(
            "INSERT INTO build_charges (serial, idempotency_key, license_id, used_after,"
            " quota_limit, quota_period, created_at) VALUES (?, 'job-1', ?, 1, 3, 'total', ?)",
            (serial, license_id, now),
        )
        self.service.db.execute(
            "INSERT INTO guard_nonces (nonce, serial, expires_at) VALUES ('ab12', ?, ?)",
            (serial, now),
        )
        self.service.db.execute(
            "INSERT INTO orders (customer_id, serial, kind, edition, status, contact,"
            " notes, amount, created_at, updated_at, handled_by)"
            " VALUES (?, ?, 'upgrade', 'pro', 'pending', '', '', '', ?, ?, '')",
            (row["customer_id"], serial, now, now),
        )

    def counts(self, serial: str) -> dict:
        tables = (
            "licenses", "applications", "key_activations", "build_charges",
            "client_checks", "guard_nonces", "orders", "events",
        )
        result = {
            table: self.service.db.query_one(
                f"SELECT COUNT(*) AS n FROM {table} WHERE serial = ?", (serial,),
            )["n"]
            for table in tables
        }
        result["email_codes"] = self.service.db.query_one(
            "SELECT COUNT(*) AS n FROM email_codes WHERE application_id IN"
            " (SELECT id FROM applications WHERE serial = ?)", (serial,),
        )["n"]
        return result

    def test_delete_purges_serial_and_orphan_customer(self) -> None:
        self.grant_trial()
        self.seed_guard_rows(self.serial)
        customer = self.service.db.query_one(
            "SELECT customer_id FROM licenses WHERE serial = ?", (self.serial,),
        )["customer_id"]
        self.login()

        status, payload = self.post(
            f"/api/admin/licenses/{self.serial}/delete", {"confirm": "PS-XXXX-XXXX-XXXX"},
        )
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "confirm_mismatch")
        status, _ = self.get(f"/api/admin/licenses/{self.serial}")
        self.assertEqual(status, 200)

        status, payload = self.post(
            f"/api/admin/licenses/{self.serial}/delete", {"confirm": self.serial},
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["removed"]["licenses"], 1)
        self.assertEqual(payload["removed"]["applications"], 1)
        self.assertEqual(payload["removed"]["email_codes"], 1)
        self.assertEqual(payload["removed"]["client_checks"], 1)
        self.assertEqual(payload["removed"]["build_charges"], 1)
        self.assertEqual(payload["removed"]["guard_nonces"], 1)
        self.assertEqual(payload["removed"]["orders"], 1)
        self.assertEqual(payload["removed"]["customers"], 1)

        for table, count in self.counts(self.serial).items():
            expected = 1 if table == "events" else 0
            self.assertEqual(count, expected, table)
        event = self.service.db.query_one(
            "SELECT * FROM events WHERE serial = ?", (self.serial,),
        )
        self.assertEqual(event["action"], "license_delete")
        self.assertEqual(event["actor"], "admin")
        self.assertIsNone(
            self.service.db.query_one("SELECT id FROM customers WHERE id = ?", (customer,)),
        )

        status, payload = self.get(f"/api/admin/licenses/{self.serial}")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "license_not_found")
        status, payload = self.get("/api/admin/licenses")
        self.assertEqual(payload["total"], 0)
        status, payload = self.get("/api/admin/customers")
        self.assertEqual(payload["total"], 0)
        status, payload = self.get("/api/admin/client-checks")
        self.assertEqual(payload["total"], 0)

    def test_delete_keeps_customer_with_other_license(self) -> None:
        self.grant_trial()
        customer_id = self.service.db.query_one(
            "SELECT customer_id FROM licenses WHERE serial = ?", (self.serial,),
        )["customer_id"]
        key = self.create_key()
        other_machine = "f" * 32
        other_serial = trial_license.serial_from(other_machine, "api-test-salt")
        status, payload = self.post("/api/public/redeem", {
            "key": key, "serial": other_serial, "machine": other_machine,
        })
        self.assertEqual(status, 200, payload)
        # Link the paid licence to the same customer so the purge must keep them.
        self.service.db.execute(
            "UPDATE licenses SET customer_id = ? WHERE serial = ?",
            (customer_id, other_serial),
        )
        self.login()
        status, payload = self.post(
            f"/api/admin/licenses/{self.serial}/delete", {"confirm": self.serial},
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["removed"]["customers"], 0)
        status, payload = self.get(f"/api/admin/licenses/{other_serial}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["license"]["customer_email"])
        status, payload = self.get("/api/admin/customers")
        self.assertEqual(payload["total"], 1)

    def test_delete_requires_session_and_csrf(self) -> None:
        self.grant_trial()
        status, payload = self.post(
            f"/api/admin/licenses/{self.serial}/delete", {"confirm": self.serial},
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "unauthorized")
        self.login()
        self.csrf = "deadbeef"
        status, payload = self.post(
            f"/api/admin/licenses/{self.serial}/delete", {"confirm": self.serial},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "csrf_failed")

    def test_delete_unknown_serial_and_method(self) -> None:
        self.login()
        status, payload = self.post(
            "/api/admin/licenses/PS-0000-0000-0000/delete", {"confirm": "PS-0000-0000-0000"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "license_not_found")
        status, payload = self.get(f"/api/admin/licenses/{self.serial}/delete")
        self.assertEqual(status, 405)
        self.assertEqual(payload["code"], "method_not_allowed")


if __name__ == "__main__":
    unittest.main()
