"""Client cloud guard against a live portal: encryption, fail-closed, quota."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "license-portal"
# Append instead of insert: a leading license-portal entry makes the
# ``tests`` namespace resolve to license-portal/tests and breaks other suites.
sys.path.append(str(PORTAL))

from license_portal.captcha import NullCaptcha
from license_portal.config import Config
from license_portal.mailer import MemoryMailer
from license_portal.server import create_server
from license_portal.service import create_service

if str(PORTAL) in sys.path:
    sys.path.remove(str(PORTAL))
from pyshield import cloud_license, crypto, fingerprint, tool_license, trial_license

MACHINE = "e" * 32


class McpActivationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.patch = mock.patch.dict(os.environ, {
            tool_license.ENV_HOME: str(self.home),
            # Pretend to be a packaged build so the activation gate applies.
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(bytes(range(32))),
        })
        self.patch.start()
        for key in (tool_license.ENV_DEV, tool_license.ENV_LICENSE, "PYSHELD_CLOUD_API"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_start_requires_activation(self) -> None:
        from pyshield.build_service import BuildService
        from pyshield.build_service.models import BuildPolicy

        projects = self.root / "projects"
        projects.mkdir()
        service = BuildService(BuildPolicy(
            project_roots=(projects,),
            output_root=self.root / "out",
            secret_dir=self.root / "secrets",
            job_dir=self.root / "jobs",
            audit_dir=self.root / "audit",
            allow_build=True,
        ))
        with self.assertRaises(Exception) as ctx:
            service.start({"project": str(projects)}, confirm=True)
        self.assertEqual(getattr(ctx.exception, "code", ""), "LICENSE_REQUIRED")


class CloudGuardClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()

        self.portal_temp = tempfile.TemporaryDirectory()
        self.config = Config(
            data_dir=Path(self.portal_temp.name),
            host="127.0.0.1",
            port=0,
            dev=True,
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
        self.csrf = ""

        self.env = mock.patch.dict(os.environ, {
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_DEV: "1",
            cloud_license.ENV_ENABLE: "1",
            cloud_license.ENV_BASE: self.base,
        })
        self.env.start()
        self.fp = mock.patch.object(
            fingerprint, "machine_fingerprint", return_value=MACHINE,
        )
        self.fp.start()
        cloud_license.configure("", enable=None)
        cloud_license._mark_ok()

        state = trial_license.install_state()
        self.serial = state["serial"]
        self.grant = self.portal("POST", "/api/public/trial/apply", {
            "serial": self.serial,
            "machine": MACHINE,
            "edition": "trial",
            "email": "client@example.com",
            "phone": "13800138000",
            "name": "客户端测试",
            "org_type": "individual",
            "company_name": "",
            "consent": True,
        })
        self.entitlement = trial_license.claim_and_apply(self.base)

    def tearDown(self) -> None:
        cloud_license.configure("", enable=False)
        self.fp.stop()
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.service.db.close()
        self.portal_temp.cleanup()
        self.temp.cleanup()

    def portal(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method,
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read().decode("utf-8", "replace"))

    def admin_login(self) -> None:
        payload = self.portal("POST", "/api/admin/login", {
            "username": "admin", "password": "password12345",
        })
        self.csrf = payload["csrf_token"]

    # -- tests ---------------------------------------------------------------

    def test_claim_remembers_display_code(self) -> None:
        state = trial_license.install_state()
        self.assertEqual(
            state["codes"][self.serial], self.grant["trial_code"],
        )

    def test_identity_binds_serial_and_machine(self) -> None:
        ident = cloud_license.identity(self.entitlement)
        self.assertEqual(ident["serial"], self.serial)
        self.assertEqual(ident["machine"], MACHINE)
        self.assertEqual(ident["license_id"], self.entitlement.license_id)
        self.assertEqual(ident["edition"], "trial")

    def test_verify_roundtrip(self) -> None:
        result = cloud_license.verify(self.entitlement)
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["serial"], self.serial)
        self.assertEqual(result["quota_period"], "total")
        self.assertFalse(cloud_license.guard_state()["blocked"])

    def test_preflight_and_consume(self) -> None:
        preflight = cloud_license.preflight(self.entitlement)
        self.assertTrue(preflight["allowed"])
        self.assertEqual(preflight["quota_used"], 0)
        first = cloud_license.consume(self.entitlement, idempotency_key="job-1")
        self.assertTrue(first["charged"])
        self.assertEqual(first["quota_used"], 1)
        again = cloud_license.consume(self.entitlement, idempotency_key="job-1")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["quota_used"], 1)

    def test_display_code_bootstrap_via_status(self) -> None:
        state = trial_license.install_state()
        state.pop("codes", None)
        trial_license.secure_store.write_private(
            trial_license.install_path(),
            json.dumps(state, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        result = cloud_license.verify(self.entitlement)
        self.assertEqual(result["status"], "active")
        refreshed = trial_license.install_state()
        self.assertEqual(refreshed["codes"][self.serial], self.grant["trial_code"])

    def test_revoked_license_fails_closed(self) -> None:
        self.admin_login()
        self.portal(
            "POST", f"/api/admin/licenses/{self.serial}/revoke", {"reason": "测试"},
        )
        with self.assertRaises(cloud_license.CloudError) as caught:
            cloud_license.verify(self.entitlement)
        self.assertEqual(caught.exception.code, "license_revoked")

    def test_network_failure_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {
            cloud_license.ENV_BASE: "http://127.0.0.1:1",
        }):
            with self.assertRaises(cloud_license.CloudError) as caught:
                cloud_license.verify(self.entitlement)
            self.assertEqual(caught.exception.code, "network")

    def test_disabled_skips(self) -> None:
        cloud_license.configure("", enable=False)
        self.assertTrue(cloud_license.preflight(self.entitlement)["skipped"])
        self.assertTrue(cloud_license.consume(self.entitlement)["skipped"])
        self.assertTrue(cloud_license.verify(self.entitlement)["skipped"])
        cloud_license.ensure_allowed()  # must not raise

    def test_watchdog_blocks_and_recovers(self) -> None:
        blocked: list[dict] = []
        with mock.patch.dict(os.environ, {
            cloud_license.ENV_BASE: "http://127.0.0.1:1",
        }):
            cloud_license.start_watchdog(
                lambda: self.entitlement, blocked.append, interval=(1, 1),
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not blocked:
                time.sleep(0.2)
            self.assertTrue(blocked, "watchdog did not block on network failure")
            self.assertTrue(cloud_license.guard_state()["blocked"])
            with self.assertRaises(cloud_license.CloudError):
                cloud_license.ensure_allowed()
        # Server reachable again: the next heartbeat clears the block.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and cloud_license.guard_state()["blocked"]:
            time.sleep(0.2)
        self.assertFalse(cloud_license.guard_state()["blocked"])
        cloud_license.ensure_allowed()


if __name__ == "__main__":
    unittest.main()