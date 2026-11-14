from __future__ import annotations

import unittest
from pathlib import Path

from pyshield import _runtime_bundle

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "pyshield" / "runtime_template" / "_shield_runtime"


class RuntimeBundleTests(unittest.TestCase):
    def test_bundle_matches_checked_in_sources(self) -> None:
        expected = {
            "__init__.py": TEMPLATE / "__init__.py",
            "__main__.py": TEMPLATE / "__main__.py",
            "_core.py": TEMPLATE / "_core.py",
            "_chacha_fast.pyx": TEMPLATE / "_chacha_fast.pyx",
            "_crypto.py": ROOT / "pyshield" / "crypto.py",
            "_machine.py": ROOT / "pyshield" / "fingerprint.py",
            "_ed25519.py": ROOT / "pyshield" / "ed25519.py",
            "_secure_store.py": ROOT / "pyshield" / "secure_store.py",
        }
        bundled = _runtime_bundle.load_files()
        self.assertEqual(set(bundled), set(expected))
        for name, path in expected.items():
            self.assertEqual(
                bundled[name], path.read_bytes(),
                f"{name} is stale; run python tools/embed_runtime.py",
            )

    def test_pyinstaller_spec_does_not_ship_plaintext_runtime(self) -> None:
        spec = (ROOT / "PyShieldBuilder.spec").read_text(encoding="utf-8")
        self.assertNotIn('"pyshield" / "runtime_template"', spec)
        self.assertNotIn('"crypto.py"', spec)
        self.assertNotIn('"fingerprint.py"', spec)
        self.assertNotIn('"ed25519.py"', spec)
        self.assertNotIn('"pyshield" / "crypto.py"', spec)
        self.assertNotIn('"pyshield" / "fingerprint.py"', spec)
        self.assertNotIn('"pyshield" / "ed25519.py"', spec)


if __name__ == "__main__":
    unittest.main()
