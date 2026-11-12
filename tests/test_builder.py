from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pyshield import builder, crypto, licensing

MANAGE = """\
import sys
from mypkg import VALUE
from mypkg.tools import double


def main():
    assert double(21) == 42, "encrypted modules must work together"
    print("fake check ok", VALUE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

INIT = "VALUE = 42\n"
TOOLS = "def double(value):\n    return value * 2\n"
BOOM = "def explode():\n    raise RuntimeError('boom')\n"


def make_project(root: Path) -> None:
    (root / "manage.py").write_text(MANAGE, encoding="utf-8")
    package = root / "mypkg"
    package.mkdir()
    (package / "__init__.py").write_text(INIT, encoding="utf-8")
    (package / "tools.py").write_text(TOOLS, encoding="utf-8")
    (package / "boom.py").write_text(BOOM, encoding="utf-8")
    (package / "tests.py").write_text("raise RuntimeError('tests are excluded')\n", encoding="utf-8")
    migrations = package / "migrations"
    migrations.mkdir()
    (migrations / "0001_initial.py").write_text("VALUE = 1\n", encoding="utf-8")
    templates = package / "templates"
    templates.mkdir()
    (templates / "page.html").write_text("<h1>hello</h1>\n", encoding="utf-8")
    (root / "db.sqlite3").write_bytes(b"database")
    (root / "old.shield-secret.json").write_text("{}", encoding="utf-8")


class BuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        make_project(self.project)
        self.output = root / "dist"
        self.secret_path = licensing.secret_path_for(self.output)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, **kwargs) -> builder.BuildResult:
        options = {
            "project": str(self.project),
            "output": str(self.output),
            "cipher": "chacha",
            "no_verify": True,
        }
        options.update(kwargs)
        return builder.build(builder.BuildOptions(**options))

    def run_project(self, *args: str, license_path: Path | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYSHELD_LICENSE"] = str(license_path or self.output / "pyshield.lic")
        return subprocess.run(
            [sys.executable, str(self.output / "manage.py"), *args],
            cwd=self.output, env=env, capture_output=True, text=True,
        )

    def test_build_layout_and_runtime(self) -> None:
        result = self.build()
        self.assertTrue(result.license.is_file())
        self.assertTrue(self.secret_path.is_file())
        secret = licensing.read_secret(self.secret_path)
        self.assertIn("signing_key", secret)
        self.assertIn("signing_public_key", secret)
        self.assertIn("PyShield protected module", (self.output / "mypkg" / "tools.py").read_text(encoding="utf-8"))
        self.assertNotIn("PyShield protected module", (self.output / "mypkg" / "tests.py").read_text(encoding="utf-8"))
        self.assertNotIn("PyShield protected module", (self.output / "mypkg" / "migrations" / "0001_initial.py").read_text(encoding="utf-8"))
        self.assertTrue((self.output / "mypkg" / "templates" / "page.html").is_file())
        self.assertFalse((self.output / "db.sqlite3").exists())
        self.assertFalse((self.output / "old.shield-secret.json").exists())
        self.assertFalse((self.output / "mypkg" / "__pycache__").exists())

        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("fake check ok 42", completed.stdout)

    def test_expired_license_and_reissue(self) -> None:
        result = self.build(expire="2000-01-01")
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("expired", completed.stderr)

        builder.issue(builder.IssueOptions(
            secret=str(result.secret), out=str(result.license), expire="2035-01-01",
        ))
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_device_binding(self) -> None:
        self.build(bind_device=["00000000000000000000000000000000"])
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("not licensed", completed.stderr)

    def test_tampered_payload(self) -> None:
        self.build()
        path = self.output / "mypkg" / "tools.py"
        text = path.read_text(encoding="utf-8")
        start = text.index('        "') + len('        "')
        path.write_text(text[:start] + ("A" if text[start] != "A" else "B") + text[start + 1:], encoding="utf-8")
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("cannot decrypt protected module mypkg.tools", completed.stderr)

    def test_forged_license_with_extracted_master_key(self) -> None:
        result = self.build(expire="2000-01-01")
        secret = licensing.read_secret(result.secret)
        master_key = crypto.b85decode(secret["master_key"])
        build_id = secret["build_id"]
        license_key = licensing.license_key(master_key, build_id)
        sealed = result.license.read_bytes()
        payload = json.loads(
            crypto.open_envelope(license_key, sealed, licensing.license_aad(build_id)).decode("utf-8")
        )
        payload["expires"] = None
        forged = crypto.seal(
            license_key,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            licensing.license_aad(build_id),
            cipher="chacha",
        )
        result.license.write_bytes(forged)

        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("signature", completed.stderr)

    def test_runtime_pyc(self) -> None:
        self.build(runtime_pyc=True)
        runtime = self.output / "_shield_runtime"
        self.assertFalse((runtime / "_core.py").exists())
        self.assertTrue((runtime / "_core.pyc").exists())
        self.assertFalse((runtime / "_chacha_fast.pyx").exists())
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_runtime_native(self) -> None:
        try:
            import Cython  # noqa: F401
            import setuptools  # noqa: F401
        except ImportError:
            self.skipTest("Cython and setuptools are not installed")
        if builder.find_mingw() is None and not shutil.which("cc") and not shutil.which("gcc"):
            self.skipTest("no C compiler is available")

        self.build(runtime_native=True, cipher="chacha-pure")
        runtime = self.output / "_shield_runtime"
        self.assertFalse((runtime / "_core.py").exists())
        self.assertFalse((runtime / "_crypto.py").exists())
        self.assertFalse((runtime / "_ed25519.py").exists())
        self.assertFalse((runtime / "_chacha_fast.pyx").exists())
        self.assertTrue(
            any(runtime.glob("_core*.pyd")) or any(runtime.glob("_core*.so")),
            "native runtime extension was not produced",
        )
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_traceback_uses_deployed_path(self) -> None:
        self.build()
        env = os.environ.copy()
        env["PYSHELD_LICENSE"] = str(self.output / "pyshield.lic")
        completed = subprocess.run(
            [sys.executable, "-c", "import mypkg.boom; mypkg.boom.explode()"],
            cwd=self.output, env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("boom", completed.stderr)
        self.assertIn(str(self.output / "mypkg" / "boom.py"), completed.stderr)

    def test_refuses_to_replace_unknown_directory(self) -> None:
        self.output.mkdir()
        (self.output / "keep.txt").write_text("user data", encoding="utf-8")
        with self.assertRaises(builder.BuildError):
            self.build()
        self.assertTrue((self.output / "keep.txt").is_file())
        builder.build(builder.BuildOptions(
            project=str(self.project), output=str(self.output), cipher="chacha", no_verify=True, force=True,
        ))
        self.assertFalse((self.output / "keep.txt").exists())
        self.assertTrue((self.output / "pyshield.lic").is_file())

    def test_dry_run_writes_nothing(self) -> None:
        result = builder.build(builder.BuildOptions(
            project=str(self.project), output=str(self.output), cipher="chacha", dry_run=True,
        ))
        self.assertEqual(result.modules, 4)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.secret_path.exists())


if __name__ == "__main__":
    unittest.main()
