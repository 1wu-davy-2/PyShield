from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from pyshield import builder, crypto, fingerprint, licensing, secure_store

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
    (package / "local_settings.py").write_text('SECRET_KEY = "site"\n', encoding="utf-8")
    (package / "site.ini").write_text("mode = demo\n", encoding="utf-8")
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
        self.state_dir = root / "state"
        self.state_dir.mkdir()

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
        env["PYSHELD_STATE_DIR"] = str(self.state_dir)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(self.output / "manage.py"), *args],
            cwd=self.output, env=env, capture_output=True, text=True,
        )

    def assert_master_key_not_exposed(self, master_key: bytes) -> None:
        encoded = crypto.b85encode(master_key).encode("ascii")
        runtime = self.output / "_shield_runtime"
        for path in runtime.rglob("*"):
            if not path.is_file():
                continue
            data = path.read_bytes()
            self.assertNotIn(encoded, data, f"base85 master key in {path}")
            self.assertNotIn(master_key, data, f"raw master key in {path}")

    def run_runtime(self, *args: str, license_path: Path | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYSHELD_LICENSE"] = str(license_path or self.output / "pyshield.lic")
        env["PYSHELD_STATE_DIR"] = str(self.state_dir)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "_shield_runtime", *args],
            cwd=self.output, env=env, capture_output=True, text=True,
        )

    def test_build_layout_and_runtime(self) -> None:
        result = self.build()
        self.assertTrue(result.license.is_file())
        self.assertTrue(self.secret_path.is_file())
        secret = licensing.read_secret(self.secret_path)
        self.assertIn("signing_key", secret)
        self.assertIn("signing_public_key", secret)
        self.assertEqual(secret["format"], licensing.FORMAT)
        self.assertEqual(secret["license_format"], licensing.FORMAT)
        self.assertEqual(secret["integrity"], "protected")
        self.assertEqual(secret["grace_days"], 0)
        manifest_path = self.output / builder.MANIFEST_FILE
        self.assertTrue(manifest_path.is_file())
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = payload["files"]
        self.assertEqual(files["mypkg/tools.py"]["category"], "code")
        self.assertEqual(files["_shield_runtime/_core.pyc"]["category"], "runtime")
        self.assertEqual(files["_shield_runtime/_keyring.pyc"]["category"], "runtime")
        self.assertNotIn("_shield_runtime/_core.py", files)
        self.assertFalse((self.output / "_shield_runtime" / "_core.py").exists())
        self.assertFalse((self.output / "_shield_runtime" / "_keyring.py").exists())
        self.assertFalse((self.output / "_shield_runtime" / "_chacha_fast.pyx").exists())
        self.assert_master_key_not_exposed(crypto.b85decode(secret["master_key"]))
        self.assertEqual(files["mypkg/templates/page.html"]["category"], "data")
        self.assertNotIn("mypkg/local_settings.py", files)
        self.assertNotIn("pyshield.lic", files)
        self.assertEqual(files["mypkg/tests.py"]["category"], "data")
        self.assertEqual(files["mypkg/migrations/0001_initial.py"]["category"], "data")
        self.assertIn('SECRET_KEY = "site"', (self.output / "mypkg" / "local_settings.py").read_text(encoding="utf-8"))
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

    def test_first_run_binding(self) -> None:
        result = self.build(bind_first_run=True)
        secret = licensing.read_secret(result.secret)
        self.assertEqual(secret["bind_mode"], "first-run")
        self.assertEqual(secret["fingerprints"], [])
        binding_path = self.output / licensing.BINDING_FILE_NAME
        self.assertFalse(binding_path.exists())

        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(binding_path.read_text(encoding="utf-8"))
        self.assertEqual(record["build_id"], secret["build_id"])
        self.assertEqual(record["fingerprint"], fingerprint.machine_fingerprint())
        anchor_path = self.output / "_shield_runtime" / (licensing.BINDING_FILE_NAME + ".anchor")
        self.assertTrue(anchor_path.is_file())
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        self.assertEqual(anchor["bound_at"], record["bound_at"])

        # Deleting only the visible record must restore it from the anchor
        # instead of treating the deployment as a first run.
        binding_path.unlink()
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(binding_path.read_text(encoding="utf-8"))["bound_at"],
            record["bound_at"],
        )

        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        completed = self.run_runtime("status", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        status = json.loads(completed.stdout)
        self.assertEqual(status["bind_mode"], "first-run")
        self.assertEqual(status["bound_fingerprint"], fingerprint.machine_fingerprint())

        completed = self.run_runtime("fingerprint", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["fingerprint"], fingerprint.machine_fingerprint()
        )

    def test_first_run_binding_rejects_other_machine(self) -> None:
        result = self.build(bind_first_run=True)
        secret = licensing.read_secret(result.secret)
        build_id = secret["build_id"]
        key = crypto.hkdf_sha256(
            crypto.b85decode(secret["build_key"]), salt=b"pyshield-binding-v1",
            info=build_id.encode("utf-8"), length=32,
        )
        bound_at = "2026-01-01T00:00:00+00:00"
        other = "0" * 32
        mac = hmac.new(
            key, f"{build_id}|{other}|{bound_at}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        (self.output / licensing.BINDING_FILE_NAME).write_text(json.dumps({
            "format": 1,
            "build_id": build_id,
            "fingerprint": other,
            "bound_at": bound_at,
            "mac": mac,
        }), encoding="utf-8")
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("bound to machine", completed.stderr)

    def test_first_run_binding_record_is_tamper_evident(self) -> None:
        self.build(bind_first_run=True)
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        path = self.output / licensing.BINDING_FILE_NAME
        record = json.loads(path.read_text(encoding="utf-8"))
        record["fingerprint"] = "0" * 32
        path.write_text(json.dumps(record), encoding="utf-8")
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("was modified", completed.stderr)

    def test_build_verification_does_not_consume_first_run_binding(self) -> None:
        previous = os.environ.get("PYSHELD_STATE_DIR")
        os.environ["PYSHELD_STATE_DIR"] = str(self.state_dir)
        try:
            self.build(bind_first_run=True, no_verify=False)
        finally:
            if previous is None:
                os.environ.pop("PYSHELD_STATE_DIR", None)
            else:
                os.environ["PYSHELD_STATE_DIR"] = previous
        self.assertFalse((self.output / licensing.BINDING_FILE_NAME).exists())
        self.assertFalse(
            (self.output / "_shield_runtime" / (licensing.BINDING_FILE_NAME + ".anchor")).exists()
        )
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_issue_preserves_first_run_binding_and_expiry(self) -> None:
        result = self.build(
            expire="2035-01-01", customer="ACME", bind_first_run=True,
        )
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        builder.issue(builder.IssueOptions(secret=str(result.secret)))
        secret = licensing.read_secret(result.secret)
        payload = licensing.open_license(
            crypto.b85decode(secret["master_key"]), secret["build_id"],
            result.license.read_bytes(), licensing.secret_public_key(secret),
        )
        self.assertEqual(payload["expires"], "2035-01-01")
        self.assertEqual(payload["bind_mode"], "first-run")
        self.assertEqual(payload["customer"], "ACME")

        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_issue_preserves_fixed_binding(self) -> None:
        local = fingerprint.machine_fingerprint()
        result = self.build(bind_device=[local], expire="2035-01-01")
        builder.issue(builder.IssueOptions(secret=str(result.secret), expire="2036-01-01"))
        secret = licensing.read_secret(result.secret)
        payload = licensing.open_license(
            crypto.b85decode(secret["master_key"]), secret["build_id"],
            result.license.read_bytes(), licensing.secret_public_key(secret),
        )
        self.assertEqual(payload["bind_mode"], "fixed")
        self.assertEqual(payload["fingerprints"], [local])
        self.assertEqual(payload["expires"], "2036-01-01")

    def test_tampered_payload(self) -> None:
        self.build()
        path = self.output / "mypkg" / "tools.py"
        text = path.read_text(encoding="utf-8")
        start = text.index('        "') + len('        "')
        path.write_text(text[:start] + ("A" if text[start] != "A" else "B") + text[start + 1:], encoding="utf-8")
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("package integrity check failed", completed.stderr)
        self.assertIn("mypkg/tools.py", completed.stderr)

    def test_data_changes_allowed_by_default(self) -> None:
        self.build()
        (self.output / "mypkg" / "templates" / "page.html").write_text("<h1>edited</h1>\n", encoding="utf-8")
        (self.output / "mypkg" / "local_settings.py").write_text('SECRET_KEY = "changed"\n', encoding="utf-8")
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_full_integrity_detects_data_change(self) -> None:
        self.build(integrity="full")
        (self.output / "mypkg" / "templates" / "page.html").write_text("<h1>edited</h1>\n", encoding="utf-8")
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("package integrity check failed", completed.stderr)
        self.assertIn("templates/page.html", completed.stderr)

    def test_integrity_off(self) -> None:
        self.build(integrity="off")
        self.assertFalse((self.output / builder.MANIFEST_FILE).exists())
        self.assertIsNone(licensing.read_secret(self.secret_path)["manifest"])
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        path = self.output / "mypkg" / "tools.py"
        text = path.read_text(encoding="utf-8")
        start = text.index('        "') + len('        "')
        path.write_text(text[:start] + ("A" if text[start] != "A" else "B") + text[start + 1:], encoding="utf-8")
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("cannot decrypt protected module mypkg.tools", completed.stderr)

    def test_mutable_patterns_excluded_from_manifest(self) -> None:
        self.build(integrity="full", mutable_patterns=["mypkg/site.ini"])
        payload = json.loads((self.output / builder.MANIFEST_FILE).read_text(encoding="utf-8"))
        self.assertNotIn("mypkg/site.ini", payload["files"])
        (self.output / "mypkg" / "site.ini").write_text("mode = prod\n", encoding="utf-8")
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_manifest_removed_detected(self) -> None:
        self.build()
        (self.output / builder.MANIFEST_FILE).unlink()
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("package manifest", completed.stderr)

    def test_verify_command(self) -> None:
        result = self.build(integrity="full")
        report = builder.verify(builder.VerifyOptions(
            dist=str(self.output), secret=str(result.secret),
        ))
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["integrity"], "full")
        payload = json.loads((self.output / builder.MANIFEST_FILE).read_text(encoding="utf-8"))
        self.assertEqual(report["manifest"]["files"], len(payload["files"]))
        self.assertGreaterEqual(report["manifest"]["files"], 10)
        self.assertEqual(report["license"]["expires"], None)
        self.assertEqual(report["license"]["fingerprints"], [])

        (self.output / "mypkg" / "templates" / "page.html").write_text("<h1>edited</h1>\n", encoding="utf-8")
        report = builder.verify(builder.VerifyOptions(
            dist=str(self.output), secret=str(result.secret),
        ))
        self.assertFalse(report["ok"])
        self.assertTrue(any("templates/page.html" in item for item in report["failures"]))

    def test_runtime_verify_and_status(self) -> None:
        self.build(expire="2035-01-01", customer="ACME")
        completed = self.run_runtime("verify", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["ok"])
        self.assertGreater(report["checked"], 0)

        completed = self.run_runtime("status", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        status = json.loads(completed.stdout)
        self.assertEqual(status["expires"], "2035-01-01")
        self.assertEqual(status["customer"], "ACME")
        self.assertEqual(status["integrity"], "protected")
        self.assertTrue(status["last_seen"])

        (self.output / "mypkg" / "templates" / "page.html").write_text("<h1>edited</h1>\n", encoding="utf-8")
        completed = self.run_runtime("verify", "--full")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("templates/page.html", completed.stdout)

    def test_grace_period(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.build(expire=yesterday, grace_days=5)
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        completed = self.run_runtime("status", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        status = json.loads(completed.stdout)
        self.assertTrue(status["in_grace"])
        self.assertEqual(status["grace_days"], 5)

        self.build(expire=yesterday, grace_days=0)
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("expired", completed.stderr)

    def test_clock_rollback_detected(self) -> None:
        result = self.build(expire="2035-01-01")
        secret = licensing.read_secret(result.secret)
        build_id = secret["build_id"]
        last_seen = (date.today() + timedelta(days=5)).isoformat()
        payload = {
            "format": 2,
            "build_id": build_id,
            "last_seen": last_seen,
        }
        sealed = secure_store.seal_state(
            payload, fingerprint.machine_fingerprint(), "pyshield-clock"
        )
        secure_store.write_private(self.state_dir / (build_id + ".state"), sealed)
        completed = self.run_project("check")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("clock rollback", completed.stderr)

    def test_hot_reload_after_renewal(self) -> None:
        result = self.build(expire="2000-01-01")
        renewed = Path(self.temp.name) / "renewed.lic"
        builder.issue(builder.IssueOptions(
            secret=str(result.secret), out=str(renewed), expire="2035-01-01",
        ))

        script = (
            "import os\n"
            "import _shield_runtime\n"
            "first = None\n"
            "try:\n"
            "    _shield_runtime.license_status()\n"
            "except _shield_runtime.LicenseError as exc:\n"
            "    first = str(exc)\n"
            "open('pyshield.lic', 'wb').write(bytes.fromhex(os.environ['PYSHELD_NEW_LICENSE']))\n"
            "print(first)\n"
            "print(_shield_runtime.license_status(refresh=True)['expires'])\n"
        )
        env = os.environ.copy()
        env["PYSHELD_STATE_DIR"] = str(self.state_dir)
        env["PYSHELD_NEW_LICENSE"] = renewed.read_bytes().hex()
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.output, env=env, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("expired", completed.stdout)
        self.assertIn("2035-01-01", completed.stdout)

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
        self.assertTrue((runtime / "_keyring.pyc").exists())
        self.assertFalse((runtime / "_keyring.py").exists())
        self.assertFalse((runtime / "_chacha_fast.pyx").exists())
        payload = json.loads((self.output / builder.MANIFEST_FILE).read_text(encoding="utf-8"))
        runtime_files = [
            name for name, info in payload["files"].items() if info["category"] == "runtime"
        ]
        code_files = [
            name for name, info in payload["files"].items() if info["category"] == "code"
        ]
        self.assertIn("_shield_runtime/_core.pyc", runtime_files)
        self.assertIn("_shield_runtime/__main__.pyc", runtime_files)
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        completed = self.run_runtime("verify", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["checked"], len(runtime_files) + len(code_files))

    def test_runtime_source_is_opt_in(self) -> None:
        self.build(runtime_source=True)
        runtime = self.output / "_shield_runtime"
        self.assertTrue((runtime / "_core.py").exists())
        self.assertTrue((runtime / "_keyring.py").exists())
        self.assertFalse((runtime / "_core.pyc").exists())
        self.assertFalse((runtime / "_chacha_fast.pyx").exists())
        self.assert_master_key_not_exposed(
            crypto.b85decode(licensing.read_secret(self.secret_path)["master_key"])
        )
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_license_does_not_carry_build_key(self) -> None:
        result = self.build()
        secret = licensing.read_secret(result.secret)
        payload = licensing.open_license(
            crypto.b85decode(secret["master_key"]), secret["build_id"],
            result.license.read_bytes(), licensing.secret_public_key(secret),
        )
        self.assertEqual(payload["format"], licensing.FORMAT)
        self.assertNotIn("build_key", payload)
        # The module key is derivable by the vendor-side secret and by the
        # runtime from the embedded master key, but is not in the license.
        self.assertEqual(
            crypto.b85decode(secret["build_key"]),
            licensing.build_key_for(crypto.b85decode(secret["master_key"]), secret["build_id"]),
        )

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
        self.assertFalse((runtime / "_keyring.py").exists())
        self.assertFalse((runtime / "_chacha_fast.pyx").exists())
        self.assertTrue(
            any(runtime.glob("_core*.pyd")) or any(runtime.glob("_core*.so")),
            "native runtime extension was not produced",
        )
        self.assertTrue(
            any(runtime.glob("_keyring*.pyd")) or any(runtime.glob("_keyring*.so")),
            "native keyring extension was not produced",
        )
        self.assertFalse(list(runtime.glob("*.pdb")))
        self.assert_master_key_not_exposed(
            crypto.b85decode(licensing.read_secret(self.secret_path)["master_key"])
        )
        payload = json.loads((self.output / builder.MANIFEST_FILE).read_text(encoding="utf-8"))
        runtime_files = [
            name for name, info in payload["files"].items() if info["category"] == "runtime"
        ]
        self.assertTrue(any(name.endswith((".pyd", ".so")) for name in runtime_files))
        completed = self.run_project("check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        completed = self.run_runtime("verify", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_traceback_uses_deployed_path(self) -> None:
        self.build()
        env = os.environ.copy()
        env["PYSHELD_LICENSE"] = str(self.output / "pyshield.lic")
        env["PYSHELD_STATE_DIR"] = str(self.state_dir)
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


class P0HardeningTests(unittest.TestCase):
    def test_keyring_roundtrip_and_masking(self) -> None:
        master = bytes(range(32))
        source = builder.render_keyring(master)
        self.assertNotIn(crypto.b85encode(master), source)
        self.assertNotIn(master.hex(), source)
        namespace: dict = {}
        exec(compile(source, "<keyring>", "exec"), namespace)
        self.assertEqual(namespace["master_key"](), master)

    def test_master_key_audit_rejects_exposed_key(self) -> None:
        master = bytes(range(32))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "runtime.bin").write_bytes(
                b"prefix" + crypto.b85encode(master).encode("ascii")
            )
            with self.assertRaises(builder.BuildError):
                builder.assert_no_master_key(root, master)

    def test_pe_debug_directory_is_cleared(self) -> None:
        data = bytearray(0x400)
        data[0:2] = b"MZ"
        data[0x3C:0x40] = (0x80).to_bytes(4, "little")
        data[0x80:0x84] = b"PE\0\0"
        coff = 0x84
        data[coff + 2:coff + 4] = (1).to_bytes(2, "little")
        data[coff + 16:coff + 18] = (0xE0).to_bytes(2, "little")
        optional = coff + 20
        data[optional:optional + 2] = (0x10B).to_bytes(2, "little")
        data[optional + 92:optional + 96] = (16).to_bytes(4, "little")
        entry = optional + 96 + 6 * 8
        data[entry:entry + 4] = (0x1000).to_bytes(4, "little")
        data[entry + 4:entry + 8] = (28).to_bytes(4, "little")
        section = optional + 0xE0
        data[section + 8:section + 12] = (0x180).to_bytes(4, "little")
        data[section + 12:section + 16] = (0x1000).to_bytes(4, "little")
        data[section + 16:section + 20] = (0x200).to_bytes(4, "little")
        data[section + 20:section + 24] = (0x200).to_bytes(4, "little")
        data[0x200:0x21C] = b"CodeView" + b"\0" * 20

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.pyd"
            path.write_bytes(data)
            self.assertTrue(builder._scrub_pe_debug_directory(path))
            cleaned = path.read_bytes()
            self.assertEqual(cleaned[entry:entry + 8], b"\0" * 8)
            self.assertEqual(cleaned[0x200:0x21C], b"\0" * 28)
            self.assertEqual(cleaned[:2], b"MZ")
            self.assertEqual(cleaned[0x80:0x84], b"PE\0\0")


if __name__ == "__main__":
    unittest.main()
