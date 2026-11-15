"""Round 5: PYTHONPYCACHEPREFIX must not let a poisoned cache run.

Python applies ``PYTHONPYCACHEPREFIX`` at startup, before any project code is
imported. If the runtime did not override it, an attacker could point the
prefix at a directory they control and plant a ``.pyc`` for a protected stub.
Fixed builds overwrite ``sys.pycache_prefix`` during runtime import, so the
poisoned cache is never consulted and the normal check still passes.

Usage:
    py -3.12 prefix_hijack_r5.py <round3-django-shielded directory>
"""
import importlib.util
import marshal
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TREE = Path(sys.argv[1]).resolve()
MARKER = "PREFIX_POISON_EXECUTED"


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="pyshield-prefix-r5-"))
    try:
        copy = work / "app"
        shutil.copytree(TREE, copy)
        poison = work / "poison-prefix" / "demo"
        poison.mkdir(parents=True)
        source = copy / "demo" / "__init__.py"
        stat = source.stat()
        header = (
            importlib.util.MAGIC_NUMBER
            + struct.pack("<I", 0)
            + struct.pack("<I", int(stat.st_mtime))
            + struct.pack("<I", stat.st_size)
        )
        code = compile(f"print({MARKER!r})\n", str(source), "exec")
        (poison / f"__init__.{sys.implementation.cache_tag}.pyc").write_bytes(
            header + marshal.dumps(code)
        )
        env = os.environ.copy()
        env["PYTHONPYCACHEPREFIX"] = str(work / "poison-prefix")
        env["PYSHELD_STATE_DIR"] = str(work / "state")
        completed = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=copy, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        print(output.strip()[-600:])
        print("returncode:", completed.returncode)
        if MARKER in output:
            print("FAIL: poisoned cache from PYTHONPYCACHEPREFIX executed")
            return 1
        if completed.returncode != 0:
            print("FAIL: normal check did not pass")
            return 1
        print("OK: runtime replaced PYTHONPYCACHEPREFIX and ignored the poisoned cache")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
