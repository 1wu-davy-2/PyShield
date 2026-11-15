from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcp_test_support import McpProcess, make_project


class McpProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.projects, self.project = make_project(self.root)
        self.process = McpProcess(self.root, self.projects, "--allow-build")

    def tearDown(self) -> None:
        self.process.close()
        self.temp.cleanup()

    def test_initialize_and_tool_listing(self) -> None:
        response = self.process.initialize()
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "pyshield")
        self.assertIn("tools", result["capabilities"])
        _id, listed = self.process.request("tools/list")
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertIn("pyshield_validate_build", names)
        self.assertIn("pyshield_start_build", names)
        self.assertIn("pyshield_cancel_build", names)
        self.assertNotIn("pyshield_check_output", names)
        start = next(tool for tool in listed["result"]["tools"]
                     if tool["name"] == "pyshield_start_build")
        self.assertTrue(start["annotations"]["destructiveHint"])
        self.assertFalse(start["inputSchema"]["additionalProperties"])

    def test_request_before_initialize_is_rejected(self) -> None:
        _id, response = self.process.request("tools/list")
        self.assertEqual(response["error"]["code"], -32002)

    def test_invalid_json_and_unknown_method(self) -> None:
        self.process.initialize()
        self.process.send_raw("{not json")
        response = self.process.recv()
        self.assertEqual(response["error"]["code"], -32700)
        _id, response = self.process.request("does/not/exist")
        self.assertEqual(response["error"]["code"], -32601)

    def test_tools_are_hidden_without_allow_build(self) -> None:
        other = McpProcess(self.root, self.projects)
        try:
            other.initialize()
            _id, listed = other.request("tools/list")
            names = {tool["name"] for tool in listed["result"]["tools"]}
            self.assertNotIn("pyshield_start_build", names)
            self.assertNotIn("pyshield_cancel_build", names)
            self.assertNotIn("pyshield_check_output", names)
            self.assertIn("pyshield_validate_build", names)
        finally:
            other.close()

    def test_resources_and_prompts(self) -> None:
        self.process.initialize()
        _id, resources = self.process.request("resources/list")
        uris = {item["uri"] for item in resources["result"]["resources"]}
        self.assertIn("pyshield://status", uris)
        self.assertIn("pyshield://docs/mcp-server", uris)
        _id, prompts = self.process.request("prompts/list")
        names = {item["name"] for item in prompts["result"]["prompts"]}
        self.assertIn("protect-django-project", names)
        _id, read = self.process.request("resources/read", {"uri": "pyshield://status"})
        self.assertTrue(read["result"]["contents"][0]["text"])
        _id, prompt = self.process.request("prompts/get", {
            "name": "protect-django-project",
            "arguments": {"project": "demo", "output_name": "demo-build"},
        })
        self.assertIn("messages", prompt["result"])

    def test_eof_shuts_down_cleanly(self) -> None:
        self.process.initialize()
        assert self.process.proc.stdin is not None
        self.process.proc.stdin.close()
        self.process.proc.wait(timeout=10)
        self.assertEqual(self.process.proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
