#!/usr/bin/env python3
"""Validate an output produced with ``--runtime-native``.

Used by CI and by release checks. It does not need the build secret to be
published: the caller passes the output directory, and this script reads the
secret from its standard sibling path only for the local leak scan.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyshield import crypto, licensing  # noqa: E402


def fail(message: str) -> None:
    print(f"native check failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def _native_extensions(runtime: Path, name: str) -> list[Path]:
    return sorted(
        list(runtime.glob(name + "*.pyd"))
        + list(runtime.glob(name + "*.so"))
        + list(runtime.glob(name + "*.dll"))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=("layout", "keys", "run", "all"), default="all",
        help="check only one phase; useful for pinpointing CI failures",
    )
    parser.add_argument("output", help="output directory produced with --runtime-native")
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    runtime = output / "_shield_runtime"
    if not runtime.is_dir():
        fail(f"missing runtime directory: {runtime}")

    extensions = _native_extensions(runtime, "_core")
    if args.phase in ("layout", "all"):
        if not extensions:
            fail("no native _core extension found")
        for name in ("_core", "_crypto", "_ed25519", "_keyring", "_chacha_fast"):
            if (runtime / (name + ".py")).exists():
                fail(f"native build still publishes {name}.py")
            if not _native_extensions(runtime, name):
                fail(f"native extension missing for {name}")

        plaintext = [path for path in runtime.rglob("*.py") if path.is_file()]
        if plaintext:
            fail("plaintext runtime sources remain: " + ", ".join(
                path.name for path in plaintext
            ))
        for path in runtime.iterdir():
            if path.suffix.lower() in {".pdb", ".ilk", ".exp", ".lib"}:
                fail(f"debug sidecar remains: {path.name}")
        print(f"native layout OK: {extensions[0].name}")
    if args.phase == "layout":
        return 0

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
    if args.phase == "keys":
        print("native key scan OK: no full master key in runtime")
        return 0

    env = os.environ.copy()
    env["PYSHELD_STATE_DIR"] = tempfile.mkdtemp(prefix="pyshield-native-state-")
    env.pop("PYTHONPATH", None)
    for command in (("manage.py", "check"), ("-m", "_shield_runtime", "verify", "--json")):
        completed = subprocess.run(
            [sys.executable, *command], cwd=output, env=env,
            capture_output=True, text=True,
        )
        if completed.returncode != 0:
            fail(
                f"{' '.join(command)} failed ({completed.returncode}):\n"
                + (completed.stdout + completed.stderr)[-2000:]
            )

    print(f"native check OK: {extensions[0].name} ({extensions[0].stat().st_size} bytes)")
    print("no .py runtime, no debug sidecars, no full master key, protected app runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
