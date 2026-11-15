from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pyshield import build_spec, secure_store


def make_project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "manage.py").write_text("print('ok')\n", encoding="utf-8")
    package = project / "mypkg"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "tools.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return project


class BuildSpecTests(unittest.TestCase):
    def test_output_name_rules(self) -> None:
        self.assertIsNone(build_spec.validate_output_name("demo-shielded"))
        for value in ("", ".", "..", "a/b", "a\\b", "CON", "com1.txt", "name.", "name "):
            self.assertIsNotNone(build_spec.validate_output_name(value), value)

    def test_project_relative_rules(self) -> None:
        self.assertIsNone(build_spec.validate_project_relative("demo"))
        self.assertIsNone(build_spec.validate_project_relative("apps/demo"))
        for value in ("", "../demo", "/demo", "C:\\demo", "a/../b"):
            self.assertIsNotNone(build_spec.validate_project_relative(value), value)

    def test_is_within_is_case_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.assertTrue(build_spec.is_within(root / "a" / "b", root))
            self.assertFalse(build_spec.is_within(root.parent / "outside", root))

    def test_scan_project_rejects_limits_and_flags_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            project = make_project(root)
            (project / ".env").write_text("SECRET=1\n", encoding="utf-8")
            scan = build_spec.scan_project(project, allow_sensitive=True)
            self.assertIn(".env", scan.sensitive_files)
            with self.assertRaises(build_spec.SpecError) as ctx:
                build_spec.scan_project(project, allow_sensitive=False)
            self.assertEqual(ctx.exception.code, "SENSITIVE_FILE")
            with self.assertRaises(build_spec.SpecError) as ctx:
                build_spec.scan_project(project, max_files=1, allow_sensitive=True)
            self.assertEqual(ctx.exception.code, "LIMIT_FILES")

    def test_scan_project_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            project = make_project(root)
            try:
                os.symlink(root, project / "escape")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not available")
            with self.assertRaises(build_spec.SpecError) as ctx:
                build_spec.scan_project(project, allow_sensitive=True)
            self.assertEqual(ctx.exception.code, "PATH_REPARSE")

    def test_python_syntax_check_does_not_execute(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            project = make_project(root)
            marker = root / "executed"
            (project / "mypkg" / "broken.py").write_text(
                f"open({str(marker)!r}, 'w').write('x')\nif True print('broken')\n",
                encoding="utf-8",
            )
            scan = build_spec.scan_project(project, allow_sensitive=True)
            issues = build_spec.check_python_syntax(project, scan.files)
            self.assertTrue(any(item.code == "PROJECT_SYNTAX" for item in issues))
            self.assertFalse(marker.exists())

    def test_expiry_grace_and_bind_rules(self) -> None:
        today = date(2026, 11, 15)
        self.assertIsNone(build_spec.validate_expiry_mode("days", 30, None, today))
        self.assertIsNotNone(build_spec.validate_expiry_mode("days", 0, None, today))
        self.assertIsNotNone(build_spec.validate_expiry_mode("date", None, "2026-11-14", today))
        self.assertIsNone(build_spec.validate_grace_days(0))
        self.assertIsNotNone(build_spec.validate_grace_days(-1))
        self.assertEqual(
            build_spec.validate_bind_mode("fixed", ["A" * 32]), [],
        )
        self.assertTrue(build_spec.validate_bind_mode("any", ["A" * 32]))
        self.assertTrue(build_spec.validate_patterns(["*/manage.py"], "excludes"))
        self.assertTrue(build_spec.validate_patterns(["**/pyshield.lic"], "excludes"))
        self.assertFalse(build_spec.validate_patterns(["tests/*.py"], "excludes"))

    def test_exclusive_lock_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lock"
            with secure_store.exclusive_lock(path, timeout=1):
                self.assertTrue(path.exists())
            with secure_store.exclusive_lock(path, timeout=1):
                pass


if __name__ == "__main__":
    unittest.main()
