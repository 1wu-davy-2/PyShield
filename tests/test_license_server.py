from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from pyshield import activation, crypto, license_admin, license_server, tool_license

ADMIN_TOKEN = "test-admin-token"


class LicenseServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.key_path, _, self.public_key = license_admin.generate_root(self.root / "keys")
        self.root_key = license_admin.load_root_key(self.key_path)
        self.server = license_server.create_server(
            "127.0.0.1", 0, self.root_key, self.root / "license-data", ADMIN_TOKEN,
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

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.patch.stop()
        self.temp.cleanup()

    def write_license(self, **kwargs) -> Path:
        options = {
            "license_id": "PS-SERVER-001",
            "customer": "ACME",
            "edition": tool_license.EDITION_PRO,
            "activation_required": True,
            "max_activations": 1,
            "max_builds_per_month": 10,
        }
        options.update(kwargs)
        document = license_admin.issue_entitlement(self.root_key, **options)
        return license_admin.write_document(document, self.root / "license.json")

    def post(self, path: str, payload: dict, token: str = "") -> dict:
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_activation_binds_machine_and_heartbeat_succeeds(self) -> None:
        path = self.write_license()
        entitlement = activation.activate(path, server=self.url, machine="machine-a")
        self.assertEqual(entitlement.bind_mode, tool_license.BIND_FIXED)
        self.assertEqual(entitlement.fingerprints, ("machine-a",))

        status = tool_license.check(machine="machine-a")
        self.assertTrue(status.valid, status.reason)
        payload = activation.heartbeat(
            status.entitlement, server=self.url, machine="machine-a", used_builds=3,
        )
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["used_builds"], 3)
        self.assertIsNotNone(activation.read_heartbeat())

    def test_activation_limit_is_enforced(self) -> None:
        path = self.write_license(max_activations=1)
        activation.activate(path, server=self.url, machine="machine-a")
        with self.assertRaises(activation.ActivationError) as caught:
            activation.activate(path, server=self.url, machine="machine-b")
        self.assertIn("activation limit", str(caught.exception))

    def test_heartbeat_rejects_another_machine(self) -> None:
        path = self.write_license()
        activation.activate(path, server=self.url, machine="machine-a")
        status = tool_license.check(machine="machine-a")
        with self.assertRaises(activation.ActivationError) as caught:
            activation.heartbeat(status.entitlement, server=self.url, machine="machine-b")
        self.assertIn("bound to a different machine", str(caught.exception))

    def test_revocation_endpoint_invalidates_license(self) -> None:
        path = self.write_license()
        activation.activate(path, server=self.url, machine="machine-a")
        license_admin.revoke(
            self.root_key, "PS-SERVER-001", self.server.store.revocations_path,
        )
        revoked = activation.fetch_revocations(server=self.url)
        self.assertEqual(revoked, 1)

        status = tool_license.check(machine="machine-a")
        self.assertFalse(status.valid)
        self.assertIn("revoked", status.reason)

        with self.assertRaises(activation.ActivationError):
            activation.heartbeat(
                tool_license.load_entitlement(),
                server=self.url,
                machine="machine-a",
            )

    def test_admin_issue_requires_token(self) -> None:
        payload = {
            "license_id": "PS-ADMIN-001",
            "customer": "Admin Customer",
            "edition": "enterprise",
            "expires": "30",
        }
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/v1/issue", payload)
        self.assertEqual(caught.exception.code, 400)

        document = self.post("/v1/issue", payload, token=ADMIN_TOKEN)
        self.assertEqual(document["format"], tool_license.TOOL_LICENSE_FORMAT)
        entitlement = tool_license.parse_entitlement(document["payload"])
        self.assertEqual(entitlement.license_id, "PS-ADMIN-001")
        self.assertIn(tool_license.FEATURE_CI, entitlement.features)


if __name__ == "__main__":
    unittest.main()
