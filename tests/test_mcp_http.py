"""Loopback HTTP transport and GUI MCP host tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from mcp_test_support import make_project
from pyshield.build_service import BuildPolicy, BuildService
from pyshield.mcp_server.http_transport import (
    SESSION_HEADER,
    TOKEN_HEADER,
    McpHttpServer,
    format_token,
)


class McpHttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.projects, self.project = make_project(self.root)
        policy = BuildPolicy(
            project_roots=(self.projects,),
            output_root=self.root / "outputs",
            secret_dir=self.root / "secrets",
            job_dir=self.root / "jobs",
            audit_dir=self.root / "audit",
        )
        self.service = BuildService(policy)
        self.token = format_token()
        self.server = McpHttpServer(self.service, port=0, token=self.token)
        self.port = self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.service.shutdown(grace=1)
        self.temp.cleanup()

    # -- helpers -----------------------------------------------------
    def post(self, payload, *, token: str | None = "", headers: dict | None = None):
        body = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        if token is not None:
            request_headers[TOKEN_HEADER] = token or self.token
        request_headers.update(headers or {})
        request = urllib.request.Request(
            f"{self.base}/mcp", data=body, headers=request_headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                return response.status, dict(response.headers), (
                    json.loads(raw) if raw else None
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload_out = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload_out = raw.decode("utf-8", "replace")
            return exc.code, dict(exc.headers), payload_out

    # -- protocol ----------------------------------------------------
    def test_initialize_issues_session_and_tools(self) -> None:
        status, headers, payload = self.post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        })
        self.assertEqual(status, 200)
        self.assertTrue(headers.get(SESSION_HEADER))
        self.assertEqual(payload["result"]["serverInfo"]["name"], "pyshield")
        self.assertEqual(payload["result"]["protocolVersion"], "2025-06-18")

        status, _headers, payload = self.post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        self.assertEqual(status, 200)
        names = [tool["name"] for tool in payload["result"]["tools"]]
        self.assertIn("pyshield_status", names)
        self.assertIn("pyshield_validate_build", names)

    def test_notification_returns_202(self) -> None:
        status, _headers, payload = self.post({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        self.assertEqual(status, 202)
        self.assertIsNone(payload)

    def test_batch_returns_array(self) -> None:
        status, _headers, payload = self.post([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ])
        self.assertEqual(status, 200)
        self.assertIsInstance(payload, list)
        self.assertEqual([item["id"] for item in payload], [1, 2])

    def test_requires_token(self) -> None:
        status, _headers, payload = self.post(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=None,
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

        status, _headers, payload = self.post(
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            token="", headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"], {})

    def test_ping_before_initialize(self) -> None:
        status, _headers, payload = self.post({
            "jsonrpc": "2.0", "id": 5, "method": "ping",
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"], {})

    def test_invalid_json_is_rejected(self) -> None:
        request = urllib.request.Request(
            f"{self.base}/mcp", data=b"{not json",
            headers={"Content-Type": "application/json", TOKEN_HEADER: self.token},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 400)

    def test_health_and_metadata_endpoints(self) -> None:
        with urllib.request.urlopen(f"{self.base}/health", timeout=10) as response:
            health = json.load(response)
        self.assertTrue(health["ok"])
        self.assertEqual(health["port"], self.port)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{self.base}/mcp", timeout=10)
        self.assertEqual(caught.exception.code, 405)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{self.base}/nope", timeout=10)
        self.assertEqual(caught.exception.code, 404)

    def test_delete_session(self) -> None:
        request = urllib.request.Request(
            f"{self.base}/mcp", method="DELETE",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 204)

    def test_status_counts_requests(self) -> None:
        self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        status = self.server.status()
        self.assertTrue(status["running"])
        self.assertEqual(status["requests"], 1)
        self.assertEqual(status["last_method"], "ping")


class McpHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "config"
        self.projects, self.project = make_project(self.root)
        self.env = mock.patch.dict(os.environ, {
            "PYSHELD_GUI_CONFIG_DIR": str(self.config),
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_start_status_snippets_and_stop(self) -> None:
        from gui import mcp_host

        host = mcp_host.McpHost()
        result = host.start({
            "mcp_port": 0,
            "mcp_project_root": str(self.projects),
            "mcp_allow_build": True,
        })
        try:
            self.assertTrue(result["ok"], result.get("error"))
            status = host.status()
            self.assertTrue(status["running"])
            self.assertGreater(status["port"], 0)
            self.assertIn("pyshield_start_build", status["tools"])
            self.assertTrue(status["token"])
            self.assertTrue((self.config / mcp_host.TOKEN_FILENAME).is_file())

            snippet = host.snippet("http-json")["text"]
            self.assertIn(status["url"], snippet)
            self.assertIn(status["token"], snippet)
            stdio = host.snippet("stdio")["text"]
            self.assertIn("--project-root", stdio)
            self.assertIn("--allow-build", stdio)
        finally:
            host.stop()
        self.assertFalse(host.status()["running"])

    def test_read_only_policy_hides_build_tools(self) -> None:
        from gui import mcp_host

        host = mcp_host.McpHost()
        result = host.start({
            "mcp_port": 0,
            "mcp_project_root": str(self.projects),
            "mcp_allow_build": False,
        })
        try:
            self.assertTrue(result["ok"], result.get("error"))
            tools = host.status()["tools"]
            self.assertNotIn("pyshield_start_build", tools)
            self.assertIn("pyshield_status", tools)
        finally:
            host.stop()

    def test_missing_project_root_reports_policy_error(self) -> None:
        from gui import mcp_host

        host = mcp_host.McpHost()
        result = host.start({
            "mcp_port": 0,
            "mcp_project_root": str(self.root / "missing"),
            "mcp_allow_build": False,
        })
        self.assertFalse(result["ok"])
        self.assertIn("项目根目录", result["error"])
        self.assertFalse(host.status()["running"])


if __name__ == "__main__":
    unittest.main()
