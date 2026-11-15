"""Offline key/master recovery from a PyInstaller onefile exe (no execution)."""

import json
import marshal
import re
import sys
import zlib
from pathlib import Path

EXE = Path(sys.argv[1])
REPO = Path(r"E:\tmp\workbuild\code\PyShield")
sys.path.insert(0, str(REPO))
from pyshield import crypto  # noqa: E402

from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

arc = CArchiveReader(str(EXE))
pyz_name = next((name for name in arc.toc if name.lower().endswith(".pyz")), None)
print("exe:", EXE.name, "pyz:", pyz_name, "license entry:", "pyshield.lic" in arc.toc)
pyz = arc.open_embedded_archive(pyz_name)
modules = set(pyz.toc)
print("has keyring:", "_shield_runtime._keyring" in modules)
if "_shield_runtime._keyring" not in modules:
    raise SystemExit("no keyring")

ns = {}
exec(pyz.extract("_shield_runtime._keyring"), ns)
master = ns["master_key"]()
print("master recovered:", master.hex())

core_code = pyz.extract("_shield_runtime._core")
hex_ids = [c for c in core_code.co_consts if isinstance(c, str) and re.fullmatch(r"[0-9a-f]{16}", c)]
build_id = hex_ids[0] if hex_ids else None
print("build_id:", build_id)
build_key = crypto.hkdf_sha256(
    master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
)
print("build_key:", build_key.hex())

protected = [name for name in ("pyshield.builder", "gui.backend", "manage") if name in modules]
for module_id in protected:
    stub_code = pyz.extract(module_id)
    strings = [c for c in stub_code.co_consts if isinstance(c, str)]
    if not strings:
        print(module_id, "has no string consts (not a PyShield stub?)")
        continue
    payload = max(strings, key=len)
    try:
        raw = crypto.open_envelope(
            crypto.derive_module_key(build_key, module_id),
            crypto.b85decode(payload),
            crypto.module_aad(build_id, module_id),
        )
    except Exception as exc:  # noqa: BLE001
        print(module_id, "not decryptable:", type(exc).__name__, exc)
        continue
    inner = marshal.loads(zlib.decompress(raw))
    print(f"decrypted {module_id}: co_filename={inner.co_filename} names={inner.co_names[:12]}")
    print(f"  first consts: {[repr(c)[:100] for c in inner.co_consts[:6]]}")

if "pyshield.lic" in arc.toc:
    license_raw = arc.extract("pyshield.lic")
    license_key = crypto.hkdf_sha256(
        master, salt=b"pyshield-license-v3", info=build_id.encode("utf-8"), length=32
    )
    try:
        payload = json.loads(
            crypto.open_envelope(
                license_key, license_raw, b"pyshield-license-v3:" + build_id.encode("utf-8")
            ).decode("utf-8")
        )
        print("license:", {k: payload.get(k) for k in ("format", "bind_mode", "expires", "customer")},
              "build_key_present=", "build_key" in payload)
    except Exception as exc:  # noqa: BLE001
        print("license not decryptable:", type(exc).__name__, exc)
