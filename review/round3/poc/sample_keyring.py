import json
import marshal
import re
import sys
import zlib
from pathlib import Path

sys.dont_write_bytecode = True

SAMPLE = Path(r"E:\tmp\workbuild\code\分析\round3\sample\selfbuild-protected-sample")
sys.path.insert(0, str(SAMPLE))
from _shield_runtime import _crypto, _keyring  # noqa: E402

build = json.loads((SAMPLE / ".pyshield-build.json").read_text(encoding="utf-8"))
build_id = build["build_id"]
master = _keyring.master_key()
build_key = _crypto.hkdf_sha256(master, salt=b"pyshield-build-key-v1", info=build_id.encode(), length=32)
license_raw = (SAMPLE / "pyshield.lic").read_bytes()
lic_key = _crypto.hkdf_sha256(master, salt=b"pyshield-license-v3", info=build_id.encode(), length=32)
payload = json.loads(_crypto.open_envelope(lic_key, license_raw, b"pyshield-license-v3:" + build_id.encode()).decode())
legacy_ok = False
try:
    legacy_key = _crypto.hkdf_sha256(master, salt=b"pyshield-license-v1", info=build_id.encode(), length=32)
    _crypto.open_envelope(legacy_key, license_raw, b"pyshield-license:" + build_id.encode())
    legacy_ok = True
except _crypto.CryptoError:
    pass
stub = (SAMPLE / "pyshield" / "builder.py").read_text(encoding="utf-8")
chunks = "".join(re.findall(r'^\s{8}"([^"]+)",?$', stub, re.M))
module_id = re.findall(r"^\s{4}'([^']+)',\s*$", stub, re.M)[-1]
code = marshal.loads(zlib.decompress(_crypto.open_envelope(
    _crypto.derive_module_key(build_key, module_id),
    _crypto.b85decode(chunks),
    _crypto.module_aad(build_id, module_id),
)))
print("build_id:", build_id, "master_sha256:", __import__("hashlib").sha256(master).hexdigest())
print("license format:", payload.get("format"), "build_key_present:", "build_key" in payload, "v1_accepted:", legacy_ok)
print("decrypted:", module_id, "co_filename:", code.co_filename, "names:", code.co_names[:8])
