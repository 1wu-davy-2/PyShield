from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from pyshield import builder, crypto, license_admin, tool_license
from pyshield.build_service import (
    BuildPolicy,
    BuildRequestV1,
    BuildService,
    ServiceError,
)


def make_project(root: Path) -> Path:
    project = root / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "manage.py").write_text("print('ok')\n", encoding="utf-8")
    package = project / "mypkg"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "tools.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8",
    )
    return project


class BuildServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = make_project(self.root)
        self.home = self.root / "home"
        self.home.mkdir()
        self.key_path, _, self.public_key = license_admin.generate_root(self.root / "keys")
        self.env_patch = mock.patch.dict(os.environ, {
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
            tool_license.ENV_TRIAL_BUILDS: "3",
        })
        self.env_patch.start()
        os.environ.pop(tool_license.ENV_DEV, None)
        os.environ.pop(tool_license.ENV_LICENSE, None)
        self.policy = BuildPolicy(
            project_roots=(self.root / "projects",),
            output_root=self.root / "out",
            secret_dir=self.root / "secrets",
            job_dir=self.root / "jobs",
            audit_dir=self.root / "audit",
            allow_build=True,
            allow_project_execution=True,
            verify_pythons={"python": Path(sys.executable)},
        )
        self.service = BuildService(self.policy)

    def tearDown(self) -> None:
        self.service.shutdown(grace=10)
        self.env_patch.stop()
        self.temp.cleanup()

    def request(self, **overrides) -> BuildRequestV1:
        values = {
            "project": "demo",
            "output_name": "demo-build",
            "cipher": "chacha-pure",
            "integrity": "protected",
            "runtime": "pyc",
        }
        values.update(overrides)
        return BuildRequestV1(**values)

    def wait_for(self, service: BuildService, job_id: str, timeout: float = 60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = service.status(job_id)
            if snapshot.state in ("success", "failed", "cancelled", "duplicate"):
                return snapshot
            time.sleep(0.02)
        self.fail(f"job {job_id} did not finish")

    def wait(self, job_id: str, timeout: float = 60.0):
        return self.wait_for(self.service, job_id, timeout)

    def test_validate_is_read_only(self) -> None:
        report = self.service.validate(self.request())
        self.assertTrue(report.ok, report.to_dict())
        self.assertTrue(report.plan_hash)
        self.assertFalse(tool_license.trial_path().exists())

    def test_build_success_and_static_verify(self) -> None:
        start = self.service.start(self.request(), confirm=True)
        final = self.wait(start.job_id)
        self.assertEqual(final.state, "success", final.to_dict())
        self.assertTrue(final.charged)
        self.assertIsNotNone(final.summary)
        self.assertEqual(final.summary.modules, 3)
        self.assertEqual(final.summary.output_name, "demo-build")
        self.assertTrue((self.root / "out" / "demo-build" / "pyshield.lic").is_file())
        secret = self.policy.secret_dir / final.job_id / "build.shield-secret.json"
        self.assertTrue(secret.is_file())
        self.assertEqual(tool_license.check().trial["builds_left"], 2)

        verify = self.service.verify_output(final.job_id)
        self.assertTrue(verify.ok, verify.to_dict())
        self.assertEqual(verify.build_id, final.summary.build_id)
        self.assertTrue(self.service.logs(final.job_id).lines)

    def test_preflight_syntax_error_does_not_charge(self) -> None:
        (self.project / "mypkg" / "broken.py").write_text(
            "if True print('broken')\n", encoding="utf-8",
        )
        report = self.service.validate(self.request())
        self.assertFalse(report.ok)
        with self.assertRaises(ServiceError) as ctx:
            self.service.start(self.request(), confirm=True)
        self.assertEqual(ctx.exception.code, "VALIDATION")
        self.assertFalse(tool_license.trial_path().exists())

    def test_project_code_error_refunds_quota(self) -> None:
        with mock.patch(
            "pyshield.builder.protect_module",
            side_effect=builder.ProjectCodeError("mypkg/broken.py:1: bad syntax"),
        ):
            start = self.service.start(self.request(), confirm=True)
            final = self.wait(start.job_id)
        self.assertEqual(final.state, "failed")
        self.assertEqual(final.failure_class, "PROJECT_CODE")
        self.assertIsNotNone(final.refund)
        self.assertTrue(final.refund.applied)
        self.assertEqual(tool_license.check().trial["builds_left"], 3)
        job_dir = self.policy.job_dir / final.job_id
        self.assertTrue((job_dir / "refund.json").is_file())
        self.assertIn("已退还", (job_dir / "refund.txt").read_text(encoding="utf-8"))
        self.assertIn("project code error", "\n".join(self.service.logs(final.job_id).lines))

    def test_environment_error_does_not_refund(self) -> None:
        with mock.patch("pyshield.builder.build", side_effect=OSError("disk full")):
            start = self.service.start(self.request(), confirm=True)
            final = self.wait(start.job_id)
        self.assertEqual(final.state, "failed")
        self.assertEqual(final.failure_class, "ENVIRONMENT")
        self.assertIsNone(final.refund)
        self.assertEqual(tool_license.check().trial["builds_left"], 2)
        self.assertFalse((self.policy.job_dir / final.job_id / "refund.json").exists())

    def test_default_build_does_not_execute_project_code(self) -> None:
        marker = self.root / "executed-marker"
        (self.project / "manage.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('x', encoding='utf-8')\n",
            encoding="utf-8",
        )
        start = self.service.start(self.request(), confirm=True)
        final = self.wait(start.job_id)
        self.assertEqual(final.state, "success", final.to_dict())
        self.assertFalse(marker.exists())

    def test_existing_output_is_rejected_without_charge(self) -> None:
        (self.root / "out" / "demo-build").mkdir(parents=True)
        report = self.service.validate(self.request())
        self.assertFalse(report.ok)
        with self.assertRaises(ServiceError):
            self.service.start(self.request(), confirm=True)
        self.assertFalse(tool_license.trial_path().exists())

    def test_project_symlink_is_rejected(self) -> None:
        try:
            os.symlink(self.root, self.project / "escape")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not available")
        report = self.service.validate(self.request())
        self.assertFalse(report.ok)
        self.assertTrue(any(item.code == "PATH_REPARSE" for item in report.issues))

    def test_idempotency_key_replay_does_not_charge_twice(self) -> None:
        request = self.request(idempotency_key="replay-key-123")
        first = self.wait(self.service.start(request, confirm=True).job_id)
        self.assertEqual(first.state, "success")
        self.assertEqual(tool_license.check().trial["builds_left"], 2)
        second = self.service.start(request, confirm=True)
        self.assertTrue(second.duplicate)
        self.assertTrue(second.replayed)
        self.assertFalse(second.charged)
        self.assertEqual(tool_license.check().trial["builds_left"], 2)

    def test_idempotency_key_with_different_parameters_conflicts(self) -> None:
        first = self.wait(self.service.start(
            self.request(idempotency_key="replay-key-456"), confirm=True,
        ).job_id)
        self.assertEqual(first.state, "success")
        with self.assertRaises(ServiceError) as ctx:
            self.service.start(
                self.request(
                    idempotency_key="replay-key-456", cipher="chacha",
                ),
                confirm=True,
            )
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(tool_license.check().trial["builds_left"], 2)

    def test_secret_material_is_not_in_responses_or_logs(self) -> None:
        start = self.service.start(self.request(), confirm=True)
        final = self.wait(start.job_id)
        secret_path = self.policy.secret_dir / final.job_id / "build.shield-secret.json"
        secret = json.loads(secret_path.read_text(encoding="utf-8"))
        job_dir = self.policy.job_dir / final.job_id
        blobs = [
            json.dumps(final.to_dict(), ensure_ascii=False),
            json.dumps(self.service.status_info(), ensure_ascii=False),
            "\n".join(self.service.logs(final.job_id, 200).lines),
            (job_dir / "result.json").read_text(encoding="utf-8"),
            (job_dir / "job.json").read_text(encoding="utf-8"),
        ]
        audit = self.policy.audit_dir / "mcp.jsonl"
        if audit.exists():
            blobs.append(audit.read_text(encoding="utf-8"))
        for key in ("master_key", "build_key", "signing_key", "signing_public_key"):
            value = str(secret[key])
            for blob in blobs:
                self.assertNotIn(value, blob, key)

    def test_check_output_can_be_disabled(self) -> None:
        service = BuildService(replace(
            self.policy, allow_project_execution=False, verify_pythons={},
        ))
        try:
            final = self.wait_for(
                service, service.start(self.request(), confirm=True).job_id,
            )
            self.assertEqual(final.state, "success")
            with self.assertRaises(ServiceError) as ctx:
                service.check_output(final.job_id, "python")
            self.assertEqual(ctx.exception.code, "EXECUTION_DISABLED")
        finally:
            service.shutdown(grace=10)

    def test_check_output_runs_in_sandbox(self) -> None:
        final = self.wait(self.service.start(self.request(), confirm=True).job_id)
        self.assertEqual(final.state, "success")
        report = self.service.check_output(final.job_id, "python")
        self.assertTrue(report.ok, report.to_dict())
        self.assertIn("ok", report.output.lower())
        checks = self.policy.job_dir / "checks" / final.job_id
        self.assertFalse(checks.exists() and any(checks.iterdir()))

    def test_plan_hash_mismatch_does_not_charge(self) -> None:
        report = self.service.validate(self.request())
        self.assertTrue(report.ok)
        with self.assertRaises(ServiceError) as ctx:
            self.service.start(
                self.request(), confirm=True, expect_plan_hash="0" * 64,
            )
        self.assertEqual(ctx.exception.code, "PLAN_MISMATCH")
        self.assertFalse(tool_license.trial_path().exists())

    def test_second_build_is_rejected_while_first_is_running(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_build(options):
            started.set()
            release.wait(10)
            raise OSError("test")

        with mock.patch("pyshield.builder.build", side_effect=slow_build):
            first = self.service.start(self.request(), confirm=True)
            self.assertTrue(started.wait(10))
            with self.assertRaises(ServiceError) as ctx:
                self.service.start(
                    self.request(output_name="second-build"), confirm=True,
                )
            self.assertEqual(ctx.exception.code, "BUILD_BUSY")
            release.set()
            final = self.wait(first.job_id)
        self.assertEqual(final.failure_class, "ENVIRONMENT")
        self.assertEqual(tool_license.check().trial["builds_left"], 2)


if __name__ == "__main__":
    unittest.main()
