"""Encrypted cloud-guard endpoints: verify / preflight / consume + admin UI."""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PORTAL))

from license_portal import security
from license_portal.captcha import NullCaptcha
from license_portal.config import Config
from license_portal.mailer import MemoryMailer
from license_portal.server import create_server
from license_portal.service import create_service
from pyshield import crypto, trial_license


class GuardBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(
            data_dir=Path(self.temp.name),
            host="127.0.0.1",
            port=0,
            dev=True,
            site_name="测试授权中心",
            email_verification=False,
        )
        self.service = create_service(
            self.config, mailer=MemoryMailer(), captcha=NullCaptcha(),
        )
        self.service.create_admin("admin", "password12345")
        self.server = create_server(self.config, self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
        )
        self.machine = "e" * 32
        self.serial = trial_license.serial_from(self.machine, "guard-test-salt")
        self.csrf = ""
        self.grant = self.apply_trial()
        self.license_id = self.grant["license_id"]
        self.revision = int(self.grant.get("revision") or 0)
        self.display_code = self.grant["trial_code"]
        self.key = security.derive_guard_key(self.serial, self.machine, self.display_code)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.service.db.close()
        self.temp.cleanup()

    # -- plain HTTP ---------------------------------------------------------

    def http(
        self, method: str, path: str, payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        data = None
        request_headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if self.csrf:
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

    def apply_trial(self) -> dict:
        status, payload = self.http("POST", "/api/public/trial/apply", {
            "serial": self.serial,
            "machine": self.machine,
            "edition": "trial",
            "email": "guard@example.com",
            "phone": "13800138000",
            "name": "守护测试",
            "org_type": "individual",
            "company_name": "",
            "consent": True,
        })
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload.get("trial_code"), payload)
        return payload

    def login(self) -> None:
        status, payload = self.http("POST", "/api/admin/login", {
            "username": "admin", "password": "password12345",
        })
        self.assertEqual(status, 200, payload)
        self.csrf = payload["csrf_token"]

    # -- encrypted guard calls ----------------------------------------------

    def inner(self, **overrides) -> dict:
        data = {
            "serial": self.serial,
            "machine": self.machine,
            "license_id": self.license_id,
            "edition": "trial",
            "revision": self.revision,
            "nonce": secrets.token_hex(16),
            "client_time": int(time.time()),
        }
        data.update(overrides)
        return data

    def guard(
        self, name: str, inner: dict | None = None, *,
        key: bytes | None = None, serial: str | None = None,
    ) -> tuple[int, dict]:
        inner = self.inner() if inner is None else inner
        serial = serial or self.serial
        key = key or self.key
        aad = f"pyshield-cloud:{name}:{serial}".encode("utf-8")
        envelope = crypto.seal(
            key,
            json.dumps(inner, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            aad,
            cipher="chacha-pure",
        )
        status, body = self.http(
            "POST", f"/api/license/{name}",
            {"serial": serial, "data": crypto.b85encode(envelope)},
        )
        if status == 200 and body.get("ok") and body.get("data"):
            plaintext = crypto.open_envelope(
                key, crypto.b85decode(body["data"]), aad + b":response",
            )
            return status, json.loads(plaintext.decode("utf-8"))
        return status, body


class VerifyTests(GuardBase):
    def test_verify_happy_path(self) -> None:
        status, result = self.guard("verify")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["serial"], self.serial)
        self.assertEqual(result["license_id"], self.license_id)
        self.assertEqual(result["edition"], "trial")
        self.assertEqual(result["quota_period"], "total")
        self.assertGreaterEqual(result["next_check_after"], 60)
        self.assertLessEqual(result["next_check_after"], 300)
        row = self.service.db.query_one(
            "SELECT last_seen_at FROM licenses WHERE serial = ?", (self.serial,),
        )
        self.assertTrue(row["last_seen_at"])

    def test_verify_nonce_echo(self) -> None:
        inner = self.inner()
        status, result = self.guard("verify", inner)
        self.assertEqual(status, 200)
        self.assertEqual(result["nonce"], inner["nonce"])

    def test_machine_mismatch(self) -> None:
        status, result = self.guard("verify", self.inner(machine="f" * 32))
        self.assertEqual(status, 403)
        self.assertEqual(result["code"], "machine_mismatch")

    def test_serial_mismatch(self) -> None:
        status, result = self.guard("verify", self.inner(serial="PS-AAAA-AAAA-AAAA"))
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "serial_mismatch")

    def test_edition_mismatch(self) -> None:
        status, result = self.guard("verify", self.inner(edition="pro"))
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "edition_mismatch")

    def test_revoked_license(self) -> None:
        self.login()
        status, _ = self.http(
            "POST", f"/api/admin/licenses/{self.serial}/revoke", {"reason": "测试吊销"},
        )
        self.assertEqual(status, 200)
        status, result = self.guard("verify")
        self.assertEqual(status, 403)
        self.assertEqual(result["code"], "license_revoked")

    def test_expired_license(self) -> None:
        self.service.db.execute(
            "UPDATE licenses SET expires = ?, grace_days = 0 WHERE serial = ?",
            ("2000-01-01", self.serial),
        )
        status, result = self.guard("verify")
        self.assertEqual(status, 403)
        self.assertEqual(result["code"], "license_expired")

    def test_replay_detected(self) -> None:
        inner = self.inner()
        status, _ = self.guard("verify", inner)
        self.assertEqual(status, 200)
        status, result = self.guard("verify", dict(inner))
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "replay_detected")

    def test_clock_skew(self) -> None:
        status, result = self.guard(
            "verify", self.inner(client_time=int(time.time()) - 10000),
        )
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "clock_skew")

    def test_invalid_nonce(self) -> None:
        status, result = self.guard("verify", self.inner(nonce="xyz"))
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "invalid_nonce")

    def test_tampered_envelope(self) -> None:
        inner = self.inner()
        aad = f"pyshield-cloud:verify:{self.serial}".encode("utf-8")
        envelope = bytearray(crypto.seal(
            self.key, json.dumps(inner).encode("utf-8"), aad, cipher="chacha-pure",
        ))
        envelope[-1] ^= 0xFF
        status, result = self.http("POST", "/api/license/verify", {
            "serial": self.serial, "data": crypto.b85encode(bytes(envelope)),
        })
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "bad_envelope")

    def test_wrong_key(self) -> None:
        status, result = self.guard("verify", key=bytes(32))
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "bad_envelope")

    def test_missing_envelope(self) -> None:
        status, result = self.http("POST", "/api/license/verify", {"serial": self.serial})
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "missing_envelope")

    def test_unknown_serial(self) -> None:
        status, result = self.http("POST", "/api/license/verify", {
            "serial": "PS-ZZZZ-ZZZZ-ZZZZ", "data": "xxxx",
        })
        self.assertEqual(status, 404)
        self.assertEqual(result["code"], "license_not_found")

    def test_get_not_allowed(self) -> None:
        status, result = self.http("GET", "/api/license/verify")
        self.assertEqual(status, 405)
        self.assertEqual(result["code"], "method_not_allowed")

    def test_update_required_after_renew(self) -> None:
        self.login()
        status, payload = self.http(
            "POST", f"/api/admin/licenses/{self.serial}/renew", {"days": 30},
        )
        self.assertEqual(status, 200, payload)
        status, result = self.guard("verify")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["status"], "update_required")
        document = result.get("document")
        self.assertIsInstance(document, dict)
        self.assertGreater(document["payload"]["revision"], self.revision)


