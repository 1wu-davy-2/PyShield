"""16-process clock double-write test against the fixed runtime."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
TREE = Path(sys.argv[1]).resolve()
BK = sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 16
ROUNDS = int(sys.argv[4]) if len(sys.argv) > 4 else 5
WORKER = Path(__file__).with_name("clock_worker.py")
BASE = Path(__file__).with_name("run") / "clock"
sys.path.insert(0, str(TREE))
from _shield_runtime import _core  # noqa: E402


def records(state_dir: Path):
    machine = _core._clock_machine()
    values = []
    for suffix in ("", ".anchor"):
        path = state_dir / (_core.BUILD_ID + ".state" + suffix)
        raw = _core._secure_store.read_private(path)
        record = _core._secure_store.open_state(
            raw, machine, _core.STATE_PURPOSE, bytes.fromhex(BK)
        )
        values.append(record["last_seen"])
    return values


failures_total = 0
for round_no in range(ROUNDS):
    state_dir = BASE / f"round{round_no}"
    shutil.rmtree(state_dir, ignore_errors=True)
    state_dir.mkdir(parents=True)
    env = os.environ.copy()
    env["TREE"] = str(TREE)
    env["BK"] = BK
    env["PYSHELD_STATE_DIR"] = str(state_dir)
    env.pop("PYSHELD_STATE_ANCHOR_DIR", None)
    env.pop("PYSHELD_ALLOW_LEGACY_STATE", None)
    env.pop("PYSHELD_ALLOW_VOLATILE_STATE", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
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
        out, _ = worker.communicate(timeout=90)
        results.append((index, worker.returncode, (out or "").strip()))
    elapsed = time.perf_counter() - started
    failures = [item for item in results if item[1] != 0]
    failures_total += len(failures)
    try:
        values = records(state_dir)
        state_note = f"records={values}"
    except Exception as exc:  # noqa: BLE001
        state_note = f"records_error={type(exc).__name__}: {exc}"
    leftovers = sorted(path.name for path in state_dir.glob("*.tmp"))
    print(
        json.dumps(
            {
                "round": round_no,
                "workers": WORKERS,
                "failures": len(failures),
                "elapsed_s": round(elapsed, 3),
                "tmp_leftovers": leftovers,
                "state": state_note,
                "failure_samples": [f"#{i}: {out[:240]}" for i, _, out in failures[:3]],
            },
            ensure_ascii=False,
        )
    )
print("TOTAL_FAILURES", failures_total)
raise SystemExit(0 if failures_total == 0 else 1)
