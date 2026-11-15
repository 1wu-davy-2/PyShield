"""16-process end-to-end first-run binding startup test."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).with_name("run") / "firstrun"
DIST = BASE / "dist"
MANAGE = DIST / "manage.py"
BINDING = DIST / ".pyshield-binding.json"
ANCHOR = DIST / "_shield_runtime" / ".pyshield-binding.json.anchor"
WORKERS = 16
ROUNDS = 5


def clean_binding():
    for path in (BINDING, ANCHOR, BINDING.with_name(BINDING.name + ".tmp"),
                 ANCHOR.with_name(ANCHOR.name + ".tmp")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def run_workers(state_dir: Path):
    env = os.environ.copy()
    env["PYSHELD_LICENSE"] = str(DIST / "pyshield.lic")
    env["PYSHELD_STATE_DIR"] = str(state_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in ("PYSHELD_BINDING_FILE", "PYSHELD_STATE_ANCHOR_DIR",
                "PYSHELD_ALLOW_LEGACY_STATE", "PYSHELD_ALLOW_VOLATILE_STATE",
                "PYSHELD_ALLOW_MULTI_INSTANCE"):
        env.pop(key, None)
    workers = [
        subprocess.Popen(
            [sys.executable, str(MANAGE)], cwd=str(DIST), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for _ in range(WORKERS)
    ]
    results = []
    for index, worker in enumerate(workers):
        out, _ = worker.communicate(timeout=120)
        results.append((index, worker.returncode, (out or "").strip()))
    return results


def verify_now(state_dir: Path):
    env = os.environ.copy()
    env["PYSHELD_LICENSE"] = str(DIST / "pyshield.lic")
    env["PYSHELD_STATE_DIR"] = str(state_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYSHELD_BINDING_FILE", None)
    result = subprocess.run(
        [sys.executable, str(MANAGE)], cwd=str(DIST), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode, (result.stdout or result.stderr or "").strip()[-500:]


def binding_summary():
    summary = {}
    for label, path in (("primary", BINDING), ("anchor", ANCHOR)):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            summary[label] = {key: record.get(key) for key in ("bound_at", "fingerprint", "build_id")}
        except Exception as exc:  # noqa: BLE001
            summary[label] = f"{type(exc).__name__}: {exc}"
    return summary


total = 0
for round_no in range(ROUNDS):
    clean_binding()
    state_dir = BASE / f"state{round_no}"
    shutil.rmtree(state_dir, ignore_errors=True)
    state_dir.mkdir(parents=True)
    started = time.perf_counter()
    results = run_workers(state_dir)
    elapsed = time.perf_counter() - started
    failures = [item for item in results if item[1] != 0]
    total += len(failures)
    code, output = verify_now(state_dir)
    print(json.dumps({
        "round": round_no,
        "workers": WORKERS,
        "failures": len(failures),
        "elapsed_s": round(elapsed, 3),
        "tmp_leftovers": sorted(p.name for p in (DIST, ANCHOR.parent) if p.name.endswith(".tmp")),
        "verify_after": {"returncode": code, "output": output[:400]},
        "binding": binding_summary(),
        "failure_samples": [f"#{i}: {out[:300]}" for i, _, out in failures[:4]],
    }, ensure_ascii=False))
    print("TOTAL_FAILURES", total)
