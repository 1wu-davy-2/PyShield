from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_test_support import McpProcess, make_project, wait_for_state


class McpToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.projects, self.project = make_project(self.root)
        self.process = McpProcess(self.root, self.projects, "--allow-build")
        self.process.initialize()
        self.request = {
            "project": "demo",
            "output_name": "demo-build",
            "cipher": "chacha-pure",
            "runtime": "pyc",
        }

    def tearDown(self) -> None:
        self.process.close()
        self.temp.cleanup()

    def test_validate_start_status_logs_and_verify(self) -> None:
        result, _response = self.process.call_tool(
            "pyshield_validate_build", {"request": self.request},
        )
        report = result["structuredContent"]
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["plan_hash"])
        self.assertFalse(result["isError"])

        result, response = self.process.call_tool(
            "pyshield_start_build",
            {
                "request": self.request,
                "expect_plan_hash": report["plan_hash"],
                "confirm": True,
            },
        )
        self.assertNotIn("error", response)
        started = result["structuredContent"]
        job_id = started["job_id"]
        final = wait_for_state(self.process, job_id)
        self.assertEqual(final["state"], "success", final)
        self.assertIsNotNone(final["summary"])

        result, _response = self.process.call_tool(
            "pyshield_build_logs", {"job_id": job_id, "tail_lines": 20},
        )
        self.assertTrue(result["structuredContent"]["lines"])
        result, _response = self.process.call_tool(
            "pyshield_verify_output", {"job_id": job_id},
        )
        verify = result["structuredContent"]
        self.assertTrue(verify["ok"], verify)

    def test_inspect_project_is_read_only(self) -> None:
        result, _response = self.process.call_tool(
            "pyshield_inspect_project", {"project": "demo"},
        )
        inspected = result["structuredContent"]
        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["project"], "demo")
        self.assertGreaterEqual(inspected["modules"], 2)

    def test_unknown_field_is_rejected(self) -> None:
        bad = dict(self.request)
        bad["force"] = True
        _result, response = self.process.call_tool(
            "pyshield_validate_build", {"request": bad},
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_start_requires_confirm_true(self) -> None:
        _result, response = self.process.call_tool(
            "pyshield_start_build",
            {"request": self.request, "confirm": False},
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_tools_are_strict_about_unknown_arguments(self) -> None:
        _result, response = self.process.call_tool(
            "pyshield_build_status", {"job_id": "x", "extra": 1},
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_status_of_unknown_job_returns_tool_error(self) -> None:
        result, response = self.process.call_tool(
            "pyshield_build_status", {"job_id": "missing-job"},
        )
        self.assertNotIn("error", response)
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "JOB_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
