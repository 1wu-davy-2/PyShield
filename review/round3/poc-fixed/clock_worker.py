"""One clock check in a fresh process. Env: TREE, BK (hex)."""
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ["TREE"])
from _shield_runtime import _core  # noqa: E402

try:
    seen = _core._check_clock(bytes.fromhex(os.environ["BK"]))
    print("OK", seen)
except BaseException as exc:  # noqa: BLE001
    print("ERR", type(exc).__name__, exc)
    raise
