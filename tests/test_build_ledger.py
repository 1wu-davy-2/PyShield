from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from pyshield import activation, crypto, license_admin, secure_store, tool_license

ROOT = Path(__file__).resolve().parents[1]


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.key_path, _, self.public_key = license_admin.generate_root(self.root / "keys")
        self.root_key = license_admin.load_root_key(self.key_path)
        self.patch = mock.patch.dict(os.environ, {
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
            tool_license.ENV_TRIAL_BUILDS: "3",
        })
        self.patch.start()
        os.environ.pop(tool_license.ENV_DEV, None)
        os.environ.pop(tool_license.ENV_LICENSE, None)

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_readonly_check_does_not_start_trial(self) -> None:
        status = tool_license.check(machine="machine-a", initialize_trial=False)
        self.assertTrue(status.valid)
        self.assertEqual(status.mode, "trial")
        self.assertTrue(status.trial.get("not_started"))
        self.assertFalse(tool_license.trial_path().exists())

    def test_begin_refund_and_idempotent_duplicate(self) -> None:
        lease = tool_license.begin_build(machine="machine-a", token="job-1")
        self.assertTrue(lease.charged)
        self.assertEqual(lease.mode, "trial")
        after_begin = tool_license.check(machine="machine-a")
        self.assertEqual(after_begin.trial["builds_left"], 2)

        first = tool_license.refund_build(
            lease, "PROJECT_CODE", "PROJECT_CODE_ERROR", "bad module",
        )
        self.assertTrue(first.applied)
        self.assertFalse(first.already_applied)
        self.assertEqual(tool_license.check(machine="machine-a").trial["builds_left"], 3)
        second = tool_license.refund_build(
            lease, "PROJECT_CODE", "PROJECT_CODE_ERROR", "bad module",
        )
        self.assertTrue(second.already_applied)
        self.assertEqual(tool_license.check(machine="machine-a").trial["builds_left"], 3)

        duplicate = tool_license.begin_build(machine="machine-a", token="job-1")
        self.assertTrue(duplicate.duplicate)
        self.assertFalse(duplicate.charged)

    def test_record_build_compatibility_still_charges(self) -> None:
        status = tool_license.check(machine="machine-a")
        tool_license.record_build(status)
        self.assertEqual(tool_license.check(machine="machine-a").trial["builds_left"], 2)

    def test_concurrent_begin_never_oversells(self) -> None:
        script = (
            "import json, os\n"
            "from pyshield import tool_license\n"
            "try:\n"
            "    r = tool_license.begin_build(machine='machine-a', token=os.environ['TOKEN'])\n"
            "    print(json.dumps({'ok': True, 'charged': r.charged}))\n"
            "except Exception as exc:\n"
            "    print(json.dumps({'ok': False, 'error': str(exc)}))\n"
        )
        processes = []
        env = os.environ.copy()
        env.update({
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
            tool_license.ENV_TRIAL_BUILDS: "3",
        })
        env.pop(tool_license.ENV_DEV, None)
        for index in range(8):
            child_env = dict(env)
            child_env["TOKEN"] = f"job-{index}"
            processes.append(subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=str(ROOT), env=child_env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ))
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout.strip().splitlines()[-1]))
        self.assertEqual(sum(1 for item in results if item["ok"]), 3, results)
        status = tool_license.check(machine="machine-a")
        self.assertEqual(status.trial["builds"], 3)

    def test_concurrent_refund_is_idempotent(self) -> None:
        lease = tool_license.begin_build(machine="machine-a", token="job-refund")
        script = (
            "import json, os\n"
            "from pyshield import tool_license\n"
            "r = tool_license.BuildReservation(token=os.environ['TOKEN'], mode='trial', charged=True, machine='machine-a')\n"
            "result = tool_license.refund_build(r, 'PROJECT_CODE', 'PROJECT_CODE_ERROR', 'bad module')\n"
            "print(json.dumps({'applied': result.applied, 'already': result.already_applied}))\n"
        )
        env = os.environ.copy()
        env.update({
            tool_license.ENV_HOME: str(self.home),
            tool_license.ENV_PUBLIC_KEY: crypto.b85encode(self.public_key),
            "TOKEN": lease.token,
        })
        env.pop(tool_license.ENV_DEV, None)
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(2)
        ]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout.strip().splitlines()[-1]))
        self.assertEqual(sum(1 for item in results if item["applied"] and not item["already"]), 1)
        self.assertEqual(sum(1 for item in results if item["already"]), 1)
        self.assertEqual(tool_license.check(machine="machine-a").trial["builds"], 0)

    def test_cross_month_refund_becomes_credit(self) -> None:
        document = license_admin.issue_entitlement(
            self.root_key,
            license_id="PS-CREDIT-001",
            customer="ACME",
            edition=tool_license.EDITION_PRO,
            max_builds_per_month=1,
            bind_mode=tool_license.BIND_FIXED,
            fingerprints=["machine-a"],
            activation_required=False,
            # Fixed dates below must stay inside the window no matter when the
            # suite runs; without this the entitlement defaults to the real
            # not_before and the cross-month scenario breaks after midnight.
            not_before=date(2026, 11, 1),
        )
        path = license_admin.write_document(document, self.root / "license.json")
        activation.install_license(path, machine="machine-a")

        first = tool_license.begin_build(
            machine="machine-a", token="nov", today=date(2026, 11, 15),
        )
        self.assertTrue(first.charged)
        refund = tool_license.refund_build(
            first, "PROJECT_CODE", "PROJECT_CODE_ERROR", "bad module",
            today=date(2026, 12, 1),
        )
        self.assertTrue(refund.applied)

        credit = tool_license.begin_build(
            machine="machine-a", token="dec-credit", today=date(2026, 12, 1),
        )
        self.assertTrue(credit.paid_by_credit)
        self.assertEqual(credit.after_used, 0)
        normal = tool_license.begin_build(
            machine="machine-a", token="dec-used", today=date(2026, 12, 1),
        )
        self.assertEqual(normal.after_used, 1)
        with self.assertRaises(tool_license.ToolLicenseError):
            tool_license.begin_build(
                machine="machine-a", token="dec-over", today=date(2026, 12, 1),
            )

    def test_public_status_hides_ledger_internals(self) -> None:
        status = tool_license.check(machine="machine-a")
        tool_license.record_build(status)
        again = tool_license.check(machine="machine-a")
        for key in ("pending", "refunded", "completed", "idempotency", "credits"):
            self.assertNotIn(key, again.trial)
            self.assertNotIn(key, again.to_dict()["trial"])
        payload = secure_store.open_state(
            tool_license.trial_path().read_bytes(), "machine-a", "trial",
        )
        self.assertIn("pending", payload)
        self.assertIn("refunded", payload)


if __name__ == "__main__":
    unittest.main()
