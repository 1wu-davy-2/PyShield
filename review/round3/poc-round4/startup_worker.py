"""One protected-app startup / reload in a fresh process.

Env: TREE, MODE=startup|reload|ttl.
"""
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["TREE"])
from _shield_runtime import _core  # noqa: E402

mode = os.environ.get("MODE", "startup")
try:
    if mode == "ttl":
        _core.STATE_TTL_SECONDS = 0
    state = _core._load_state()
    if mode == "reload":
        state = _core.reload_license()
    elif mode == "ttl":
        state = _core._state_for_load()
    print("OK", state.get("last_seen"), state.get("bind_mode"), state.get("license_format"))
except BaseException as exc:  # noqa: BLE001
    print("ERR", type(exc).__name__, exc)
    raise
