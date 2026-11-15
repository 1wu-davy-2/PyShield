"""Offline PyInstaller CArchive/PYZ inspection for the new builder exes."""
import json
import marshal
import os
import re
import sys
import zlib
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
EXE = Path(sys.argv[1]).resolve()
MODE = sys.argv[2] if len(sys.argv) > 2 else "selfbuild"
REPO = Path(os.environ.get("PYSHELD_REPO", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(REPO))
from pyshield import crypto  # noqa: E402
from PyInstaller.archive.readers import CArchiveReader  # noqa: E402


def string_consts(code):
    return [item for item in code.co_consts if isinstance(item, str)]


def find_build_id(pyz):
    if "_shield_runtime._core" not in pyz.toc:
        return None, None
    core = pyz.extract("_shield_runtime._core")
    ids = [c for c in core.co_consts if isinstance(c, str) and re.fullmatch(r"[0-9a-f]{16}", c)]
    return (ids[0] if ids else None), core


def decrypt_stub(pyz, module_id, build_id, build_key):
    if module_id not in pyz.toc:
        return None
    stub = pyz.extract(module_id)
    strings = string_consts(stub)
    if not strings:
        return None
    payload = max(strings, key=len)
    try:
        raw = crypto.open_envelope(
            crypto.derive_module_key(build_key, module_id),
            crypto.b85decode(payload),
            crypto.module_aad(build_id, module_id),
        )
        return marshal.loads(zlib.decompress(raw))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


arc = CArchiveReader(str(EXE))
names = list(arc.toc)
py_entries = [name for name in names if name.lower().endswith(".py")]
shield_entries = [name for name in names if "_shield_runtime" in name.lower()]
pyz_names = [name for name in names if name.lower().endswith(".pyz")]
report = {
    "exe": str(EXE),
    "bytes": EXE.stat().st_size,
    "carchive_entries": len(names),
    "py_entries": py_entries,
    "shield_entries": shield_entries,
    "pyz": pyz_names,
    "license_entry": "pyshield.lic" in names,
}
if not pyz_names:
    report["note"] = "no PYZ in CArchive"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0)

pyz = arc.open_embedded_archive(pyz_names[0])
modules = set(pyz.toc)
report["pyz_modules"] = len(modules)
report["control_entries"] = sorted(
    name for name in names
    if not name.lower().endswith((".pyz", ".pyc")) and "pyshield" in name.lower()
)

if MODE == "main":
    report["has_keyring"] = "_shield_runtime._keyring" in modules
    report["has_core"] = "_shield_runtime._core" in modules
    report["builder_present"] = "pyshield.builder" in modules
    builder = pyz.extract("pyshield.builder") if "pyshield.builder" in modules else None
    if builder is not None:
        consts = string_consts(builder)
        report["builder_const_scan"] = {
            "MASTER_KEY_B85": any("MASTER_KEY_B85" in item for item in consts),
            "shield_secret": any("shield-secret" in item for item in consts),
            "private_key": any("PRIVATE KEY" in item for item in consts),
            "flag": any("PSH-ROUND3" in item for item in consts),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0)

# Selfbuild exe: recover master/build key and decrypt selected protected modules.
build_id, core = find_build_id(pyz)
report["build_id"] = build_id
if "_shield_runtime._keyring" not in modules:
    report["error"] = "no runtime keyring in PYZ"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1)
ns = {}
exec(pyz.extract("_shield_runtime._keyring"), ns)
master = ns["master_key"]()
build_key = crypto.hkdf_sha256(
    master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
)
report["master_sha256"] = __import__("hashlib").sha256(master).hexdigest()
report["build_key_sha256"] = __import__("hashlib").sha256(build_key).hexdigest()
report["core_integrity_const"] = [
    c for c in core.co_consts if isinstance(c, str) and c in {"off", "protected", "full"}
]
report["decrypted"] = {}
for module_id in (
    "pyshield.builder",
    "pyshield._runtime_bundle",
    "pyshield.cli",
    "gui.backend",
    "gui.instance",
    "manage",
):
    inner = decrypt_stub(pyz, module_id, build_id, build_key)
    if isinstance(inner, dict):
        report["decrypted"][module_id] = inner
        continue
    if inner is None:
        report["decrypted"][module_id] = "not present"
        continue
    strings = string_consts(inner)
    big = max(strings, key=len) if strings else ""
    report["decrypted"][module_id] = {
        "filename": inner.co_filename,
        "names_head": list(inner.co_names[:16]),
        "consts_head": [repr(c)[:100] for c in inner.co_consts[:6]],
        "largest_const_len": len(big),
        "flag_in_consts": any("PSH-ROUND3" in item for item in strings),
    }
    if module_id == "pyshield._runtime_bundle":
        candidates = [item for item in strings if len(item) > 1000]
        if candidates:
            blob = max(candidates, key=len)
            report["bundle_blob_b64_len"] = len(blob)
            try:
                import base64

                packed = zlib.decompress(base64.b64decode(blob))
                report["bundle_zip_bytes"] = len(packed)
                import io
                import zipfile

                with zipfile.ZipFile(io.BytesIO(packed)) as archive:
                    report["bundle_zip_entries"] = archive.namelist()[:20]
                    report["bundle_zip_entry_count"] = len(archive.namelist())
            except Exception as exc:  # noqa: BLE001
                report["bundle_decode_error"] = f"{type(exc).__name__}: {exc}"

if "pyshield.lic" in names:
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
        report["license"] = {
            key: payload.get(key)
            for key in ("format", "bind_mode", "fingerprints", "expires", "not_before")
        }
        report["build_key_in_license"] = "build_key" in payload
    except Exception as exc:  # noqa: BLE001
        report["license_error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(report, ensure_ascii=False, indent=2))
