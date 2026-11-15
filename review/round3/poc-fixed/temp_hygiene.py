"""Inspect %TEMP% for build residue and sensitive material."""
import hashlib
import json
import marshal
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TEMP = Path(os.environ["TEMP"])
FLAG = b"PSH-ROUND3-e3b2caa377c88630"
FLAG_HASH = hashlib.sha256(FLAG).hexdigest().encode()
CHALLENGE_TREE = Path(
    os.environ.get(
        "PYSHELD_CHALLENGE_TREE",
        Path(__file__).resolve().parents[3] / "review" / "round3" / "extracted" / "round3-django-shielded",
    )
)


def derive_test_keys():
    try:
        build = json.loads((CHALLENGE_TREE / ".pyshield-build.json").read_text(encoding="utf-8"))
        build_id = build["build_id"]
        pyc = CHALLENGE_TREE / "_shield_runtime" / "_keyring.pyc"
        py = CHALLENGE_TREE / "_shield_runtime" / "_keyring.py"
        namespace = {}
        if pyc.is_file():
            exec(marshal.loads(pyc.read_bytes()[16:]), namespace)
        elif py.is_file():
            exec(compile(py.read_text(encoding="utf-8"), str(py), "exec"), namespace)
        else:
            return b"", b""
        master = namespace["master_key"]()
        sys.path.insert(0, str(CHALLENGE_TREE))
        from _shield_runtime import _crypto  # noqa: E402

        build_key = _crypto.hkdf_sha256(
            master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
        )
        return master, build_key
    except Exception:
        return b"", b""


MASTER, BUILD_KEY = derive_test_keys()
NEEDLES = {
    "new_flag": FLAG,
    "new_flag_sha256": FLAG_HASH,
}
if MASTER:
    NEEDLES["challenge_master"] = MASTER
if BUILD_KEY:
    NEEDLES["challenge_build_key"] = BUILD_KEY
dirs = []
hits = []
secret_files = []
for entry in sorted(TEMP.iterdir(), key=lambda item: item.name.lower()):
    name = entry.name.lower()
    if not (name.startswith("pyshield") or name.startswith("psh")):
        continue
    dirs.append({"name": entry.name, "type": "dir" if entry.is_dir() else "file",
                 "bytes": entry.stat().st_size if entry.is_file() else None})
    if entry.is_file():
        try:
            data = entry.read_bytes()
        except OSError:
            continue
        for label, needle in NEEDLES.items():
            if needle in data:
                hits.append({"path": str(entry), "kind": label})
    else:
        for path in entry.rglob("*"):
            if not path.is_file() or path.stat().st_size > 60_000_000:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            for label, needle in NEEDLES.items():
                if needle in data:
                    hits.append({"path": str(path), "kind": label})
            if path.name.lower().endswith(("shield-secret.json", ".state", ".anchor")):
                secret_files.append({"path": str(path), "bytes": path.stat().st_size})

# Any real (non-2-byte fixture) shield-secret anywhere under TEMP.
all_secrets = []
for dirpath, dirnames, filenames in os.walk(TEMP):
    depth = len(Path(dirpath).relative_to(TEMP).parts)
    if depth > 4:
        dirnames[:] = []
        continue
    for filename in filenames:
        if filename.lower().endswith("shield-secret.json"):
            path = Path(dirpath) / filename
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > 2:
                all_secrets.append({"path": str(path), "bytes": size})

print(json.dumps({
    "temp": str(TEMP),
    "matching_entries": dirs,
    "sensitive_hits": hits,
    "state_or_secret_samples": secret_files[:20],
    "real_secret_files": all_secrets,
}, ensure_ascii=False, indent=2))
