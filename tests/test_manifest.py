from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pyshield import crypto, ed25519, manifest


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "manage.py").write_text("print('ok')\n", encoding="utf-8")
        package = self.root / "mypkg"
        package.mkdir()
        (package / "views.py").write_text("VALUE = 1\n", encoding="utf-8")
        (package / "templates").mkdir()
        (package / "templates" / "page.html").write_text("<h1>hello</h1>\n", encoding="utf-8")
        runtime = self.root / "_shield_runtime"
        runtime.mkdir()
        (runtime / "_core.py").write_text("BUILD = 1\n", encoding="utf-8")
        cache = runtime / "__pycache__"
        cache.mkdir()
        (cache / "_core.cpython-312.pyc").write_bytes(b"cache")
        (self.root / "local_settings.py").write_text("DEBUG = True\n", encoding="utf-8")
        (self.root / "pyshield.lic").write_bytes(b"license")
        (self.root / "mypkg" / "data.log").write_text("log\n", encoding="utf-8")
        self.signing = crypto.random_bytes(32)
        self.public = ed25519.public_key(self.signing)
        self.code_paths = ["manage.py", "mypkg/views.py"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make(self, **kwargs) -> dict:
        options = {
            "root": self.root,
            "build_id": "0123456789abcdef",
            "cipher": "chacha",
            "python_version": "3.12",
            "signing_key": self.signing,
            "integrity": "protected",
            "code_paths": self.code_paths,
        }
        options.update(kwargs)
        return manifest.build_payload(**options)

    def test_categories_and_mutable_files(self) -> None:
        payload = self.make(mutable_patterns=["mypkg/templates/site/*"])
        files = payload["files"]
        self.assertEqual(files["manage.py"]["category"], "code")
        self.assertEqual(files["mypkg/views.py"]["category"], "code")
        self.assertEqual(files["_shield_runtime/_core.py"]["category"], "runtime")
        self.assertEqual(files["mypkg/templates/page.html"]["category"], "data")
        self.assertNotIn("local_settings.py", files)
        self.assertNotIn("pyshield.lic", files)
        self.assertNotIn("mypkg/data.log", files)
        self.assertFalse(any("__pycache__" in name for name in files))
        self.assertEqual(
            files["mypkg/views.py"]["size"],
            (self.root / "mypkg" / "views.py").stat().st_size,
        )
        self.assertEqual(len(files["mypkg/views.py"]["sha256"]), 64)

    def test_signature_round_trip(self) -> None:
        payload = self.make()
        self.assertTrue(manifest.verify_signature(payload, self.public))
        self.assertFalse(manifest.verify_signature(payload, ed25519.public_key(crypto.random_bytes(32))))

        path = self.root / manifest.MANIFEST_FILENAME
        manifest.write_manifest(path, payload)
        loaded = manifest.load_manifest(path, self.public)
        self.assertEqual(loaded["build_id"], "0123456789abcdef")

        tampered = dict(payload)
        tampered["build_id"] = "fedcba9876543210"
        path.write_text(json.dumps(tampered, indent=2, sort_keys=True), encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            manifest.load_manifest(path, self.public)

    def test_verify_files_detects_changes(self) -> None:
        payload = self.make()
        self.assertEqual(manifest.verify_files(self.root, payload), [])

        (self.root / "mypkg" / "views.py").write_text("VALUE = 2\n", encoding="utf-8")
        failures = manifest.verify_files(self.root, payload)
        self.assertEqual(len(failures), 1)
        self.assertIn("mypkg/views.py", failures[0])

        (self.root / "_shield_runtime" / "_core.py").unlink()
        failures = manifest.verify_files(self.root, payload)
        self.assertEqual(len(failures), 2)

    def test_full_mode_covers_data(self) -> None:
        payload = self.make(integrity="full")
        (self.root / "mypkg" / "templates" / "page.html").write_text("<h1>changed</h1>\n", encoding="utf-8")
        self.assertEqual(manifest.verify_files(self.root, payload), [])
        failures = manifest.verify_files(self.root, payload, full=True)
        self.assertEqual(len(failures), 1)
        self.assertIn("templates/page.html", failures[0])

    def test_safe_relative(self) -> None:
        for value in ("mypkg/views.py", "manage.py", "_shield_runtime/_core.py"):
            self.assertTrue(manifest.is_safe_relative(value), value)
        for value in ("", "../evil.py", "/etc/passwd", "mypkg\\views.py", "C:/windows/system32"):
            self.assertFalse(manifest.is_safe_relative(value), value)

    def test_rejects_other_formats(self) -> None:
        path = self.root / manifest.MANIFEST_FILENAME
        path.write_text(json.dumps({"format": 9, "files": {}}), encoding="utf-8")
        with self.assertRaisesRegex(manifest.ManifestError, "unsupported"):
            manifest.load_manifest(path, self.public)


if __name__ == "__main__":
    unittest.main()
