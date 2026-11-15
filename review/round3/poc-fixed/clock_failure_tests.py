"""Single-copy write failure / unreadable copy behaviour."""
import json
import os
import shutil
import stat
import sys
from datetime import date, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
TREE = Path(sys.argv[1]).resolve()
BUILD_KEY = bytes.fromhex(sys.argv[2])
BASE = Path(__file__).with_name("run") / "clock-failure"
sys.path.insert(0, str(TREE))
from _shield_runtime import _core  # noqa: E402
from _shield_runtime import _secure_store  # noqa: E402

TODAY = date.today()


def fresh(name: str):
    directory = BASE / name
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    os.environ["PYSHELD_STATE_DIR"] = str(directory)
    os.environ.pop("PYSHELD_STATE_ANCHOR_DIR", None)
    os.environ.pop("PYSHELD_ALLOW_VOLATILE_STATE", None)
    os.environ.pop("PYSHELD_ALLOW_LEGACY_STATE", None)
    return _core._clock_paths()


def keyed(seen: date) -> bytes:
    return _secure_store.seal_state(
        {"format": 2, "build_id": _core.BUILD_ID, "last_seen": seen.isoformat()},
        _core._clock_machine(), _core.STATE_PURPOSE, BUILD_KEY,
    )


def call(volatile: bool = False):
    _core._clock_started = False
    if volatile:
        os.environ["PYSHELD_ALLOW_VOLATILE_STATE"] = "1"
    try:
        return {"ok": True, "value": _core._check_clock(BUILD_KEY)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)[:400]}
    finally:
        os.environ.pop("PYSHELD_ALLOW_VOLATILE_STATE", None)


def readonly(path: Path, flag: bool):
    try:
        path.chmod(stat.S_IREAD if flag else stat.S_IWRITE | stat.S_IREAD)
    except OSError as exc:
        print("chmod_error", path, exc)


cases = []

# Anchor is a read-only valid record; primary is missing. A write failure on
# one copy must not block startup.
paths = fresh("anchor-readonly-missing-primary")
_secure_store.write_private(paths[1], keyed(TODAY - timedelta(days=1)))
readonly(paths[1], True)
cases.append({"case": "anchor_readonly_missing_primary", "first": call(), "second": call()})
readonly(paths[1], False)
cases.append({
    "case": "anchor_readonly_state",
    "primary": _secure_store.open_state(
        _secure_store.read_private(paths[0]), _core._clock_machine(), _core.STATE_PURPOSE, BUILD_KEY
    ).get("last_seen"),
})

# Both copies are read-only: fail closed unless volatile state is explicit.
paths = fresh("both-readonly")
for path in paths:
    _secure_store.write_private(path, keyed(TODAY - timedelta(days=1)))
    readonly(path, True)
cases.append({"case": "both_readonly", "result": call()})
cases.append({"case": "both_readonly_volatile", "result": call(volatile=True)})
for path in paths:
    readonly(path, False)

# One copy is replaced by a directory: reading it fails closed.
paths = fresh("anchor-directory")
_secure_store.write_private(paths[0], keyed(TODAY - timedelta(days=1)))
paths[1].mkdir()
cases.append({"case": "anchor_directory", "result": call()})

# One copy is deleted: it is recreated on the next read.
paths = fresh("anchor-deleted")
_secure_store.write_private(paths[0], keyed(TODAY - timedelta(days=1)))
cases.append({"case": "anchor_deleted", "result": call()})
cases.append({
    "case": "anchor_deleted_state",
    "anchor_exists": paths[1].exists(),
    "primary": _secure_store.open_state(
        _secure_store.read_private(paths[0]), _core._clock_machine(), _core.STATE_PURPOSE, BUILD_KEY
    ).get("last_seen"),
})

print(json.dumps(cases, ensure_ascii=False, indent=2))
