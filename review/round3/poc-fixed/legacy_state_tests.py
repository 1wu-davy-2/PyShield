"""Legacy public-MAC migration and future-date checks (fixed runtime)."""
import json
import os
import shutil
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
TREE = Path(sys.argv[1]).resolve()
BUILD_KEY = bytes.fromhex(sys.argv[2])
BASE = Path(__file__).with_name("run") / "legacy"
sys.path.insert(0, str(TREE))
from _shield_runtime import _core  # noqa: E402
from _shield_runtime import _secure_store  # noqa: E402

TODAY = date.today()


def fresh(name: str) -> tuple[Path, Path]:
    directory = BASE / name
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    os.environ["PYSHELD_STATE_DIR"] = str(directory)
    os.environ.pop("PYSHELD_STATE_ANCHOR_DIR", None)
    paths = _core._clock_paths()
    return paths


def write_both(paths, data):
    for path in paths:
        _secure_store.write_private(path, data)


def keyed(seen: date) -> bytes:
    payload = {"format": 2, "build_id": _core.BUILD_ID, "last_seen": seen.isoformat()}
    return _secure_store.seal_state(
        payload, _core._clock_machine(), _core.STATE_PURPOSE, BUILD_KEY
    )


def legacy(seen: date) -> bytes:
    payload = {"format": 2, "build_id": _core.BUILD_ID, "last_seen": seen.isoformat()}
    return _secure_store.seal_state(payload, _core._clock_machine(), _core.STATE_PURPOSE)


def call(allow_legacy: bool | None):
    _core._clock_started = False
    previous = os.environ.pop("PYSHELD_ALLOW_LEGACY_STATE", None)
    if allow_legacy is not None:
        os.environ["PYSHELD_ALLOW_LEGACY_STATE"] = "1" if allow_legacy else "0"
    try:
        return {"ok": True, "value": _core._check_clock(BUILD_KEY)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    finally:
        os.environ.pop("PYSHELD_ALLOW_LEGACY_STATE", None)
        if previous is not None:
            os.environ["PYSHELD_ALLOW_LEGACY_STATE"] = previous


def inspect(paths):
    result = {}
    for label, path in zip(("primary", "anchor"), paths):
        raw = _secure_store.read_private(path)
        try:
            keyed_record = _secure_store.open_state(
                raw, _core._clock_machine(), _core.STATE_PURPOSE, BUILD_KEY
            )
            result[label] = {"keyed": True, "last_seen": keyed_record.get("last_seen")}
        except Exception:
            try:
                legacy_record = _secure_store.open_state(
                    raw, _core._clock_machine(), _core.STATE_PURPOSE
                )
                result[label] = {"keyed": False, "last_seen": legacy_record.get("last_seen")}
            except Exception as exc:  # noqa: BLE001
                result[label] = {"error": type(exc).__name__}
    return result


cases = []

# 1. Legacy state without opt-in is rejected.
paths = fresh("default-reject")
write_both(paths, legacy(TODAY - timedelta(days=1)))
cases.append({"case": "default_reject_legacy", "result": call(False), "state": inspect(paths)})

# 2. Explicit opt-in migrates both copies to the keyed MAC.
paths = fresh("opt-in-migrate")
write_both(paths, legacy(TODAY - timedelta(days=1)))
cases.append({"case": "opt_in_migrate", "result": call(True), "state": inspect(paths)})

# 3. The same env can migrate a second forged legacy record: no one-time marker.
write_both(paths, legacy(TODAY - timedelta(days=10)))
cases.append({"case": "opt_in_repeat", "result": call(True), "state": inspect(paths)})

# 4. Once the env is removed, a forged legacy record is rejected again.
write_both(paths, legacy(TODAY - timedelta(days=10)))
cases.append({"case": "after_env_unset", "result": call(False), "state": inspect(paths)})

# 5. A keyed state more than 7 days in the future is rejected.
paths = fresh("future-keyed-30")
write_both(paths, keyed(TODAY + timedelta(days=30)))
cases.append({"case": "future_keyed_30", "result": call(False), "state": inspect(paths)})

# 6. A legacy state more than 7 days in the future is also rejected during migration.
paths = fresh("future-legacy-30")
write_both(paths, legacy(TODAY + timedelta(days=30)))
cases.append({"case": "future_legacy_30", "result": call(True), "state": inspect(paths)})

# 7. One day in the future passes the future check and freezes the clock for a day.
paths = fresh("future-keyed-1")
write_both(paths, keyed(TODAY + timedelta(days=1)))
cases.append({"case": "future_keyed_1", "result": call(False), "state": inspect(paths)})

# 8. One legacy copy plus one keyed copy is rejected by default, accepted on opt-in.
paths = fresh("mixed")
_secure_store.write_private(paths[0], legacy(TODAY - timedelta(days=1)))
_secure_store.write_private(paths[1], keyed(TODAY - timedelta(days=1)))
cases.append({"case": "mixed_default", "result": call(False), "state": inspect(paths)})
cases.append({"case": "mixed_opt_in", "result": call(True), "state": inspect(paths)})

print(json.dumps(cases, ensure_ascii=False, indent=2))
