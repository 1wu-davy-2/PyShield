from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from mcp_test_support import McpProcess, make_project, wait_for_state


class McpExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.projects, self.project = make_project(self.root)
        self.process = McpProcess(
            self.root, self.projects,
            "--allow-build", "--allow-project-execution",
            "--verify-python", f"python={sys.executable}",
        )
        self.process.initialize()
        self.request = {
            "project": "demo", "output_name": "demo-build",
            "cipher": "chacha-pure", "runtime": "pyc",
        }

    def tearDown(self) -> None:
        self.process.close()
        self.temp.cleanup()

    def test_check_output_executes_in_a_sandbox(self) -> None:
        _id, listed = self.process.request("tools/list")
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("pyshield_check_output", names)

        result, _response = self.process.call_tool(
            "pyshield_validate_build", {"request": self.request},
        )
        plan = result["structuredContent"]["plan_hash"]
        result, _response = self.process.call_tool(
            "pyshield_start_build",
            {"request": self.request, "expect_plan_hash": plan, "confirm": True},
        )
        job_id = result["structuredContent"]["job_id"]
        final = wait_for_state(self.process, job_id)
        self.assertEqual(final["state"], "success", final)

        result, _response = self.process.call_tool(
            "pyshield_check_output",
            {"job_id": job_id, "interpreter_id": "python"},
        )
        report = result["structuredContent"]
        self.assertTrue(report["ok"], report)
        self.assertIn("ok", report["output"].lower())

        output = self.root / "out" / "demo-build"
        self.assertTrue(output.is_dir())
        self.assertFalse((output / "build.shield-secret.json").exists())
        self.assertTrue(
            (self.root / "state" / "secrets" / job_id / "build.shield-secret.json").is_file()
        )

    def test_unknown_interpreter_is_rejected(self) -> None:
        result, _response = self.process.call_tool(
            "pyshield_check_output",
            {"job_id": "missing", "interpreter_id": "not-configured"},
        )
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "INTERPRETER_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
