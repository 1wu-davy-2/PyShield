from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from pyshield import crypto, licensing

ROOT = Path(__file__).resolve().parents[1]


def load_check_release():
    path = ROOT / "tools" / "check_release.py"
    spec = importlib.util.spec_from_file_location("pyshield_check_release", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_secret(path: Path) -> dict:
    master = crypto.random_bytes(32)
    payload = {
        "format": licensing.FORMAT,
        "build_id": "0123456789abcdef",
        "master_key": crypto.b85encode(master),
        "build_key": crypto.b85encode(licensing.build_key_for(master, "0123456789abcdef")),
        "signing_key": crypto.b85encode(crypto.random_bytes(32)),
    }
    licensing.write_secret(path, payload)
    return payload


def write_release(root: Path, *, manifest: dict | None = None) -> None:
    (root / "manage.py").write_text("print('ok')\n", encoding="utf-8")
    package = root / "demo"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    runtime = root / "_shield_runtime"
    runtime.mkdir()
    (runtime / "_core.pyc").write_bytes(b"runtime")
    payload = manifest or {
        "format": 1,
        "files": {
            "manage.py": {"sha256": "0" * 64, "size": 11, "category": "code"},
            "demo/__init__.py": {"sha256": "0" * 64, "size": 0, "category": "code"},
            "_shield_runtime/_core.pyc": {"sha256": "0" * 64, "size": 7, "category": "runtime"},
        },
    }
    (root / ".pyshield-manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def zip_tree(root: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())


class CheckReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.check = load_check_release()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.release = self.root / "dist"
        self.release.mkdir()
        write_release(self.release)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_check(self, *argv: str) -> int:
        return self.check.main(list(argv))

    def test_clean_tree_passes_with_secret(self) -> None:
        secret = self.root / "dist.shield-secret.json"
        write_secret(secret)
        self.assertEqual(self.run_check(str(self.release)), 0)

    def test_pycache_directory_is_rejected(self) -> None:
        cache = self.release / "demo" / "__pycache__"
        cache.mkdir()
        (cache / "__init__.cpython-312.pyc").write_bytes(b"forged")
        secret = self.root / "dist.shield-secret.json"
        write_secret(secret)
        self.assertEqual(self.run_check(str(self.release)), 1)

    def test_pycache_zip_is_rejected(self) -> None:
        cache = self.release / "demo" / "__pycache__"
        cache.mkdir()
        (cache / "__init__.cpython-312.pyc").write_bytes(b"forged")
        archive = self.root / "dist.zip"
        zip_tree(self.release, archive)
        secret = self.root / "dist.shield-secret.json"
        write_secret(secret)
        self.assertEqual(self.run_check(str(archive)), 1)

    def test_signed_runtime_bytecode_is_allowed(self) -> None:
        secret = self.root / "dist.shield-secret.json"
        write_secret(secret)
        self.assertEqual(self.run_check(str(self.release)), 0)
        self.assertTrue((self.release / "_shield_runtime" / "_core.pyc").is_file())

    def test_unlisted_bytecode_file_is_rejected(self) -> None:
        (self.release / "demo" / "extra.pyc").write_bytes(b"forged")
        secret = self.root / "dist.shield-secret.json"
        write_secret(secret)
        self.assertEqual(self.run_check(str(self.release)), 1)

    def test_missing_needles_fail_closed(self) -> None:
        self.assertEqual(self.run_check(str(self.release)), 1)

    def test_skip_key_scan_allows_unkeyed_artifact(self) -> None:
        self.assertEqual(self.run_check("--skip-key-scan", str(self.release)), 0)

    def test_explicit_secret_detects_planted_key(self) -> None:
        secret_path = self.root / "build.shield-secret.json"
        payload = write_secret(secret_path)
        master = crypto.b85decode(payload["master_key"])
        (self.release / "notes.txt").write_text(master.hex() + "\n", encoding="utf-8")
        self.assertEqual(self.run_check("--secret", str(secret_path), str(self.release)), 1)

    def test_temp_scan_detects_planted_key(self) -> None:
        secret_path = self.root / "build.shield-secret.json"
        payload = write_secret(secret_path)
        master = crypto.b85decode(payload["master_key"])
        fake_temp = self.root / "temp"
        leftover = fake_temp / "pyshield-check-gap-test"
        leftover.mkdir(parents=True)
        (leftover / "master.txt").write_text(master.hex() + "\n", encoding="utf-8")
        with mock.patch.object(self.check.tempfile, "gettempdir", return_value=str(fake_temp)):
            self.assertEqual(self.run_check("--temp", "--secret", str(secret_path)), 1)

    def test_temp_scan_without_needles_fails_closed(self) -> None:
        fake_temp = self.root / "temp"
        fake_temp.mkdir()
        with mock.patch.object(self.check.tempfile, "gettempdir", return_value=str(fake_temp)):
            self.assertEqual(self.run_check("--temp"), 1)
            self.assertEqual(self.run_check("--temp", "--skip-key-scan"), 0)


if __name__ == "__main__":
    unittest.main()
