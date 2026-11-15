"""16-process first-run binding test against the fixed runtime."""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
TREE = Path(sys.argv[1]).resolve()
BK = sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 16
ROUNDS = int(sys.argv[4]) if len(sys.argv) > 4 else 5
WORKER = Path(__file__).with_name("binding_worker.py")
BASE = Path(__file__).with_name("run") / "binding"
sys.path.insert(0, str(TREE))
from _shield_runtime import _core  # noqa: E402


def run_workers(env):
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
    return results


def verify(binding_path: Path):
    env = os.environ.copy()
    env["TREE"] = str(TREE)
    env["BK"] = BK
    env["PYSHELD_BINDING_FILE"] = str(binding_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, str(WORKER)], cwd=str(TREE), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode, (result.stdout or result.stderr or "").strip()


def record_summary(binding_path: Path):
    rows = {}
    for label, path in (
        ("primary", binding_path),
        ("anchor", Path(str(binding_path) + ".anchor")),
    ):
        try:
            rows[label] = json.loads(path.read_text(encoding="utf-8"))
            rows[label].pop("mac", None)
        except Exception as exc:  # noqa: BLE001
            rows[label] = f"{type(exc).__name__}: {exc}"
    return rows


for round_no in range(ROUNDS):
    directory = BASE / f"round{round_no}"
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    binding = directory / "binding.json"
    env = os.environ.copy()
    env["TREE"] = str(TREE)
    env["BK"] = BK
    env["PYSHELD_BINDING_FILE"] = str(binding)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.perf_counter()
    results = run_workers(env)
    elapsed = time.perf_counter() - started
    failures = [item for item in results if item[1] != 0]
    code, output = verify(binding)
    print(
        json.dumps(
            {
                "round": round_no,
                "workers": WORKERS,
                "failures": len(failures),
                "elapsed_s": round(elapsed, 3),
                "tmp_leftovers": sorted(p.name for p in directory.glob("*.tmp")),
                "verify_after": {"returncode": code, "output": output[:300]},
                "records": record_summary(binding),
                "failure_samples": [f"#{i}: {out[:240]}" for i, _, out in failures[:4]],
            },
            ensure_ascii=False,
        )
    )

# Recovery after one of the two records was deleted; all workers write the same
# missing copy with the primary's bound_at.
for round_no in range(ROUNDS):
    directory = BASE / f"recover{round_no}"
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    binding = directory / "binding.json"
    key = _core._binding_key(bytes.fromhex(BK))
    current = _core._machine.machine_fingerprint().lower()
    bound_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _core._write_binding_record(key, binding, current, bound_at)
    env = os.environ.copy()
    env["TREE"] = str(TREE)
    env["BK"] = BK
    env["PYSHELD_BINDING_FILE"] = str(binding)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.perf_counter()
    results = run_workers(env)
    elapsed = time.perf_counter() - started
    failures = [item for item in results if item[1] != 0]
    code, output = verify(binding)
    print(
        json.dumps(
            {
                "round": f"recover{round_no}",
                "workers": WORKERS,
                "failures": len(failures),
                "elapsed_s": round(elapsed, 3),
                "tmp_leftovers": sorted(p.name for p in directory.glob("*.tmp")),
                "verify_after": {"returncode": code, "output": output[:300]},
                "records": record_summary(binding),
                "failure_samples": [f"#{i}: {out[:240]}" for i, _, out in failures[:4]],
            },
            ensure_ascii=False,
        )
    )
