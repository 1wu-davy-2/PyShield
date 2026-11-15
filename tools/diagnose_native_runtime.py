#!/usr/bin/env python3
"""Diagnose a native-runtime sample without executing protected modules."""

from __future__ import annotations

import argparse
import marshal
import re
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_payload(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    chunks = "".join(re.findall(r'^\s{8}"([^"]+)",?$', text, re.M))
    module_id = re.findall(r"^\s{4}'([^']+)',\s*$", text, re.M)[-1]
    return chunks, module_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true",
        help="call load_module on the package/module instead of only decrypting",
    )
    parser.add_argument(
        "--target", choices=("mypkg", "mypkg.tools", "standalone"),
        help="check only one protected module",
    )
    parser.add_argument("output")
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    sys.path.insert(0, str(output))

    from pyshield import crypto, licensing
    from _shield_runtime import _crypto, _core, _keyring

    secret = licensing.read_secret(
        output.with_name(output.name + ".shield-secret.json")
    )
    master = crypto.b85decode(secret["master_key"])
    build_key = crypto.b85decode(secret["build_key"])
    assert _keyring.master_key() == master, "keyring master mismatch"
    assert _core._build_key(master) == build_key, "build key derivation mismatch"

    targets = (
        (output / "mypkg" / "__init__.py", "mypkg"),
        (output / "mypkg" / "tools.py", "mypkg.tools"),
        (output / "standalone.py", "standalone"),
    )
    if args.target:
        targets = tuple(item for item in targets if item[1] == args.target)
    for path, name in targets:
        stub = path.read_text(encoding="utf-8")
        assert stub.startswith("# PyShield protected module"), f"not a stub: {path}"
        chunks, module_id = _stub_payload(path)
        raw = _crypto.open_envelope(
            _crypto.derive_module_key(build_key, module_id),
            _crypto.b85decode(chunks),
            _crypto.module_aad(_core.BUILD_ID, module_id),
        )
        code = marshal.loads(zlib.decompress(raw))
        print(f"native decrypt OK: {module_id}, names={code.co_names[:6]}")
        if args.execute:
            namespace = {"__name__": name, "__file__": str(path)}
            _core.load_module(name, str(path), chunks, module_id)
            print(f"native load_module OK: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
