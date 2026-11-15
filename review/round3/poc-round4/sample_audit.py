"""Decrypt every stub in the self-build sample and inspect the bundle."""
import base64
import io
import json
import marshal
import re
import sys
import time
import zipfile
import zlib
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TREE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(TREE))
from _shield_runtime import _crypto, _keyring  # noqa: E402

build = json.loads((TREE / ".pyshield-build.json").read_text(encoding="utf-8"))
build_id = build["build_id"]
t0 = time.perf_counter()
master = _keyring.master_key()
build_key = _crypto.hkdf_sha256(
    master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
)
found = []
flag = []
for path in sorted(TREE.rglob("*.py")):
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("# PyShield protected module"):
        continue
    module_id = re.findall(r"^\s{4}'([^']+)',\s*$", text, re.M)[-1]
    chunks = "".join(re.findall(r'^\s{8}"([^"]+)",?$', text, re.M))
    raw = _crypto.open_envelope(
        _crypto.derive_module_key(build_key, module_id),
        _crypto.b85decode(chunks),
        _crypto.module_aad(build_id, module_id),
    )
    code = marshal.loads(zlib.decompress(raw))
    found.append((module_id, code))
    flag.extend(c for c in code.co_consts if isinstance(c, str) and "PSH-ROUND3" in c)
elapsed = time.perf_counter() - t0

bundle = next((code for module_id, code in found if module_id == "pyshield._runtime_bundle"), None)
bundle_info = {}
if bundle is not None:
    strings = [c for c in bundle.co_consts if isinstance(c, str)]
    blob = max(strings, key=len)
    bundle_info["largest_const_len"] = len(blob)
    try:
        packed = zlib.decompress(base64.b64decode(blob))
        with zipfile.ZipFile(io.BytesIO(packed)) as archive:
            bundle_info["entries"] = archive.namelist()
            bundle_info["has_keyring"] = "_keyring.py" in archive.namelist()
            core = archive.read("_core.py").decode("utf-8") if "_core.py" in archive.namelist() else ""
            bundle_info["core_placeholder"] = "__PYSHELD_BUILD_ID__" in core
            bundle_info["core_master_const"] = "MASTER_KEY_B85" in core
    except Exception as exc:  # noqa: BLE001
        bundle_info["error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps({
    "build_id": build_id,
    "runtime": build.get("runtime"),
    "integrity": build.get("integrity"),
    "stub_modules": len(found),
    "flag_consts": flag,
    "elapsed_ms": round(1000 * elapsed, 3),
    "bundle": bundle_info,
}, ensure_ascii=False, indent=2))
