from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from gui import core
from gui.backend import Api, BuildManager
from pyshield import licensing
from tests.test_builder import make_project


class CoreHelperTests(unittest.TestCase):
    def test_default_output_and_secret_path(self) -> None:
        root = (Path(tempfile.gettempdir()) / "pyshield-core").resolve()
        output = core.default_output(str(root / "demo"), "-shielded")
        self.assertEqual(output.name, "demo-shielded")
        self.assertEqual(output.parent, root)
        secret = core.secret_path_for(output)
        self.assertEqual(secret.name, "demo-shielded.shield-secret.json")
        self.assertEqual(secret.parent, output.parent)

    def test_split_patterns_and_fingerprints(self) -> None:
        self.assertEqual(
            core.split_patterns("a.py, b/*.py；c.py\n a.py"),
            ["a.py", "b/*.py", "c.py"],
        )
        self.assertEqual(
            core.split_fingerprints("AABB  ccdd,eeff;0011"),
            ["aabb", "ccdd", "eeff", "0011"],
        )

    def test_is_within(self) -> None:
        self.assertTrue(core.is_within("E:/work/demo/dist", "E:/work/demo"))
        self.assertTrue(core.is_within("E:/work/demo", "E:/work/demo"))
        self.assertFalse(core.is_within("E:/work/demo2", "E:/work/demo"))

    def test_format_bytes(self) -> None:
        self.assertEqual(core.format_bytes(0), "0 B")
        self.assertEqual(core.format_bytes(2048), "2.0 KB")
        self.assertEqual(core.format_bytes(2 * 1024 * 1024), "2.0 MB")


class RequestValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        make_project(self.project)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def payload(self, **overrides) -> dict:
        data = {
            "project": str(self.project),
            "output": str(self.root / "dist"),
            "cipher": "chacha-pure",
            "runtime": "pyc",
            "integrity": "protected",
            "expiry_mode": "never",
        }
        data.update(overrides)
        return data

    def test_valid_request(self) -> None:
        request = core.BuildRequest.from_payload(self.payload())
        self.assertEqual(core.validate_request(request), [])

    def test_missing_project_and_output(self) -> None:
        request = core.BuildRequest.from_payload({})
        errors = core.validate_request(request)
        self.assertIn("请选择 Django 项目目录。", errors)
        self.assertIn("请填写输出目录。", errors)

    def test_output_inside_project_rejected(self) -> None:
        request = core.BuildRequest.from_payload(
            self.payload(output=str(self.project / "dist"))
        )
        errors = core.validate_request(request)
        self.assertTrue(any("输出目录不能与项目目录" in item for item in errors))

    def test_output_parent_missing(self) -> None:
        request = core.BuildRequest.from_payload(
            self.payload(output=str(self.root / "missing" / "dist"))
        )
        errors = core.validate_request(request)
        self.assertTrue(any("上级目录不存在" in item for item in errors))

    def test_unknown_options(self) -> None:
        request = core.BuildRequest.from_payload(
            self.payload(cipher="rot13", integrity="strict", runtime="native")
        )
        errors = core.validate_request(request)
        self.assertTrue(any("未知加密算法" in item for item in errors))
        self.assertTrue(any("未知完整性模式" in item for item in errors))
        self.assertTrue(any("未知 runtime 模式" in item for item in errors))

    def test_expiry_validation(self) -> None:
        request = core.BuildRequest.from_payload(
            self.payload(expiry_mode="days", expire_days="0")
        )
        self.assertTrue(any("有效天数" in item for item in core.validate_request(request)))
        request = core.BuildRequest.from_payload(
            self.payload(expiry_mode="date", expire_date="2000-01-01")
        )
        self.assertTrue(any("不能早于今天" in item for item in core.validate_request(request)))
        request = core.BuildRequest.from_payload(
            self.payload(expiry_mode="date", expire_date=(date.today() + timedelta(days=30)).isoformat())
        )
        self.assertEqual(core.validate_request(request), [])

    def test_fingerprint_validation(self) -> None:
        request = core.BuildRequest.from_payload(self.payload(fingerprints=["xyz"]))
        self.assertTrue(any("机器指纹格式" in item for item in core.validate_request(request)))

    def test_binding_mode_validation(self) -> None:
        request = core.BuildRequest.from_payload(self.payload(bind_mode="fixed"))
        self.assertTrue(
            any("至少填写一个机器指纹" in item for item in core.validate_request(request))
        )

        request = core.BuildRequest.from_payload(
            self.payload(bind_mode="fixed", fingerprints=["a" * 32])
        )
        self.assertEqual(request.bind_mode, "fixed")
        self.assertEqual(core.validate_request(request), [])

        request = core.BuildRequest.from_payload(
            self.payload(bind_mode="first-run", fingerprints=["a" * 32])
        )
        self.assertEqual(request.bind_mode, "first-run")
        self.assertEqual(request.fingerprints, [])
        self.assertEqual(core.validate_request(request), [])

        request = core.BuildRequest.from_payload(
            self.payload(bind_mode="unknown", fingerprints=["a" * 32])
        )
        self.assertEqual(request.bind_mode, "fixed")

    def test_expire_value(self) -> None:
        request = core.BuildRequest.from_payload(
            self.payload(expiry_mode="days", expire_days="30")
        )
        self.assertEqual(request.expire_value(), "30")
        request = core.BuildRequest.from_payload(self.payload(expiry_mode="never"))
        self.assertIsNone(request.expire_value())


class SettingsAndHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.patch = mock.patch.dict(
            "os.environ", {core.CONFIG_ENV: str(self.root / "config")}
        )
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_defaults_and_roundtrip(self) -> None:
        settings = core.load_settings()
        self.assertEqual(settings["cipher"], core.DEFAULT_CIPHER)
        self.assertEqual(settings["output_suffix"], core.DEFAULT_OUTPUT_SUFFIX)

        saved = core.save_settings({
            "cipher": "aesgcm",
            "runtime": "source",
            "output_suffix": "-prod",
            "no_verify": True,
        })
        self.assertEqual(saved["cipher"], "aesgcm")
        self.assertEqual(saved["runtime"], "source")
        self.assertEqual(core.load_settings(), saved)
        raw = json.loads(core.settings_path().read_text(encoding="utf-8"))
        self.assertEqual(raw["output_suffix"], "-prod")

    def test_invalid_values_fall_back(self) -> None:
        core.settings_path().parent.mkdir(parents=True, exist_ok=True)
        core.settings_path().write_text(
            json.dumps({"cipher": "bad", "integrity": "bad", "output_suffix": " "}),
            encoding="utf-8",
        )
        settings = core.load_settings()
        self.assertEqual(settings["cipher"], core.DEFAULT_CIPHER)
        self.assertEqual(settings["integrity"], core.DEFAULT_INTEGRITY)
        self.assertEqual(settings["output_suffix"], core.DEFAULT_OUTPUT_SUFFIX)

    def test_history_limit(self) -> None:
        entries = [{"id": str(index)} for index in range(core.HISTORY_LIMIT + 20)]
        core.save_history(entries)
        loaded = core.load_history()
        self.assertEqual(len(loaded), core.HISTORY_LIMIT)
        self.assertEqual(loaded[0]["id"], "0")


class BuildManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        make_project(self.project)
        self.patch = mock.patch.dict(
            "os.environ", {core.CONFIG_ENV: str(self.root / "config")}
        )
        self.patch.start()
        self.manager = BuildManager()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def wait(self, timeout: float = 180.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            snapshot = self.manager.snapshot()
            if snapshot["status"] in ("success", "failed", "cancelled"):
                return snapshot
            time.sleep(0.1)
        self.fail("GUI build did not finish in time")

    def test_build_succeeds_with_default_output(self) -> None:
        request = core.BuildRequest.from_payload({
            "project": str(self.project),
            "cipher": "chacha-pure",
            "runtime": "pyc",
            "no_verify": True,
        })
        start = self.manager.start(request, core.load_settings())
        self.assertTrue(start["ok"], start)

        snapshot = self.wait()
        self.assertEqual(snapshot["status"], "success", snapshot["error"])
        self.assertEqual(snapshot["percent"], 100)
        self.assertEqual(snapshot["stage"], "done")
        summary = snapshot["summary"]
        self.assertEqual(summary["modules"], 4)
        self.assertEqual(summary["cipher"], "chacha-pure")
        self.assertEqual(summary["integrity"], "protected")
        self.assertEqual(summary["output"], str(self.root / "project-shielded"))
        self.assertTrue(Path(summary["output"], "pyshield.lic").is_file())
        self.assertTrue(Path(summary["secret"]).is_file())
        self.assertGreater(summary["size"], 0)
        self.assertTrue(any("done:" in line for line in snapshot["logs"]))

        history = core.load_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "success")
        self.assertEqual(history[0]["cipher"], "chacha-pure")
        self.assertEqual(history[0]["output"], str(self.root / "project-shielded"))

    def test_build_first_run_binding(self) -> None:
        request = core.BuildRequest.from_payload({
            "project": str(self.project),
            "cipher": "chacha-pure",
            "runtime": "pyc",
            "bind_mode": "first-run",
            "no_verify": True,
        })
        start = self.manager.start(request, core.load_settings())
        self.assertTrue(start["ok"], start)

        snapshot = self.wait()
        self.assertEqual(snapshot["status"], "success", snapshot["error"])
        summary = snapshot["summary"]
        self.assertEqual(summary["binding"], "first-run")
        secret = licensing.read_secret(Path(summary["secret"]))
        self.assertEqual(secret["bind_mode"], "first-run")
        self.assertEqual(secret["fingerprints"], [])
        self.assertEqual(core.load_history()[0]["binding"], "first-run")

    def test_rejects_invalid_request(self) -> None:
        request = core.BuildRequest.from_payload({
            "project": str(self.project),
            "output": str(self.project / "dist"),
        })
        start = self.manager.start(request, core.load_settings())
        self.assertFalse(start["ok"])
        self.assertTrue(start["errors"])
        self.assertEqual(self.manager.snapshot()["status"], "idle")

    def test_cancel_without_build(self) -> None:
        result = self.manager.cancel()
        self.assertFalse(result["ok"])


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        make_project(self.project)
        self.patch = mock.patch.dict(
            "os.environ", {core.CONFIG_ENV: str(self.root / "config")}
        )
        self.patch.start()
        self.api = Api()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_bootstrap_shape(self) -> None:
        boot = self.api.bootstrap()
        self.assertTrue(boot["ok"])
        self.assertEqual(boot["app"]["title"], core.APP_TITLE)
        self.assertIn("machine", boot)
        self.assertEqual(boot["state"]["status"], "idle")
        self.assertEqual(boot["defaults"]["cipher"], core.DEFAULT_CIPHER)

    def test_inspect_defaults_output(self) -> None:
        info = self.api.inspect({"project": str(self.project)})
        self.assertTrue(info["ok"])
        self.assertEqual(info["errors"], [])
        self.assertTrue(info["project"]["valid"])
        self.assertEqual(info["project"]["modules"], 4)
        self.assertIn("mypkg", info["project"]["packages"])
        self.assertEqual(info["output"], str(self.root / "project-shielded"))
        self.assertTrue(info["secret"].endswith(".shield-secret.json"))

    def test_save_settings_and_history(self) -> None:
        saved = self.api.save_settings({"output_suffix": "-sealed"})
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["settings"]["output_suffix"], "-sealed")
        info = self.api.inspect({"project": str(self.project)})
        self.assertTrue(info["output"].endswith("project-sealed"))

        self.assertTrue(self.api.clear_history()["ok"])
        self.assertEqual(self.api.get_history()["history"], [])

    def test_reveal_missing_path(self) -> None:
        result = self.api.reveal(str(self.root / "nope"))
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