class ConsumeTests(GuardBase):
    def test_preflight_does_not_charge(self) -> None:
        status, result = self.guard("preflight")
        self.assertEqual(status, 200, result)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["quota_used"], 0)
        status, result = self.guard("consume", self.inner(idempotency_key="job-1"))
        self.assertEqual(status, 200, result)
        self.assertTrue(result["charged"])
        self.assertEqual(result["quota_used"], 1)

    def test_consume_charges_once_per_idempotency_key(self) -> None:
        inner = self.inner(idempotency_key="job-42")
        status, first = self.guard("consume", inner)
        self.assertEqual(status, 200, first)
        self.assertEqual(first["quota_used"], 1)
        status, again = self.guard("consume", self.inner(idempotency_key="job-42"))
        self.assertEqual(status, 200, again)
        self.assertFalse(again["charged"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["quota_used"], 1)
        rows = self.service.db.query_all(
            "SELECT * FROM build_charges WHERE serial = ?", (self.serial,),
        )
        self.assertEqual(len(rows), 1)

    def test_consume_requires_idempotency_key(self) -> None:
        status, result = self.guard("consume")
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "invalid_idempotency_key")

    def test_quota_exhausted(self) -> None:
        self.login()
        status, payload = self.http(
            "POST", f"/api/admin/licenses/{self.serial}/build-quota",
            {"limit": 1, "reset": True},
        )
        self.assertEqual(status, 200, payload)
        status, result = self.guard("consume", self.inner(idempotency_key="job-a"))
        self.assertEqual(status, 200, result)
        self.assertEqual(result["remaining_builds"], 0)
        status, result = self.guard("consume", self.inner(idempotency_key="job-b"))
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "quota_exhausted")

    def test_trial_quota_is_total_not_monthly(self) -> None:
        self.service.db.execute(
            "UPDATE licenses SET builds_used = 1, builds_period = 'total' WHERE serial = ?",
            (self.serial,),
        )
        status, result = self.guard("consume", self.inner(idempotency_key="job-t"))
        self.assertEqual(status, 200, result)
        self.assertEqual(result["quota_used"], 2)
        self.assertEqual(result["quota_period"], "total")

    def test_paid_quota_resets_monthly(self) -> None:
        self.service.db.execute(
            "UPDATE licenses SET edition = 'pro', builds_used = 50,"
            " builds_period = '2000-01' WHERE serial = ?",
            (self.serial,),
        )
        status, result = self.guard(
            "consume", self.inner(edition="pro", idempotency_key="job-m"),
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["quota_used"], 1)
        self.assertEqual(result["quota_period"], time.strftime("%Y-%m"))

    def test_unlimited_quota(self) -> None:
        self.login()
        status, _ = self.http(
            "POST", f"/api/admin/licenses/{self.serial}/build-quota", {"limit": 0},
        )
        self.assertEqual(status, 200)
        self.service.db.execute(
            "UPDATE licenses SET builds_used = 999 WHERE serial = ?", (self.serial,),
        )
        status, result = self.guard("consume", self.inner(idempotency_key="job-u"))
        self.assertEqual(status, 200, result)
        self.assertIsNone(result["remaining_builds"])


