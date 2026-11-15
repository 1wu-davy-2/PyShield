"""Minimal offline flag recovery from the delivered shielded tree.

Usage: python recover_flag.py <extracted round3-django-shielded>
No license check, no signature check, no process execution.
"""
import hashlib
import json
import marshal
import re
import sys
import time
import zlib
from pathlib import Path

sys.dont_write_bytecode = True
TREE = Path(sys.argv[1]).resolve()
t0 = time.perf_counter()

build = json.loads((TREE / ".pyshield-build.json").read_text(encoding="utf-8"))
build_id = build["build_id"]

# 1. The sourceless keyring is directly executable and exposes master_key().
keyring_pyc = TREE / "_shield_runtime" / "_keyring.pyc"
keyring_py = TREE / "_shield_runtime" / "_keyring.py"
namespace = {}
if keyring_pyc.is_file():
    exec(marshal.loads(keyring_pyc.read_bytes()[16:]), namespace)
else:
    exec(compile(keyring_py.read_text(encoding="utf-8"), str(keyring_py), "exec"), namespace)
master = namespace["master_key"]()
t_key = time.perf_counter()

# 2. Derive the per-build key locally; the format-3 license does not carry it.
sys.path.insert(0, str(TREE))
from _shield_runtime import _crypto  # noqa: E402

build_key = _crypto.hkdf_sha256(
    master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
)
t_key_derived = time.perf_counter()

# 3. Decrypt the specific protected stub without importing it.
stub = (TREE / "demo" / "hidden_flag.py").read_text(encoding="utf-8")
chunks = "".join(re.findall(r'^\s{8}"([^"]+)",?$', stub, re.M))
raw = _crypto.open_envelope(
    _crypto.derive_module_key(build_key, "demo.hidden_flag"),
    _crypto.b85decode(chunks),
    _crypto.module_aad(build_id, "demo.hidden_flag"),
)
code = marshal.loads(zlib.decompress(raw))
t_decrypted = time.perf_counter()

flag = next(
    (value for value in code.co_consts if isinstance(value, str) and value.startswith("PSH-")),
    None,
)
print(f"build_id: {build_id}")
print(f"keyring: {keyring_pyc.name if keyring_pyc.is_file() else keyring_py.name}")
print(f"master_key: {len(master)} bytes sha256={hashlib.sha256(master).hexdigest()}")
print(f"build_key: {len(build_key)} bytes sha256={hashlib.sha256(build_key).hexdigest()}")
print(f"names: {code.co_names}")
print(f"consts: {[repr(value)[:120] for value in code.co_consts]}")
print(f"flag: {flag}")
print(f"flag_sha256: {hashlib.sha256(flag.encode()).hexdigest() if flag else None}")
print(
    "timing_ms: "
    f"key={1000 * (t_key - t0):.3f} "
    f"derive={1000 * (t_key_derived - t_key):.3f} "
    f"decrypt={1000 * (t_decrypted - t_key_derived):.3f} "
    f"total={1000 * (t_decrypted - t0):.3f}"
)
