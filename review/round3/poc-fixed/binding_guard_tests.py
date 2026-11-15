"""Onefile / custom _MEIPASS / PYSHELD_BINDING_FILE guard tests."""
import json
import os
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
TREE = Path(sys.argv[1]).resolve()
BUILD_KEY = bytes.fromhex(sys.argv[2])
BASE = Path(__file__).with_name("run") / "binding-guard"
shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir(parents=True)
sys.path.insert(0, str(TREE))
from _shield_runtime import _core  # noqa: E402


def with_env(meipass, override, action):
    old_mei = getattr(sys, "_MEIPASS", None)
    old_override = os.environ.pop("PYSHELD_BINDING_FILE", None)
    try:
        if meipass is not None:
            sys._MEIPASS = str(meipass)
        if override is not None:
            os.environ["PYSHELD_BINDING_FILE"] = str(override)
        return action()
    finally:
        if old_mei is None:
            try:
                del sys._MEIPASS
            except AttributeError:
                pass
        else:
            sys._MEIPASS = old_mei
        os.environ.pop("PYSHELD_BINDING_FILE", None)
        if old_override is not None:
            os.environ["PYSHELD_BINDING_FILE"] = old_override


def check(label, meipass, override):
    volatile = with_env(meipass, override, _core._binding_paths_are_volatile)
    outcome = None
    if override is not None:
        def attempt():
            _core._binding_started = False
            try:
                return {"ok": True, "value": _core._check_first_run_binding(BUILD_KEY)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": type(exc).__name__, "message": str(exc)[:240]}
        outcome = with_env(meipass, override, attempt)
    return {"case": label, "volatile": volatile, "binding": outcome}


cases = []
fake_mei = BASE / "_MEIfake"
fake_mei.mkdir()
exe_parent = Path(sys.executable).resolve().parent

cases.append(check("onefile_custom_mei_override_normal", fake_mei, BASE / "onefile" / "binding.json"))
cases.append(check("onefile_custom_mei_override_mei_path", fake_mei, BASE / "_MEIfoo" / "binding.json"))
cases.append(check("custom_extract_dir_not_named_mei", BASE / "custom_extract", BASE / "custom" / "binding.json"))
cases.append(check("onedir_meipass_override_normal", exe_parent, BASE / "onedir" / "binding.json"))
cases.append(check("onedir_internal_override_normal", exe_parent / "_internal", BASE / "onedir_internal" / "binding.json"))
cases.append(check("no_meipass_override_mei_path", None, BASE / "_MEIbar" / "binding.json"))
cases.append(check("no_meipass_override_normal", None, BASE / "plain" / "binding.json"))

# Moving the override to a fresh persistent path starts a new first-run binding.
first = BASE / "relocate-a" / "binding.json"
second = BASE / "relocate-b" / "binding.json"
cases.append(check("relocate_a", None, first))
cases.append(check("relocate_b", None, second))
cases.append(
    {
        "case": "relocate_records",
        "a": json.loads(first.read_text(encoding="utf-8")),
        "b": json.loads(second.read_text(encoding="utf-8")),
    }
)
cases[-1]["a"].pop("mac", None)
cases[-1]["b"].pop("mac", None)

print(json.dumps(cases, ensure_ascii=False, indent=2))
