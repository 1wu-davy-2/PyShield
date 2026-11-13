"""Signed package manifest used to detect unauthorized changes on site.

The manifest lists every file that must remain unchanged after deployment:
protected module stubs, the runtime, and (optionally) data files. It is signed
with the build's Ed25519 key; the runtime verifies it with the embedded public
key before loading any protected module.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath

from . import __version__, crypto, ed25519

MANIFEST_FILENAME = ".pyshield-manifest.json"
MANIFEST_FORMAT = 1

CATEGORY_CODE = "code"
CATEGORY_RUNTIME = "runtime"
CATEGORY_DATA = "data"
CATEGORIES = (CATEGORY_CODE, CATEGORY_RUNTIME, CATEGORY_DATA)

DEFAULT_MUTABLE_PATTERNS = (
    "pyshield.lic",
    "*.lic",
    "*.shield-secret.json",
    ".pyshield-build.json",
    MANIFEST_FILENAME,
    "local_settings.py",
    "*/local_settings.py",
    "settings_local.py",
    "*/settings_local.py",
    ".env",
    ".env.*",
    "*.sqlite3",
    "*.log",
    "logs/*",
    "*/logs/*",
    "media/*",
    "*/media/*",
    "staticfiles/*",
    "*/staticfiles/*",
    "*.pyc",
    "*.pyo",
    "__pycache__/*",
    "*/__pycache__/*",
)


class ManifestError(Exception):
    """Raised when a package manifest is missing, invalid or cannot be used."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_mutable(relative: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def is_safe_relative(relative: str) -> bool:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return False
    path = PurePosixPath(relative)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and ":" not in path.parts[0]
    )


def sign_payload(payload: dict, signing_key: bytes) -> dict:
    signed = dict(payload)
    signed.pop("signature", None)
    signed["signature"] = crypto.b85encode(
        ed25519.sign(signing_key, crypto.manifest_signing_message(signed))
    )
    return signed


def verify_signature(payload: dict, public_key: bytes) -> bool:
    signature = payload.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    try:
        raw = crypto.b85decode(signature)
    except Exception:
        return False
    return ed25519.verify(public_key, crypto.manifest_signing_message(payload), raw)


def collect_files(
    root: Path,
    code_paths: tuple[str, ...] | list[str],
    mutable_patterns: tuple[str, ...] | list[str],
) -> dict[str, dict]:
    code = {PurePosixPath(path).as_posix() for path in code_paths}
    files: dict[str, dict] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        is_runtime = relative.startswith("_shield_runtime/")
        # The runtime is never mutable: sourceless .pyc builds must stay covered.
        if not is_runtime and is_mutable(relative, mutable_patterns):
            continue
        if relative in code:
            category = CATEGORY_CODE
        elif is_runtime:
            category = CATEGORY_RUNTIME
        else:
            category = CATEGORY_DATA
        files[relative] = {
            "sha256": file_sha256(path),
            "size": path.stat().st_size,
            "category": category,
        }
    return files


def build_payload(
    root: Path,
    build_id: str,
    cipher: str,
    python_version: str,
    signing_key: bytes,
    integrity: str,
    code_paths: tuple[str, ...] | list[str],
    mutable_patterns: tuple[str, ...] | list[str] = (),
) -> dict:
    payload = {
        "format": MANIFEST_FORMAT,
        "tool": f"pyshield {__version__}",
        "build_id": build_id,
        "cipher": cipher,
        "python": python_version,
        "integrity": integrity,
        "files": collect_files(
            root, code_paths, tuple(DEFAULT_MUTABLE_PATTERNS) + tuple(mutable_patterns)
        ),
    }
    return sign_payload(payload, signing_key)


def write_manifest(path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def read_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"cannot read package manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"package manifest {path} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"package manifest {path} has an invalid structure")
    if payload.get("format") != MANIFEST_FORMAT:
        raise ManifestError("unsupported package manifest format; rebuild with a current PyShield")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise ManifestError("package manifest has no file list")
    return payload


def load_manifest(path: Path, public_key: bytes) -> dict:
    payload = read_manifest(path)
    if not verify_signature(payload, public_key):
        raise ManifestError("package manifest signature is invalid")
    return payload


def verify_files(root: Path, payload: dict, full: bool = False) -> list[str]:
    checked = {CATEGORY_CODE, CATEGORY_RUNTIME}
    if full:
        checked.add(CATEGORY_DATA)
    failures: list[str] = []
    for relative, info in payload.get("files", {}).items():
        if not is_safe_relative(relative) or not isinstance(info, dict):
            failures.append(f"invalid manifest entry: {relative!r}")
            continue
        category = info.get("category")
        if category not in CATEGORIES:
            failures.append(f"unknown category for {relative}")
            continue
        if category not in checked:
            continue
        target = root / relative
        try:
            if target.is_symlink() or not target.is_file():
                raise OSError("missing")
            if target.stat().st_size != info.get("size"):
                raise OSError("size mismatch")
            if file_sha256(target) != info.get("sha256"):
                raise OSError("hash mismatch")
        except OSError as exc:
            failures.append(f"{relative}: {exc}")
    return failures
