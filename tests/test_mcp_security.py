from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp_test_support import McpProcess, make_project, wait_for_state
from pyshield import crypto, license_admin, tool_license
from pyshield.build_service import BuildPolicy, BuildService
from pyshield.mcp_server.protocol import JsonRpcServer


class McpSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.projects, self.project = make_project(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_secret_never_returned_by_tools_or_logs(self) -> None:
        process = McpProcess(self.root, self.projects, "--allow-build")
        try:
            process.initialize()
            request = {
                "project": "demo", "output_name": "demo-build",
                "cipher": "chacha-pure", "runtime": "pyc",
            }
            result, _response = process.call_tool(
                "pyshield_validate_build", {"request": request},
            )
            plan = result["structuredContent"]["plan_hash"]
            result, _response = process.call_tool(
                "pyshield_start_build",
                {"request": request, "expect_plan_hash": plan, "confirm": True},
            )
            job_id = result["structuredContent"]["job_id"]
            final = wait_for_state(process, job_id)
            self.assertEqual(final["state"], "success", final)

            secret_path = (
                self.root / "state" / "secrets" / job_id / "build.shield-secret.json"
            )
            secret = json.loads(secret_path.read_text(encoding="utf-8"))
            blobs = [json.dumps(final)]
            for tool, arguments in (
                ("pyshield_status", {}),
                ("pyshield_build_logs", {"job_id": job_id}),
                ("pyshield_verify_output", {"job_id": job_id}),
            ):
                result, _response = process.call_tool(tool, arguments)
                blobs.append(json.dumps(result, ensure_ascii=False))
            audit = self.root / "state" / "audit" / "mcp.jsonl"
            if audit.exists():
                blobs.append(audit.read_text(encoding="utf-8"))
            for key in ("master_key", "build_key", "signing_key", "signing_public_key"):
                value = str(secret[key])
                for blob in blobs:
                    self.assertNotIn(value, blob, key)
        finally:
            process.close()

    def test_server_never_creates_a_socket(self) -> None:
        with mock.patch.dict(os.environ, {"PYSHELD_DEV": "1"}):
            policy = BuildPolicy(
                project_roots=(self.projects,),
                output_root=self.root / "out",
                secret_dir=self.root / "secrets",
                job_dir=self.root / "jobs",
                audit_dir=self.root / "audit",
            )
            service = BuildService(policy)
        stdin = io.BytesIO(
            (json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            }) + "\n" + json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
            }) + "\n").encode("utf-8")
        )
        stdout = io.BytesIO()
        server = JsonRpcServer(
            service, stdin=stdin, stdout=stdout, stderr=io.StringIO(),
        )
        with mock.patch("socket.socket", side_effect=AssertionError("network used")):
            self.assertEqual(server.serve(), 0)
        service.shutdown(grace=1)
        lines = stdout.getvalue().decode("utf-8").splitlines()
        self.assertGreaterEqual(len(lines), 2)
        for line in lines:
            json.loads(line)


if __name__ == "__main__":
    unittest.main()
