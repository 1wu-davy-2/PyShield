"""Round 3 state/binding behavior tests against the plaintext sample runtime."""

import os
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True

SAMPLE = Path(r"E:\tmp\workbuild\code\分析\round3\sample\selfbuild-protected-sample")
sys.path.insert(0, str(SAMPLE))
from _shield_runtime import _core, _secure_store  # noqa: E402

SECRET = bytes(range(32))
MACHINE = "test-machine"
PURPOSE = _core.STATE_PURPOSE
_core._clock_machine = lambda: MACHINE  # deterministic machine id for state MAC
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def record(day):
    return {"format": _core.STATE_FORMAT, "build_id": _core.BUILD_ID, "last_seen": day.isoformat()}


def write_keyed(path, day):
    path.parent.mkdir(parents=True, exist_ok=True)
    _secure_store.write_private(path, _secure_store.seal_state(record(day), MACHINE, PURPOSE, SECRET))


def write_legacy(path, day):
    path.parent.mkdir(parents=True, exist_ok=True)
    _secure_store.write_private(path, _secure_store.seal_state(record(day), MACHINE, PURPOSE))


def setup_dirs(root):
    primary = root / "primary"
    anchor = root / "anchor"
    primary.mkdir(parents=True, exist_ok=True)
    anchor.mkdir(parents=True, exist_ok=True)
    os.environ[_core.STATE_DIR_ENV] = str(primary)
    os.environ[_core.STATE_ANCHOR_DIR_ENV] = str(anchor)
    os.environ.pop(_core.VOLATILE_STATE_ENV, None)
    _core._clock_started = False
    return primary / (_core.BUILD_ID + ".state"), anchor / (_core.BUILD_ID + ".state")


def read_day(path, keyed=True):
    data = path.read_bytes()
    record_ = _secure_store.open_state(
        data, MACHINE, PURPOSE, SECRET if keyed else None
    )
    return record_["last_seen"]


# --- 1. keyed state MAC -----------------------------------------------------
legacy = _secure_store.seal_state(record(date(2024, 1, 1)), MACHINE, PURPOSE)
keyed = _secure_store.seal_state(record(date(2024, 1, 1)), MACHINE, PURPOSE, SECRET)
check("keyed MAC verifies with secret", _secure_store.open_state(keyed, MACHINE, PURPOSE, SECRET)["last_seen"] == "2024-01-01")
try:
    _secure_store.open_state(keyed, MACHINE, PURPOSE)
    check("keyed state rejected without secret", False)
except _secure_store.SecureStoreError:
    check("keyed state rejected without secret", True)
try:
    _secure_store.open_state(legacy, MACHINE, PURPOSE, SECRET)
    check("legacy state rejected with secret", False)
except _secure_store.SecureStoreError:
    check("legacy state rejected with secret", True)
check("legacy state still accepted without secret", _secure_store.open_state(legacy, MACHINE, PURPOSE)["last_seen"] == "2024-01-01")

# --- 2. mismatch self-heals to newer ---------------------------------------
with tempfile.TemporaryDirectory() as td:
    primary, anchor = setup_dirs(Path(td))
    write_keyed(primary, date(2026, 1, 1))
    write_keyed(anchor, date(2026, 2, 1))
    seen, paths = _core._read_clock(SECRET)
    check("mismatch returns max", seen == date(2026, 2, 1), str(seen))
    check("mismatch rewrites primary", read_day(primary) == "2026-02-01", read_day(primary))
    check("mismatch rewrites anchor", read_day(anchor) == "2026-02-01", read_day(anchor))

# --- 3. single write failure does not block, next read heals ----------------
with tempfile.TemporaryDirectory() as td:
    primary, anchor = setup_dirs(Path(td))
    write_keyed(primary, date(2026, 2, 1))
    write_keyed(anchor, date(2026, 1, 1))
    original = _secure_store.write_private

    def fail_anchor(path, data):
        if Path(path) == anchor:
            raise OSError("simulated anchor write failure")
        return original(path, data)

    _secure_store.write_private = fail_anchor
    try:
        seen, _ = _core._read_clock(SECRET)
        check("single write failure does not raise", True)
        check("single write failure returns max", seen == date(2026, 2, 1), str(seen))
        check("primary got newer value", read_day(primary) == "2026-02-01")
        check("anchor stayed old", read_day(anchor) == "2026-01-01")
    except Exception as exc:
        check("single write failure does not raise", False, repr(exc))
    finally:
        _secure_store.write_private = original
    _core._clock_started = False
    seen, _ = _core._read_clock(SECRET)
    check("second read heals both copies", read_day(primary) == read_day(anchor) == "2026-02-01")

