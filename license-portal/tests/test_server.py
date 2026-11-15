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

DIST = PORTAL / "web" / "dist"


class ServerBase:
    """Live portal instance used to exercise the HTTP surface."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(
            data_dir=Path(self.temp.name),
            host="127.0.0.1",
            port=0,
            dev=True,
            email_verification=False,
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
        self.machine = "a1" * 16
        self.serial = trial_license.serial_from(self.machine, "server-test-salt")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.service.db.close()
        self.temp.cleanup()

    # -- helpers ----------------------------------------------------------

    def fetch(self, path: str) -> tuple[int, str, str]:
        request = urllib.request.Request(self.base + path, method="GET")
        try:
            with self.opener.open(request, timeout=10) as response:
                return (
                    response.status,
                    response.headers.get("Content-Type", ""),
                    response.read().decode("utf-8", "replace"),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read().decode("utf-8", "replace")

    def get_json(self, path: str) -> tuple[int, dict]:
        status, _, body = self.fetch(path)
        return status, json.loads(body)

    def post_json(self, path: str, payload: dict) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def issue_trial(self) -> dict:
        return self.service.start_trial({
            "serial": self.serial,
            "machine": self.machine,
            "email": "desktop@example.com",
            "phone": "13800138200",
            "name": "桌面客户端",
            "org_type": "individual",
            "company_name": "",
            "consent": True,
        }, "127.0.0.1")


class SpaHostingTests(ServerBase, unittest.TestCase):
    def test_root_serves_the_react_shell(self) -> None:
        status, content_type, body = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn('<div id="root">', body)
        self.assertIn("/assets/", body)

    def test_client_side_routes_fall_back_to_index(self) -> None:
        for path in ("/activate?serial=PS-1&machine=x", "/admin/licenses/PS-1234-5678-9012", "/admin"):
            status, content_type, body = self.fetch(path)
            self.assertEqual(status, 200, path)
            self.assertIn("text/html", content_type, path)
            self.assertIn('<div id="root">', body, path)

    def test_hashed_asset_is_served_with_long_cache(self) -> None:
        assets = sorted((DIST / "assets").glob("*.js"))
        self.assertTrue(assets, "web/dist is missing; run npm run build")
        status, content_type, body = self.fetch(f"/assets/{assets[0].name}")
        self.assertEqual(status, 200)
        self.assertIn("javascript", content_type)
        self.assertGreater(len(body), 1000)

    def test_missing_asset_is_a_404(self) -> None:
        status, _, body = self.fetch("/assets/does-not-exist.js")
        self.assertEqual(status, 404)
        self.assertIn("文件不存在", body)

    def test_robots_and_health(self) -> None:
        status, _, body = self.fetch("/robots.txt")
        self.assertEqual(status, 200)
        self.assertIn("Disallow: /admin", body)
        status, payload = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], "pyshield-license-portal")

    def test_service_failure_returns_json_error(self) -> None:
        status, payload = self.post_json("/api/license/status", {"serial": "", "machine": ""})
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "validation_failed")
        self.assertIn("serial", payload["errors"])


class MissingFrontendTests(unittest.TestCase):
    def test_unbuilt_frontend_returns_503_with_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as empty, tempfile.TemporaryDirectory() as data:
            config = Config(
                data_dir=Path(data), host="127.0.0.1", port=0, dev=True,
                email_verification=False, web_dir=empty,
            )
            service = create_service(config, mailer=MemoryMailer(), captcha=NullCaptcha())
            server = create_server(config, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address[:2]
            try:
                try:
                    urllib.request.urlopen(f"http://{host}:{port}/", timeout=10)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 503)
                    body = exc.read().decode("utf-8")
                    self.assertIn("前端尚未构建", body)
                    self.assertIn("npm run build", body)
                else:
                    self.fail("expected 503 when web/dist is missing")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                service.db.close()


class DesktopClientApiTests(ServerBase, unittest.TestCase):
    """The five endpoints used by pyshield/trial_license.py."""

    def test_trial_status_and_claim(self) -> None:
        self.issue_trial()
        status, payload = self.post_json("/api/trial/status", {
            "serial": self.serial, "machine": self.machine,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "granted")

        status, payload = self.get_json(
            f"/api/trial/status?serial={self.serial}&machine={self.machine}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "granted")

        status, payload = self.post_json("/api/trial/claim", {
            "serial": self.serial, "machine": self.machine,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["document"]["payload"]["edition"], "trial")
        self.assertEqual(payload["deep_link"].split("?")[0], "pyshield://activate")

    def test_unknown_serial_reports_unknown(self) -> None:
        status, payload = self.post_json("/api/trial/status", {
            "serial": self.serial, "machine": self.machine,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "unknown")

    def test_license_activate_and_status(self) -> None:
        issued = self.service.create_license_keys("admin", {
            "edition": "pro", "days": 365, "max_builds": 100, "seats": 1,
            "max_activations": 2, "grace_days": 14, "quantity": 1,
            "redemption_days": 30, "batch_name": "", "notes": "",
        })
        key = issued["keys"][0]["key"]

        status, payload = self.post_json("/api/license/activate", {
            "key": key, "serial": self.serial, "machine": self.machine, "version": "0.6.0",
        })
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["edition"], "pro")
        revision = payload["revision"]

        status, payload = self.post_json("/api/license/status", {
            "serial": self.serial, "machine": self.machine,
            "license_id": payload["license_id"], "revision": revision,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "active")

        # A stale revision receives a freshly signed document.
        status, payload = self.post_json("/api/license/status", {
            "serial": self.serial, "machine": self.machine,
            "license_id": "PRO-OLD", "revision": 0,
        })
        self.assertEqual(status, 200)
        self.assertIn("document", payload)

    def test_revoked_license_and_revocation_document(self) -> None:
        issued = self.service.create_license_keys("admin", {
            "edition": "pro", "days": 30, "quantity": 1,
        })
        status, payload = self.post_json("/api/license/activate", {
            "key": issued["keys"][0]["key"],
            "serial": self.serial, "machine": self.machine,
        })
        self.assertEqual(status, 200, payload)
        license_id = payload["license_id"]

        self.service.revoke_license("admin", self.serial, "server test")
        status, payload = self.post_json("/api/license/status", {
            "serial": self.serial, "machine": self.machine,
            "license_id": license_id, "revision": 1,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "revoked")

        status, payload = self.get_json("/api/license/revocations")
        self.assertEqual(status, 200)
        self.assertIn(license_id, payload["payload"]["revoked"])
        self.assertIn("signature", payload)

    def test_wrong_machine_is_rejected(self) -> None:
        self.issue_trial()
        status, payload = self.post_json("/api/license/status", {
            "serial": self.serial, "machine": "ff" * 16,
            "license_id": "TRIAL", "revision": 0,
        })
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "machine_mismatch")

    def test_version_endpoint(self) -> None:
        status, payload = self.get_json("/api/version")
        self.assertEqual(status, 200)
        self.assertEqual(payload["version"], "0.1.0")

    def test_unknown_api_returns_json_404(self) -> None:
        status, payload = self.get_json("/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "not_found")


if __name__ == "__main__":
    unittest.main()
