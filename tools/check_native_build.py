#!/usr/bin/env python3
"""Validate an output produced with ``--runtime-native``.

Used by CI and by release checks. It does not need the build secret to be
published: the caller passes the output directory, and this script reads the
secret from its standard sibling path only for the local leak scan.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pyshield import crypto, licensing


def fail(message: str) -> None:
    print(f"native check failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python tools/check_native_build.py <output-directory>", file=sys.stderr)
        return 2
    output = Path(args[0]).resolve()
    runtime = output / "_shield_runtime"
    if not runtime.is_dir():
        fail(f"missing runtime directory: {runtime}")

    extensions = sorted(
        list(runtime.glob("_core*.pyd"))
        + list(runtime.glob("_core*.so"))
        + list(runtime.glob("_core*.dll"))
    )
    if not extensions:
        fail("no native _core extension found")
    for name in ("_core", "_crypto", "_ed25519", "_keyring", "_chacha_fast"):
        if (runtime / (name + ".py")).exists():
            fail(f"native build still publishes {name}.py")
        if not (
            list(runtime.glob(name + "*.pyd"))
            or list(runtime.glob(name + "*.so"))
            or list(runtime.glob(name + "*.dll"))
        ):
            fail(f"native extension missing for {name}")

    for path in runtime.iterdir():
        if path.suffix.lower() in {".pdb", ".ilk", ".exp", ".lib"}:
            fail(f"debug sidecar remains: {path.name}")

    secret_path = licensing.secret_path_for(output)
    if not secret_path.is_file():
        fail(f"build secret not found for leak scan: {secret_path}")
    secret = licensing.read_secret(secret_path)
    master = crypto.b85decode(secret["master_key"])
    needles = (
        crypto.b85encode(master).encode("ascii"),
        master,
        master.hex().encode("ascii"),
    )
    for path in runtime.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for needle in needles:
            if needle and needle in data:
                fail(f"master key material appears in {path.relative_to(output)}")

    env = os.environ.copy()
    env["PYSHELD_STATE_DIR"] = tempfile.mkdtemp(prefix="pyshield-native-state-")
    env.pop("PYTHONPATH", None)
    for args in (("manage.py", "check"), ("-m", "_shield_runtime", "verify", "--json")):
        completed = subprocess.run(
            [sys.executable, *args], cwd=output, env=env,
            capture_output=True, text=True,
        )
        if completed.returncode != 0:
            fail(
                f"{' '.join(args)} failed ({completed.returncode}):\n"
                + (completed.stdout + completed.stderr)[-2000:]
            )

    print(f"native check OK: {extensions[0].name} ({extensions[0].stat().st_size} bytes)")
    print("no .py runtime, no debug sidecars, no full master key, protected app runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