# --- 4. both writes failing still blocks -----------------------------------
with tempfile.TemporaryDirectory() as td:
    _, (primary, anchor) = None, setup_dirs(Path(td))
    original = _secure_store.write_private
    _secure_store.write_private = lambda path, data: (_ for _ in ()).throw(OSError("read-only"))
    try:
        try:
            _core._write_clock(date(2026, 3, 1), (primary, anchor), SECRET)
            check("both writes failing raises", False)
        except _core.LicenseError:
            check("both writes failing raises", True)
        os.environ[_core.VOLATILE_STATE_ENV] = "1"
        _core._write_clock(date(2026, 3, 1), (primary, anchor), SECRET)
        check("volatile opt-in allows read-only deployment", True)
    finally:
        _secure_store.write_private = original

# --- 5. one unreadable copy raises SecureStoreError ------------------------
with tempfile.TemporaryDirectory() as td:
    primary, anchor = setup_dirs(Path(td))
    write_keyed(primary, date(2026, 1, 1))
    original = _secure_store.read_private

    def fail_anchor_read(path):
        if Path(path) == anchor:
            raise _secure_store.SecureStoreError("simulated permission error")
        return original(path)

    _secure_store.read_private = fail_anchor_read
    try:
        try:
            _core._read_clock(SECRET)
            check("unreadable copy raises", False)
        except Exception as exc:
            check("unreadable copy raises", True, type(exc).__name__)
    finally:
        _secure_store.read_private = original

# --- 6. legacy public-MAC state is migrated (downgrade path) ---------------
with tempfile.TemporaryDirectory() as td:
    primary, anchor = setup_dirs(Path(td))
    write_legacy(primary, date(2020, 1, 1))
    write_legacy(anchor, date(2020, 1, 1))
    seen, _ = _core._read_clock(SECRET)
    check("legacy state accepted once", seen == date(2020, 1, 1), str(seen))
    check("legacy state rewritten keyed", read_day(primary) == "2020-01-01", "keyed MAC ok")

# --- 7. concurrent first reads ---------------------------------------------
with tempfile.TemporaryDirectory() as td:
    primary, anchor = setup_dirs(Path(td))
    write_keyed(primary, date(2026, 1, 1))
    write_keyed(anchor, date(2026, 2, 1))
    errors = []

    def worker():
        try:
            _core._read_clock(SECRET)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    check("concurrent reads do not raise", not errors, "; ".join(errors[:2]))
    check("concurrent reads converge", read_day(primary) == read_day(anchor) == "2026-02-01")

# --- 8. onefile binding guard and env migration ----------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    os.environ.pop(_core.BINDING_FILE_ENV, None)
    fake_mei = Path(tempfile.gettempdir()) / "_MEIunit"
    sys._MEIPASS = str(fake_mei)
    check("default _MEI temp is volatile", _core._binding_paths_are_volatile())
    try:
        _core._check_first_run_binding(SECRET)
        check("onefile first-run binding rejected", False)
    except _core.LicenseError:
        check("onefile first-run binding rejected", True)
    binding = root / "binding.json"
    os.environ[_core.BINDING_FILE_ENV] = str(binding)
    check("binding env disables volatile guard", not _core._binding_paths_are_volatile())
    _core._binding_started = False
    _core._check_first_run_binding(SECRET)
    anchor = Path(str(binding) + ".anchor")
    check("binding env writes primary", binding.is_file())
    check("binding env writes anchor", anchor.is_file())
    _core._binding_started = False
    anchor.unlink()
    _core._check_first_run_binding(SECRET)
    check("deleted anchor is restored", anchor.is_file())
    _core._binding_started = False
    binding.unlink()
    _core._check_first_run_binding(SECRET)
    check("deleting both records resets first-run binding", binding.is_file() and anchor.is_file())
    sys._MEIPASS = str(root / "_MEIfake")
    check("non-temp _MEIPASS evades heuristic", not _core._binding_paths_are_volatile())
    del sys._MEIPASS
    os.environ.pop(_core.BINDING_FILE_ENV, None)

print("\nsummary:", sum(1 for _, ok, _ in RESULTS if ok), "/", len(RESULTS), "passed")
