#!/usr/bin/env python3
"""Diagnose a native-runtime sample without executing protected modules."""

from __future__ import annotations

import marshal
import re
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python tools/diagnose_native_runtime.py <output>", file=sys.stderr)
        return 2
    output = Path(args[0]).resolve()
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

    stub = (output / "mypkg" / "tools.py").read_text(encoding="utf-8")
    chunks = "".join(re.findall(r'^\s{8}"([^"]+)",?$', stub, re.M))
    module_id = re.findall(r"^\s{4}'([^']+)',\s*$", stub, re.M)[-1]
    raw = _crypto.open_envelope(
        _crypto.derive_module_key(build_key, module_id),
        _crypto.b85decode(chunks),
        _crypto.module_aad(_core.BUILD_ID, module_id),
    )
    code = marshal.loads(zlib.decompress(raw))
    print(f"native decrypt OK: {module_id}, names={code.co_names[:6]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
