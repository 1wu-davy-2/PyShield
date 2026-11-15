"""Raw-byte scan of the delivered artifacts and extracted trees."""
import hashlib
import json
import marshal
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(os.environ.get("PYSHELD_REPO", Path(__file__).resolve().parents[3]))
ROUND = Path(os.environ.get("PYSHELD_ROUND_DIR", REPO / "review" / "round3"))
CHALLENGE_TREE = Path(
    os.environ.get("PYSHELD_CHALLENGE_TREE", ROUND / "extracted" / "round3-django-shielded")
)
SAMPLE_TREE = Path(
    os.environ.get("PYSHELD_SAMPLE_TREE", ROUND / "extracted" / "selfbuild-protected-sample")
)
FLAG = "PSH-ROUND3-e3b2caa377c88630"
FLAG_HASH = hashlib.sha256(FLAG.encode()).hexdigest()


def derive_test_keys():
    """Derive the challenge master/build key from the extracted tree.

    Keeping the key out of this script avoids copying key material into the
    repository; the keyring already contains it for the authorized sample.
    """
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
    "flag": FLAG.encode(),
    "flag_sha256": FLAG_HASH.encode(),
    "MASTER_KEY_B85": b"MASTER_KEY_B85",
    "shield_secret": b"shield-secret",
    "private_key": b"PRIVATE KEY",
}
if MASTER:
    NEEDLES["master"] = MASTER
if BUILD_KEY:
    NEEDLES["build_key"] = BUILD_KEY
ARTIFACTS = {
    "challenge_zip": REPO / "review/round3/round3-django-shielded.zip",
    "sample_zip": REPO / "review/round3/PyShieldBuilder-protected-sample-round3.zip",
    "main_exe": REPO / "PyShieldBuilder.exe",
    "selfbuild_exe": REPO / "dist-selfbuild/PyShieldBuilder-selfbuild.exe",
    "challenge_tree": CHALLENGE_TREE,
    "sample_tree": SAMPLE_TREE,
}


def scan_bytes(data: bytes):
    return {name: data.count(needle) for name, needle in NEEDLES.items() if needle in data}


def scan_tree(root: Path):
    hits = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        found = scan_bytes(data)
        if found:
            hits[str(path.relative_to(root))] = found
    return hits


report = {}
for name, path in ARTIFACTS.items():
    if path.is_dir():
        report[name] = {"path": str(path), "tree_hits": scan_tree(path)}
    else:
        data = path.read_bytes()
        report[name] = {
            "path": str(path),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "hits": scan_bytes(data),
        }
print(json.dumps(report, ensure_ascii=False, indent=2))
