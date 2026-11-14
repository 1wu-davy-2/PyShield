from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gui import core, state as state_db
from gui.backend import BuildManager


class DualStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.patch = mock.patch.dict(
            os.environ,
            {
                core.CONFIG_ENV: str(self.root / "config"),
                state_db.ENV_INSTALL_DIR: str(self.root / "install"),
            },
        )
        self.patch.start()
        state_db.clear_store_cache()
        self.user_path, self.install_path = state_db.default_paths(core.config_dir())
        self.assertIsNotNone(self.install_path)

    def tearDown(self) -> None:
        state_db.clear_store_cache()
        self.patch.stop()
        self.temp.cleanup()

    def test_legacy_json_is_migrated_and_written_to_both_copies(self) -> None:
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        core.settings_path().write_text(
            json.dumps({"cipher": "aesgcm", "output_suffix": "-legacy"}),
            encoding="utf-8",
        )
        core.history_path().write_text(
            json.dumps([{"id": "old", "status": "success"}]),
            encoding="utf-8",
        )

        settings = core.load_settings()
        self.assertEqual(settings["cipher"], "aesgcm")
        self.assertEqual(settings["output_suffix"], "-legacy")
        self.assertEqual(core.load_history(), [{"id": "old", "status": "success"}])
        self.assertTrue(self.user_path.is_file())
        self.assertTrue(self.install_path.is_file())

        core.save_settings({**settings, "cipher": "chacha-pure"})
        core.save_history([{"id": "new", "status": "success"}])
        status = core.state_status()
        self.assertFalse(status["tampered"], status)
        self.assertEqual(status["mode"], "ok")
        self.assertEqual(core.load_settings()["cipher"], "chacha-pure")
        self.assertEqual(core.load_history(), [{"id": "new", "status": "success"}])
        self.assertTrue(self.user_path.is_file())
        self.assertTrue(self.install_path.is_file())

    def test_mismatch_is_tampering_and_blocks_builds_and_writes(self) -> None:
        core.save_settings({"cipher": "aesgcm"})
        document = state_db._read_document(self.install_path)
        self.assertIsNotNone(document)
        document["settings"]["cipher"] = "chacha-pure"
        state_db._write_document(
            self.install_path,
            state_db._seal(document, state_db._generation(document) + 1),
        )

        status = core.state_status()
        self.assertTrue(status["tampered"], status)
        self.assertEqual(status["mode"], "tampered")
        self.assertTrue(status["incident_dir"])
        self.assertTrue(Path(status["incident_dir"]).is_dir())

        with self.assertRaises(state_db.StateTamperError):
            core.save_settings({"cipher": "chacha-pure"})

        manager = BuildManager()
        response = manager.start(core.BuildRequest(), core.load_settings())
        self.assertFalse(response["ok"], response)
        self.assertTrue(response["integrity"]["tampered"])
        self.assertIn("状态数据库", response["errors"][0])

        repaired = core.state_repair("user")
        self.assertFalse(repaired["tampered"], repaired)
        self.assertEqual(repaired["mode"], "ok")
        self.assertEqual(core.load_settings()["cipher"], "aesgcm")
        self.assertEqual(
            state_db._read_document(self.user_path)["meta"]["digest"],
            state_db._read_document(self.install_path)["meta"]["digest"],
        )

    def test_repair_can_choose_the_install_copy(self) -> None:
        core.save_settings({"cipher": "aesgcm"})
        document = state_db._read_document(self.user_path)
        self.assertIsNotNone(document)
        document["settings"]["cipher"] = "chacha-pure"
        state_db._write_document(
            self.user_path,
            state_db._seal(document, state_db._generation(document) + 1),
        )

        status = core.state_status()
        self.assertTrue(status["tampered"], status)
        self.assertIn("install", status["repair_sources"])

        repaired = core.state_repair("install")
        self.assertFalse(repaired["tampered"], repaired)
        self.assertEqual(core.load_settings()["cipher"], "aesgcm")
        self.assertEqual(
            state_db._read_document(self.user_path)["meta"]["digest"],
            state_db._read_document(self.install_path)["meta"]["digest"],
        )

    def test_unwritable_install_directory_uses_single_copy(self) -> None:
        blocked = self.root / "blocked"
        blocked.write_text("this is a file, not a directory", encoding="utf-8")
        with mock.patch.dict(
            os.environ, {state_db.ENV_INSTALL_DIR: str(blocked)}
        ):
            state_db.clear_store_cache()
            saved = core.save_settings({"cipher": "aesgcm"})
            self.assertEqual(saved["cipher"], "aesgcm")
            status = core.state_status()
            self.assertFalse(status["tampered"], status)
            self.assertEqual(status["mode"], "single-copy")
            self.assertFalse(status["install_available"])
            self.assertTrue(self.user_path.is_file())
            self.assertEqual(core.load_settings()["cipher"], "aesgcm")
        state_db.clear_store_cache()

    def test_missing_install_copy_is_restored(self) -> None:
        core.save_settings({"cipher": "aesgcm"})
        self.install_path.unlink()
        status = core.state_status()
        self.assertFalse(status["tampered"], status)
        self.assertEqual(status["mode"], "recovered")
        self.assertTrue(self.install_path.is_file())
        self.assertEqual(core.load_settings()["cipher"], "aesgcm")

    def test_interrupted_dual_write_recovers_from_newer_copy(self) -> None:
        core.save_settings({"cipher": "aesgcm"})
        old = state_db._read_document(self.user_path)
        self.assertIsNotNone(old)
        new = state_db._copy_document(old)
        new["settings"]["cipher"] = "chacha-pure"
        generation = state_db._generation(old) + 1
        sealed = state_db._seal(new, generation)
        state_db._write_document(self.user_path, sealed)
        marker = {
            "generation": generation,
            "digest": sealed["meta"]["digest"],
            "previous_generation": state_db._generation(old),
            "previous_digest": old["meta"]["digest"],
            "token": "test",
        }
        self.assertTrue(state_db._write_marker(self.user_path.parent, marker))
        self.assertTrue(state_db._write_marker(self.install_path.parent, marker))

        status = core.state_status()
        self.assertFalse(status["tampered"], status)
        self.assertEqual(status["mode"], "recovered")
        self.assertEqual(core.load_settings()["cipher"], "chacha-pure")
        self.assertFalse(
            (self.user_path.parent / state_db.PENDING_FILENAME).exists()
        )
        self.assertFalse(
            (self.install_path.parent / state_db.PENDING_FILENAME).exists()
        )


if __name__ == "__main__":
    unittest.main()
