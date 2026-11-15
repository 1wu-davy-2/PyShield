"""Round 5 regression: a forged __pycache__ entry must not execute.

Round 4 shipped the cache inside the challenge zip; this version plants the
cache the way an attacker would (the release directory is writable), then runs
``manage.py check``. Expected result on fixed builds: non-zero exit, no marker,
and an ``unsigned bytecode cache`` integrity failure.

Usage:
    py -3.12 pycache_tamper_r5.py <round3-django-shielded directory>
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
MARKER = "PYSHELD_PYC_BYPASS_R5"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pyshield-pyc-tamper-r5-") as temp:
        copy = Path(temp) / "app"
        shutil.copytree(TREE, copy)
        source = copy / "demo" / "__init__.py"
        cache_dir = copy / "demo" / "__pycache__"
        cache_dir.mkdir()
        pycache = cache_dir / f"__init__.{sys.implementation.cache_tag}.pyc"
        stat = source.stat()
        header = (
            importlib.util.MAGIC_NUMBER
            + struct.pack("<I", 0)  # timestamp-based pyc
            + struct.pack("<I", int(stat.st_mtime))
            + struct.pack("<I", stat.st_size)
        )
        code = compile(f"print({MARKER!r})\n", str(source), "exec")
        pycache.write_bytes(header + marshal.dumps(code))
        print("planted", pycache.relative_to(copy).as_posix(), pycache.stat().st_size, "bytes")

        env = os.environ.copy()
        env["PYSHELD_STATE_DIR"] = str(Path(temp) / "state")
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        completed = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=copy, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        print(output.strip()[-1200:])
        print("returncode:", completed.returncode)

        if MARKER in output:
            print("FAIL: forged pyc executed")
            return 1
        if completed.returncode == 0:
            print("FAIL: build accepted an unsigned bytecode cache")
            return 1
        if "unsigned bytecode cache" not in output:
            print("FAIL: rejected for an unexpected reason")
            return 1
        if list(copy.rglob("__pycache__")):
            print("note: cache directory still present (rejected before import)")
        print("OK: forged pyc was rejected before it could execute")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
