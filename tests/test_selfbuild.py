from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from pyshield import crypto, licensing

ROOT = Path(__file__).resolve().parents[1]


def load_selfbuild():
    path = ROOT / "tools" / "selfbuild.py"
    spec = importlib.util.spec_from_file_location("pyshield_selfbuild", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SelfBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selfbuild = load_selfbuild()

    def test_staging_excludes_runtime_template_and_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp) / "staging"
            self.selfbuild.prepare_staging(staging)

            self.assertTrue((staging / "manage.py").is_file())
            self.assertTrue((staging / "pyshield" / "builder.py").is_file())
            self.assertTrue((staging / "pyshield" / "_runtime_bundle.py").is_file())
            self.assertTrue((staging / "gui" / "main.py").is_file())
            self.assertTrue((staging / "gui" / "web" / "index.html").is_file())
            self.assertFalse((staging / "pyshield" / "runtime_template").exists())
            self.assertFalse(list(staging.rglob("*.pyc")))
            self.assertFalse(list(staging.rglob("*" + licensing.SECRET_SUFFIX)))

            imports = self.selfbuild.collect_imports(staging)
            self.assertIn("sqlite3", imports)
            self.assertIn("webview", imports)
            self.assertFalse(any(item.startswith(".") for item in imports))

    def test_protected_tree_is_stubbed_and_license_has_no_build_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = root / "staging"
            protected = root / "protected"
            state = root / "state"

            self.selfbuild.prepare_staging(staging)
            self.selfbuild.protect_project(staging, protected)
            self.selfbuild.validate_protected(protected)

            secret_path = licensing.secret_path_for(protected)
            secret = licensing.read_secret(secret_path)
            payload = licensing.open_license(
                crypto.b85decode(secret["master_key"]),
                secret["build_id"],
                (protected / "pyshield.lic").read_bytes(),
                licensing.secret_public_key(secret),
            )
            self.assertEqual(payload["format"], licensing.FORMAT)
            self.assertNotIn("build_key", payload)
            self.assertFalse((protected / "pyshield" / "runtime_template").exists())

            for path in [
                *(protected / "pyshield").rglob("*.py"),
                *(protected / "gui").rglob("*.py"),
            ]:
                if "__pycache__" in path.parts:
                    continue
                self.assertTrue(
                    path.read_text(encoding="utf-8").startswith(
                        "# PyShield protected module."
                    ),
                    path,
                )

            # The protected engine must still import and decrypt outside the
            # eventual PyInstaller archive.
            self.selfbuild.smoke_import(protected, state)
            secret_path.unlink()
            self.assertFalse(secret_path.exists())
