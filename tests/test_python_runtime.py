"""Tests for the mirrored CPython downloader."""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from pyshield import python_runtime


class VersionParsingTests(unittest.TestCase):
    HTML = (
        '<a href="3.12.10/">3.12.10/</a>'
        '<a href="3.12.9/">3.12.9/</a>'
        '<a href="3.13.4/">3.13.4/</a>'
        '<a href="2.7.18/">2.7.18/</a>'
        '<a href="3.11.9/">3.11.9/</a>'
    )

    def test_keeps_newest_patch_per_minor(self) -> None:
        self.assertEqual(
            python_runtime.parse_versions(self.HTML),
            {"3.12": "3.12.10", "3.13": "3.13.4", "3.11": "3.11.9"},
        )

    def test_ignores_unrelated_links(self) -> None:
        self.assertEqual(python_runtime.parse_versions(""), {})
        self.assertEqual(python_runtime.parse_versions('<a href="3.12/">3.12/</a>'), {})

    def test_describe_marks_compatible_minor(self) -> None:
        with mock.patch.object(python_runtime, "app_minor", return_value="3.12"):
            item = python_runtime._describe("3.12.10", "3.12", "https://mirror.example/python")
        self.assertTrue(item["compatible"])
        self.assertTrue(item["recommended"])
        self.assertEqual(item["tag"], "推荐")
        self.assertTrue(item["url"].endswith("/3.12.10/python-3.12.10-embed-amd64.zip"))

    def test_describe_flags_other_minors(self) -> None:
        with mock.patch.object(python_runtime, "app_minor", return_value="3.12"):
            item = python_runtime._describe("3.11.9", "3.11", "https://mirror.example/python")
        self.assertFalse(item["compatible"])
        self.assertEqual(item["tag"], "版本不匹配")


class RuntimeRootTests(unittest.TestCase):
    def test_env_override_wins(self) -> None:
        with mock.patch.dict(os.environ, {"PYSHELD_RUNTIME_DIR": "D:/pyshield-rt"}, clear=False):
            self.assertEqual(python_runtime.runtime_root(), Path("D:/pyshield-rt"))
            self.assertEqual(
                python_runtime.target_dir("3.12.10"),
                Path("D:/pyshield-rt") / "python-3.12.10",
            )

    def test_home_fallback(self) -> None:
        with mock.patch.dict(os.environ, {"PYSHELD_HOME": "D:/home"}, clear=False):
            os.environ.pop("PYSHELD_RUNTIME_DIR", None)
            self.assertEqual(python_runtime.runtime_root(), Path("D:/home") / "runtimes")

    def test_mirrors_are_copyable(self) -> None:
        items = python_runtime.mirrors()
        items[0]["label"] = "changed"
        self.assertNotEqual(python_runtime.mirrors()[0]["label"], "changed")
        with self.assertRaises(python_runtime.InstallError):
            python_runtime.mirror_by_id("nope")


class ArchiveExtractionTests(unittest.TestCase):
    def test_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape.txt", "nope")
            with zipfile.ZipFile(archive) as bundle:
                with self.assertRaises(python_runtime.InstallError):
                    python_runtime._safe_extract(bundle, Path(temp) / "out")
            self.assertFalse((Path(temp).parent / "escape.txt").exists())

    def test_extracts_normal_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "good.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("python.exe", b"MZ")
                bundle.writestr("Lib/os.py", b"# os")
            target = Path(temp) / "out"
            with zipfile.ZipFile(archive) as bundle:
                python_runtime._safe_extract(bundle, target)
            self.assertTrue((target / "python.exe").is_file())
            self.assertTrue((target / "Lib" / "os.py").is_file())


class DjangoSpecTests(unittest.TestCase):
    def test_reads_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "requirements.txt").write_text(
                "asgiref==3.8.1\nDjango==5.1.3\nsqlparse>=0.4\n", encoding="utf-8"
            )
            self.assertEqual(python_runtime.detect_django_spec(root), "Django==5.1.3")

    def test_handles_extras_and_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "requirements.txt").write_text('Django[argon2]>=4.2,<5.0; python_version>"3.9"\n', encoding="utf-8")
            self.assertEqual(python_runtime.detect_django_spec(root), "Django>=4.2,<5.0")

    def test_falls_back_to_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "requirements.txt").write_text("flask\n", encoding="utf-8")
            self.assertEqual(python_runtime.detect_django_spec(root), "Django")


class InstallJobTests(unittest.TestCase):
    def test_starts_idle(self) -> None:
        job = python_runtime.InstallJob()
        snapshot = job.snapshot()
        self.assertEqual(snapshot["status"], "idle")
        self.assertFalse(job.running)
        self.assertEqual(snapshot["logs"], [])

    def test_rejects_concurrent_start(self) -> None:
        job = python_runtime.InstallJob()
        job.status = "running"
        with self.assertRaises(python_runtime.InstallError):
            job.start("3.12.10")

    def test_reports_failure_without_network(self) -> None:
        job = python_runtime.InstallJob()
        with mock.patch.object(
            python_runtime, "install", side_effect=python_runtime.InstallError("boom")
        ):
            job.start("3.12.10")
            for _ in range(50):
                if job.status != "running":
                    break
                job._thread.join(timeout=0.1)
        self.assertEqual(job.status, "failed")
        self.assertIn("boom", job.error)


if __name__ == "__main__":
    unittest.main()