class AdminGuardTests(GuardBase):
    def test_client_checks_list_and_filters(self) -> None:
        self.guard("verify")
        self.guard("consume", self.inner(idempotency_key="job-x"))
        self.guard("verify", self.inner(machine="f" * 32))
        self.login()
        status, payload = self.http("GET", "/api/admin/client-checks?limit=50")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["total"], 3)
        kinds = {item["kind"] for item in payload["items"]}
        self.assertEqual(kinds, {"verify", "consume"})
        status, payload = self.http("GET", "/api/admin/client-checks?ok=0")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["code"], "machine_mismatch")
        status, payload = self.http(
            "GET", f"/api/admin/client-checks?kind=consume&serial={self.serial}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)

    def test_build_quota_adjust_and_detail(self) -> None:
        self.guard("verify")
        self.login()
        status, payload = self.http(
            "POST", f"/api/admin/licenses/{self.serial}/build-quota",
            {"limit": 25, "reset": True},
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["license"]["builds_limit"], 25)
        self.assertEqual(payload["license"]["builds_used"], 0)
        status, payload = self.http("GET", f"/api/admin/licenses/{self.serial}")
        self.assertEqual(status, 200, payload)
        detail = payload["license"]
        self.assertEqual(detail["builds_limit"], 25)
        self.assertIn("client_checks", detail)
        self.assertTrue(detail["client_checks"])
        self.assertIn("last_seen_at", detail)
        status, payload = self.http("GET", "/api/admin/audit?action=build_quota_adjust")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)

    def test_build_quota_validation(self) -> None:
        self.login()
        status, result = self.http(
            "POST", f"/api/admin/licenses/{self.serial}/build-quota", {"limit": -5},
        )
        self.assertEqual(status, 400)
        status, result = self.http(
            "POST", "/api/admin/licenses/PS-ZZZZ-ZZZZ-ZZZZ/build-quota", {"limit": 5},
        )
        self.assertEqual(status, 404)
        self.assertEqual(result["code"], "license_not_found")


if __name__ == "__main__":
    unittest.main()