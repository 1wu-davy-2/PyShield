import sys
from pathlib import Path

sys.dont_write_bytecode = True

SAMPLE = Path(r"E:\tmp\workbuild\code\分析\round3\sample\selfbuild-protected-sample")
sys.path.insert(0, str(SAMPLE))
from _shield_runtime import _core  # noqa: E402

secret = bytes.fromhex(sys.argv[1])
try:
    seen = _core._check_clock(secret)
    print("OK", seen)
    raise SystemExit(0)
except Exception as exc:  # noqa: BLE001
    print(type(exc).__name__, exc)
    raise SystemExit(1)
