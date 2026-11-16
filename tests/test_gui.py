from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from gui import core, instance, main as gui_main, tray
from gui.backend import Api, BuildManager
from pyshield import crypto, devsite, license_admin, licensing, tool_license
from tests.test_builder import make_project


class _FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeWindow:
    def __init__(self) -> None:
        self.events = type("Events", (), {})()
        self.events.shown = _FakeEvent()


class TrayTests(unittest.TestCase):
    def test_install_is_noop_off_windows(self) -> None:
        window = _FakeWindow()
        with mock.patch.object(tray, "sys", mock.Mock(platform="linux")):
            self.assertIsNone(tray.install(window, None))
        self.assertEqual(window.events.shown.handlers, [])

    def test_install_registers_window_events_on_windows(self) -> None:
        window = _FakeWindow()
        with mock.patch.object(tray, "sys", mock.Mock(platform="win32")):
            controller = tray.install(window, None, lambda: True)
        self.assertIsInstance(controller, tray.TrayController)
        self.assertEqual(len(window.events.shown.handlers), 1)

    def test_close_hides_until_explicit_quit(self) -> None:
        controller = tray.TrayController(_FakeWindow(), None, lambda: True)
        controller.hide_to_tray = mock.Mock(return_value=True)
        args = type("Args", (), {"Cancel": False, "CloseReason": None})()
        controller._on_form_closing(None, args)
        self.assertTrue(args.Cancel)

        controller._quitting = True
        args.Cancel = False
        controller._on_form_closing(None, args)
        self.assertFalse(args.Cancel)


class SingleInstanceTests(unittest.TestCase):
    def test_second_acquire_is_rejected_until_release(self) -> None:
        name = f"pyshield-test-{os.getpid()}-{time.time_ns()}"
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(
                os.environ,
                {
                    instance.ENV_ALLOW_MULTI: "",
                    "XDG_RUNTIME_DIR": temp,
                },
            ):
                first = instance.acquire_single_instance(name)
                self.assertIsNotNone(first)
                try:
                    self.assertIsNone(instance.acquire_single_instance(name))
                finally:
                    first.release()
                third = instance.acquire_single_instance(name)
                self.assertIsNotNone(third)
                third.release()

    def test_multi_instance_override_is_explicit(self) -> None:
        with mock.patch.dict(os.environ, {instance.ENV_ALLOW_MULTI: "1"}):
            first = instance.acquire_single_instance()
            second = instance.acquire_single_instance()
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertTrue(first.bypassed)
            self.assertTrue(second.bypassed)
            first.release()
            second.release()


