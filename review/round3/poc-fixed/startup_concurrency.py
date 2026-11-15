"""16-process full startup / reload test on the challenge tree."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
TREE = Path(sys.argv[1]).resolve()
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 16
ROUNDS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
WORKER = Path(__file__).with_name("startup_worker.py")
BASE = Path(__file__).with_name("run") / "startup"


def run_round(label: str, mode: str, round_no: int):
    directory = BASE / f"{label}{round_no}"
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    env = os.environ.copy()
    env["TREE"] = str(TREE)
    env["MODE"] = mode
    env["PYSHELD_STATE_DIR"] = str(directory)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYSHELD_STATE_ANCHOR_DIR", None)
    env.pop("PYSHELD_ALLOW_LEGACY_STATE", None)
    started = time.perf_counter()
    workers = [
        subprocess.Popen(
            [sys.executable, str(WORKER)], cwd=str(TREE), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for _ in range(WORKERS)
    ]
    results = []
    for index, worker in enumerate(workers):
        out, _ = worker.communicate(timeout=120)
        results.append((index, worker.returncode, (out or "").strip()))
    elapsed = time.perf_counter() - started
    failures = [item for item in results if item[1] != 0]
    leftovers = sorted(path.name for path in directory.glob("*.tmp"))
    print(
        json.dumps(
            {
                "label": label,
                "mode": mode,
                "workers": WORKERS,
                "failures": len(failures),
                "elapsed_s": round(elapsed, 3),
                "tmp_leftovers": leftovers,
                "failure_samples": [f"#{i}: {out[:300]}" for i, _, out in failures[:4]],
                "ok_sample": results[0][2][:200] if results else "",
            },
            ensure_ascii=False,
        )
    )
    return len(failures)


total = 0
for round_no in range(ROUNDS):
    total += run_round("startup", "startup", round_no)
for round_no in range(ROUNDS):
    total += run_round("reload", "reload", round_no)
for round_no in range(ROUNDS):
    total += run_round("ttl", "ttl", round_no)
print("TOTAL_FAILURES", total)
raise SystemExit(0 if total == 0 else 1)
