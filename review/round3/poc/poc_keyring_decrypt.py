"""Minimal round 3 PoC: release tree -> master key -> plaintext modules.

Usage: python poc_round3.py <shielded-tree>
No license check, no signature check, no process execution.
"""

import json
import marshal
import re
import statistics
import sys
import time
import zlib
from pathlib import Path

sys.dont_write_bytecode = True

TREE = Path(sys.argv[1]).resolve()
RUNTIME = TREE / "_shield_runtime"

t0 = time.perf_counter()
# 1. Recover master key from the sourceless _keyring.pyc with the stdlib only.
keyring_raw = (RUNTIME / "_keyring.pyc").read_bytes()
code = marshal.loads(keyring_raw[16:])
namespace = {}
exec(code, namespace)
master = namespace["master_key"]()
t_key = time.perf_counter()

sys.path.insert(0, str(TREE))
from _shield_runtime import _crypto  # noqa: E402

build = json.loads((TREE / ".pyshield-build.json").read_text(encoding="utf-8"))
build_id = build["build_id"]
build_key = _crypto.hkdf_sha256(
    master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
)
t_derive = time.perf_counter()

# 2. License: prove it is format 3 and that the legacy v1 key is rejected.
license_raw = (TREE / "pyshield.lic").read_bytes()
license_key = _crypto.hkdf_sha256(
    master, salt=b"pyshield-license-v3", info=build_id.encode("utf-8"), length=32
)
payload = json.loads(
    _crypto.open_envelope(
        license_key, license_raw, b"pyshield-license-v3:" + build_id.encode("utf-8")
    ).decode("utf-8")
)
legacy_ok = False
try:
    legacy_key = _crypto.hkdf_sha256(
        master, salt=b"pyshield-license-v1", info=build_id.encode("utf-8"), length=32
    )
    _crypto.open_envelope(
        legacy_key, license_raw, b"pyshield-license:" + build_id.encode("utf-8")
    )
    legacy_ok = True
except _crypto.CryptoError:
    pass

# 3. Decrypt every protected stub without executing it.
timings = []
recovered = {}
for path in sorted(TREE.rglob("*.py")):
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("# PyShield protected module"):
        continue
    module_id = re.findall(r"^\s{4}'([^']+)',\s*$", text, re.M)[-1]
    chunks = "".join(re.findall(r'^\s{8}"([^"]+)",?$', text, re.M))
    start = time.perf_counter()
    raw = _crypto.open_envelope(
        _crypto.derive_module_key(build_key, module_id),
        _crypto.b85decode(chunks),
        _crypto.module_aad(build_id, module_id),
    )
    module_code = marshal.loads(zlib.decompress(raw))
    timings.append(time.perf_counter() - start)
    consts = [
        item for item in module_code.co_consts if isinstance(item, (str, bytes))
    ]
    recovered[module_id] = (module_code, consts)

print(f"tree: {TREE}")
print(f"build_id: {build_id}  cipher: {build.get('cipher')}  runtime: {build.get('runtime')}")
print(f"key recovery: {keyring_raw[16:].__len__()} bytes marshal -> master_key() -> {len(master)} bytes")
print(f"license: format={payload.get('format')} bind_mode={payload.get('bind_mode')} "
      f"expires={payload.get('expires')} build_key_in_license={'build_key' in payload} "
      f"v1_key_accepted={legacy_ok}")
print(f"decrypted modules: {len(recovered)}")
for module_id, (module_code, consts) in recovered.items():
    flag_consts = [c for c in consts if isinstance(c, str) and "PSH-ROUND3" in c]
    note = f"  FLAG_CONST={flag_consts!r}" if flag_consts else ""
    print(f"  {module_id}: names={module_code.co_names}{note}")
print(f"timing: key={1000*(t_key-t0):.2f} ms derive={1000*(t_derive-t_key):.2f} ms "
      f"decrypt_median={1000*statistics.median(timings):.2f} ms "
      f"decrypt_all={1000*sum(timings):.2f} ms")