class LaunchDispatchTests(unittest.TestCase):
    def test_show_message_raises_hidden_window(self) -> None:
        api = mock.Mock()
        window = mock.Mock()
        gui_main._dispatch_message(api, window, {"show": True, "link": ""})
        api._raise_window.assert_called_once_with()
        api.trial_handle_link.assert_not_called()
        window.evaluate_js.assert_not_called()

    def test_link_message_is_forwarded(self) -> None:
        api = mock.Mock()
        window = mock.Mock()
        gui_main._dispatch_message(api, window, {"show": True, "link": "pyshield://activate?serial=x"})
        api._raise_window.assert_called_once_with()
        api.trial_handle_link.assert_called_once_with("pyshield://activate?serial=x")
        window.evaluate_js.assert_called_once()


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
        native = core.BuildRequest.from_payload(self.payload(runtime="native"))
        self.assertEqual(core.validate_request(native), [])

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
            self.payload(cipher="rot13", integrity="strict", runtime="jit")
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
        self.assertEqual(settings["runtime"], core.DEFAULT_RUNTIME)
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

    def test_onboarding_flag_defaults_and_persists(self) -> None:
        settings = core.load_settings()
        self.assertFalse(settings["onboarding_seen"])

        saved = core.save_settings({**settings, "onboarding_seen": True})
        self.assertTrue(saved["onboarding_seen"])
        self.assertTrue(core.load_settings()["onboarding_seen"])

        # A partial settings save must not flip the flag back.
        core.save_settings({"cipher": "aesgcm"})
        self.assertTrue(core.load_settings()["onboarding_seen"])

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
            "os.environ",
            {
                core.CONFIG_ENV: str(self.root / "config"),
                tool_license.ENV_HOME: str(self.root / "license"),
                tool_license.ENV_DEV: "1",
            },
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
        self.assertEqual(summary["tool_mode"], "development")
        self.assertEqual(summary["tool_license"]["license_id"], "development")
        self.assertTrue(Path(summary["output"], "pyshield.lic").is_file())
        self.assertTrue(Path(summary["secret"]).is_file())
        self.assertGreater(summary["size"], 0)
        self.assertTrue(any("done:" in line for line in snapshot["logs"]))

        history = core.load_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "success")
        self.assertEqual(history[0]["cipher"], "chacha-pure")
        self.assertEqual(history[0]["output"], str(self.root / "project-shielded"))
        self.assertEqual(history[0]["tool_mode"], "development")

    def test_native_runtime_requires_paid_entitlement(self) -> None:
        status = mock.Mock()
        status.entitlement = None
        status.allows.return_value = False
        status.to_dict.return_value = {}
        request = core.BuildRequest.from_payload({
            "project": str(self.project),
            "output": str(self.root / "native-out"),
            "cipher": "chacha-pure",
            "runtime": "native",
            "no_verify": True,
        })
        with mock.patch("gui.backend.tool_license.require", return_value=status):
            response = self.manager.start(request, core.load_settings())
        self.assertFalse(response["ok"], response)
        self.assertTrue(
            any("原生运行时" in item for item in response["errors"]),
            response,
        )
        self.assertEqual(self.manager.snapshot()["status"], "idle")

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

    def test_build_consumes_trial_quota_when_started(self) -> None:
        _, _, public_key = license_admin.generate_root(self.root / "keys")
        with mock.patch.dict(
            "os.environ",
            {
                tool_license.ENV_PUBLIC_KEY: crypto.b85encode(public_key),
                tool_license.ENV_HOME: str(self.root / "trial-home"),
                tool_license.ENV_TRIAL_BUILDS: "3",
            },
        ):
            os.environ.pop(tool_license.ENV_DEV, None)
            status = tool_license.check()
            self.assertEqual(status.mode, "trial")
            self.assertEqual(status.trial["builds_left"], 3)

            request = core.BuildRequest.from_payload({
                "project": str(self.project),
                "cipher": "chacha-pure",
                "runtime": "pyc",
                "no_verify": True,
            })
            check = self.manager.check(request, core.load_settings())
            self.assertTrue(check["ok"], check)
            self.assertEqual(tool_license.check().trial["builds_left"], 3)

            start = self.manager.start(request, core.load_settings())
            self.assertTrue(start["ok"], start)
            self.assertEqual(start["license"]["mode"], "trial")
            self.assertEqual(start["license"]["trial"]["builds_left"], 2)
            self.assertEqual(tool_license.check().trial["builds_left"], 2)

            snapshot = self.wait()
            self.assertEqual(snapshot["status"], "success", snapshot["error"])
            self.assertEqual(tool_license.check().trial["builds_left"], 2)

    def test_build_rejected_when_trial_is_exhausted(self) -> None:
        _, _, public_key = license_admin.generate_root(self.root / "keys")
        with mock.patch.dict(
            "os.environ",
            {
                tool_license.ENV_PUBLIC_KEY: crypto.b85encode(public_key),
                tool_license.ENV_HOME: str(self.root / "exhausted-home"),
                tool_license.ENV_TRIAL_BUILDS: "1",
            },
        ):
            os.environ.pop(tool_license.ENV_DEV, None)
            status = tool_license.check()
            self.assertEqual(status.mode, "trial")
            tool_license.record_build(status)

            request = core.BuildRequest.from_payload({
                "project": str(self.project),
                "cipher": "chacha-pure",
                "runtime": "pyc",
                "no_verify": True,
            })
            start = self.manager.start(request, core.load_settings())
        self.assertFalse(start["ok"])
        self.assertIn("工具授权不可用", start["errors"][0])
        self.assertEqual(start["license"]["mode"], "invalid")
        self.assertEqual(self.manager.snapshot()["status"], "idle")

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
            "os.environ",
            {
                core.CONFIG_ENV: str(self.root / "config"),
                tool_license.ENV_HOME: str(self.root / "license"),
                tool_license.ENV_DEV: "1",
            },
        )
        self.patch.start()
        devsite.stop()
        self.api = Api()

    def tearDown(self) -> None:
        devsite.stop()
        self.patch.stop()
        self.temp.cleanup()

    def test_bootstrap_shape(self) -> None:
        boot = self.api.bootstrap()
        self.assertTrue(boot["ok"])
        self.assertEqual(boot["app"]["title"], core.APP_TITLE)
        self.assertIn("machine", boot)
        self.assertEqual(boot["license"]["mode"], "development")
        self.assertEqual(boot["state"]["status"], "idle")
        self.assertEqual(boot["defaults"]["cipher"], core.DEFAULT_CIPHER)
        self.assertIn("available", boot["native_runtime"])
        self.assertIn("reason", boot["native_runtime"])

    def test_inspect_defaults_output(self) -> None:
        info = self.api.inspect({"project": str(self.project)})
        self.assertTrue(info["ok"])
        self.assertEqual(info["errors"], [])
        self.assertTrue(info["project"]["valid"])
        self.assertEqual(info["project"]["modules"], 4)
        self.assertIn("mypkg", info["project"]["packages"])
        self.assertEqual(info["output"], str(self.root / "project-shielded"))
        self.assertTrue(info["secret"].endswith(".shield-secret.json"))

    def test_native_settings_fall_back_when_unavailable(self) -> None:
        with mock.patch.object(
            Api, "_native_runtime_block_reason", return_value="缺少 Cython"
        ):
            saved = self.api.save_settings({"runtime": "native"})
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["settings"]["runtime"], "pyc")
        self.assertIn("warning", saved)

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
