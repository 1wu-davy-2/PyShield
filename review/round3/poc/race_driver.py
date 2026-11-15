import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKER = Path(r"E:\tmp\workbuild\code\分析\round3\state_race_worker.py")
SECRET = bytes(range(32)).hex()
failures = 0
for round_no in range(1, 6):
    with tempfile.TemporaryDirectory(prefix="pyshield-race-") as td:
        env = os.environ.copy()
        env["PYSHELD_STATE_DIR"] = str(Path(td) / "primary")
        env["PYSHELD_STATE_ANCHOR_DIR"] = str(Path(td) / "anchor")
        env.pop("PYSHELD_ALLOW_VOLATILE_STATE", None)
        procs = [
            subprocess.Popen(
                [sys.executable, str(WORKER), SECRET],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for _ in range(8)
        ]
        bad = []
        for index, proc in enumerate(procs):
            out, _ = proc.communicate(timeout=30)
            if proc.returncode != 0:
                bad.append(f"#{index}: {(out or '').strip()[:200]}")
        failures += len(bad)
        print(f"round {round_no}: {8 - len(bad)}/8 ok")
        for line in bad[:2]:
            print("  ", line)
print("total failed workers:", failures, "/ 40")
