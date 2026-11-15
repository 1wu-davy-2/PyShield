#!/usr/bin/env python3
"""Fail a release check when secrets, challenge flags or bytecode caches leak.

The builder intentionally writes ``<output>.shield-secret.json`` next to the
release tree so licenses can be reissued. That sibling file must not be shipped
inside the release, and it is used here only to derive key needles for a leak
scan of the release bytes.

Three things are checked:

1. challenge flag material, private key markers and (when needles are
   available) master/build key material;
2. ``*.shield-secret.json`` files inside the release or left in the build
   machine temp directory;
3. import bytecode: any ``__pycache__`` entry, or any ``.pyc``/``.pyo`` file
   that is not listed in the package's own signed manifest. A timestamp-based
   ``.pyc`` is validated only against the source mtime/size, so shipping or
   planting one next to a protected stub bypasses the manifest.

Key material cannot be found by shape alone: a random 32-byte key looks like
any other 64 hex characters. Pass ``--secret <path>`` (or keep the sibling
``*.shield-secret.json``) so the scanner has real needles. Without needles the
key scan is reported as skipped and, unless ``--skip-key-scan`` is given, the
check fails instead of implying that no key leaked.

Usage:
    python tools/check_release.py dist-demo release/
    python tools/check_release.py --secret build/dist.shield-secret.json dist
    python tools/check_release.py --temp --secret build/dist.shield-secret.json
    python tools/check_release.py --skip-key-scan dist
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyshield import crypto, licensing  # noqa: E402

FLAG_PATTERN = re.compile(rb"PSH-ROUND[0-9A-Za-z-]{8,}")
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)
SECRET_KEYS = ("master_key", "build_key", "signing_key")
SECRET_SUFFIX = ".shield-secret.json"
MANIFEST_SUFFIX = ".pyshield-manifest.json"
PYCACHE_DIR = "__pycache__"
BYTECODE_SUFFIXES = (".pyc", ".pyo")


def _secret_needles(secret_path: Path) -> tuple[bytes, ...]:
    try:
        secret = licensing.read_secret(secret_path)
        master = crypto.b85decode(secret["master_key"])
        build = crypto.b85decode(secret["build_key"])
    except (OSError, KeyError, ValueError, TypeError, licensing.LicenseError):
        return ()
    needles = [
        crypto.b85encode(master).encode("ascii"),
        master,
        master.hex().encode("ascii"),
        crypto.b85encode(build).encode("ascii"),
        build,
        build.hex().encode("ascii"),
    ]
    return tuple(needle for needle in needles if needle)


def _looks_like_real_secret(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and any(key in data for key in SECRET_KEYS)


def _collect_needles(secret_paths: list[Path]) -> tuple[tuple[bytes, ...], list[Path]]:
    needles: list[bytes] = []
    used: list[Path] = []
    for path in secret_paths:
        found = _secret_needles(path)
        if not found:
            continue
        used.append(path)
        for needle in found:
            if needle not in needles:
                needles.append(needle)
    return tuple(needles), used


def _discover_secrets(target: Path, explicit: list[Path]) -> list[Path]:
    """Explicit secrets plus the sibling the builder writes next to a build."""
    found: list[Path] = list(explicit)
    try:
        sibling = licensing.secret_path_for(target)
    except (OSError, ValueError):
        sibling = None
    if sibling is not None and sibling.is_file() and sibling not in found:
        if _looks_like_real_secret(sibling):
            found.append(sibling)
    return found


def _manifest_entries(payload: object, prefix: str) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    files = payload.get("files")
    if not isinstance(files, dict):
        return set()
    return {prefix + name for name in files}


def _bytecode_failures(names: list[str], signed: set[str]) -> list[str]:
    failures: list[str] = []
    for name in names:
        parts = name.replace("\\", "/").split("/")
        if PYCACHE_DIR in parts:
            failures.append(f"bytecode cache directory inside release: {name}")
            continue
        if name.lower().endswith(BYTECODE_SUFFIXES) and name not in signed:
            failures.append(f"unsigned bytecode file inside release: {name}")
    return failures


def _scan_bytes(
    name: str, data: bytes, needles: tuple[bytes, ...], failures: list[str]
) -> None:
    match = FLAG_PATTERN.search(data)
    if match:
        # Do not print the flag itself: CI logs must not become a leak channel.
        failures.append(f"challenge flag material in {name} ({len(match.group(0))} bytes)")
    for marker in PRIVATE_KEY_MARKERS:
        if marker in data:
            failures.append(f"private key marker in {name}")
    for needle in needles:
        if needle and needle in data:
            failures.append(f"build key material in {name}")


def _scan_zip(
    path: Path, needles: tuple[bytes, ...], failures: list[str]
) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = [info.filename for info in infos]
            signed: set[str] = set()
            for info in infos:
                if not info.filename.endswith(MANIFEST_SUFFIX):
                    continue
                prefix = info.filename[: -len(MANIFEST_SUFFIX)]
                try:
                    payload = json.loads(archive.read(info).decode("utf-8"))
                except (OSError, RuntimeError, ValueError, UnicodeDecodeError):
                    continue
                signed |= _manifest_entries(payload, prefix)
            failures.extend(_bytecode_failures(names, signed))
            for info in infos:
                if info.filename.endswith(SECRET_SUFFIX):
                    failures.append(
                        f"build secret file inside archive {path}: {info.filename}"
                    )
                    continue
                try:
                    data = archive.read(info)
                except (OSError, RuntimeError):
                    continue
                _scan_bytes(f"{path}!/{info.filename}", data, needles, failures)
    except (OSError, zipfile.BadZipFile) as exc:
        failures.append(f"cannot read archive {path}: {exc}")


def _scan_tree(
    target: Path, needles: tuple[bytes, ...], failures: list[str]
) -> None:
    signed: set[str] = set()
    for manifest_path in target.rglob(f"*{MANIFEST_SUFFIX}"):
        if not manifest_path.is_file():
            continue
        prefix = manifest_path.relative_to(target).as_posix()[: -len(MANIFEST_SUFFIX)]
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        signed |= _manifest_entries(payload, prefix)

    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(target).as_posix()
        if path.name.endswith(SECRET_SUFFIX):
            failures.append(f"build secret file inside release tree: {path}")
            continue
        failures.extend(_bytecode_failures([relative], signed))
        try:
            data = path.read_bytes()
        except OSError as exc:
            failures.append(f"cannot read {path}: {exc}")
            continue
        _scan_bytes(str(path), data, needles, failures)


def _scan_target(
    target: Path, needles: tuple[bytes, ...], failures: list[str]
) -> None:
    if target.is_dir():
        _scan_tree(target, needles, failures)
        return
    if target.suffix.lower() == ".zip":
        _scan_zip(target, needles, failures)
        return
    if target.name.endswith(SECRET_SUFFIX):
        failures.append(f"build secret file passed as a release target: {target}")
        return
    failures.extend(_bytecode_failures([target.name], set()))
    try:
        data = target.read_bytes()
    except OSError as exc:
        failures.append(f"cannot read {target}: {exc}")
        return
    _scan_bytes(str(target), data, needles, failures)


def _temp_entries(root: Path) -> list[Path]:
    entries: list[Path] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return entries
    for path in children:
        if path.name.startswith("pyshield") or path.name.startswith("psh"):
            entries.append(path)
        elif path.is_file() and path.name.endswith(SECRET_SUFFIX):
            entries.append(path)
    return entries


def _temp_secrets(entries: list[Path]) -> list[Path]:
    found: list[Path] = []
    for entry in entries:
        if entry.is_file():
            if entry.name.endswith(SECRET_SUFFIX) and _looks_like_real_secret(entry):
                found.append(entry)
            continue
        if not entry.is_dir():
            continue
        for child in entry.rglob(f"*{SECRET_SUFFIX}"):
            if child.is_file() and _looks_like_real_secret(child):
                found.append(child)
    return found


def _scan_temp(
    failures: list[str], explicit: list[Path], skip_key_scan: bool
) -> tuple[int, tuple[bytes, ...], int]:
    root = Path(tempfile.gettempdir())
    entries = _temp_entries(root)
    discovered = _temp_secrets(entries)
    needles, used = _collect_needles(discovered + explicit)
    for entry in entries:
        if entry.is_file() and entry.name.endswith(SECRET_SUFFIX) and _looks_like_real_secret(entry):
            failures.append(f"real build secret left in TEMP: {entry}")
            continue
        if entry.is_file():
            try:
                data = entry.read_bytes()
            except OSError:
                continue
            _scan_bytes(str(entry), data, needles, failures)
            continue
        for child in entry.rglob("*"):
            if not child.is_file():
                continue
            if child.name.endswith(SECRET_SUFFIX) and _looks_like_real_secret(child):
                failures.append(f"real build secret left in TEMP: {child}")
                continue
            try:
                data = child.read_bytes()
            except OSError:
                continue
            _scan_bytes(str(child), data, needles, failures)
    if not needles and not skip_key_scan:
        failures.append(
            "cannot verify master/build key leakage in TEMP: no --secret and no "
            f"real *{SECRET_SUFFIX} found (pass --secret <build secret>)"
        )
    return len(entries), needles, len(used)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "targets", nargs="*", help="release files or directories to scan",
    )
    parser.add_argument(
        "--temp", action="store_true",
        help="scan leftover pyshield*/psh* entries in the system temp directory",
    )
    parser.add_argument(
        "--secret", action="append", default=[], metavar="PATH",
        help="build secret used as a key needle source (repeatable)",
    )
    parser.add_argument(
        "--skip-key-scan", action="store_true",
        help="allow scanning without master/build key needles (reports the gap)",
    )
    args = parser.parse_args(argv)
    if not args.targets and not args.temp:
        parser.error("provide at least one target or --temp")

    explicit: list[Path] = []
    for raw in args.secret:
        path = Path(raw)
        if not path.is_file():
            parser.error(f"--secret is not a file: {path}")
        explicit.append(path)

    failures: list[str] = []
    for raw in args.targets:
        target = Path(raw)
        if not target.exists():
            failures.append(f"target does not exist: {target}")
            continue
        secrets = _discover_secrets(target, explicit)
        needles, used = _collect_needles(secrets)
        if not needles:
            if args.skip_key_scan:
                print(
                    f"note: {target}: master/build key scan skipped "
                    "(no --secret or sibling build secret)",
                    file=sys.stderr,
                )
            else:
                failures.append(
                    f"cannot verify master/build key leakage for {target}: "
                    "no --secret and no sibling *.shield-secret.json "
                    "(pass --secret <path>, or --skip-key-scan to accept the gap)"
                )
        elif used:
            print(f"key needles: {len(needles)} from {', '.join(str(path) for path in used)}")
        _scan_target(target, needles, failures)

    if args.temp:
        count, needles, secrets_used = _scan_temp(failures, explicit, args.skip_key_scan)
        detail = f", key needles: {len(needles)}" if needles else ", key needles: 0"
        print(f"TEMP scan: checked {count} pyshield*/psh* entries{detail}")
        if secrets_used:
            print(f"TEMP scan: derived needles from {secrets_used} build secret file(s)")

    if failures:
        print("release check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    scope = (
        "no challenge flag, build secret, private key marker or unsigned bytecode found"
        if not args.skip_key_scan
        else "no challenge flag, build secret, private key marker or unsigned bytecode "
        "found (master/build key scan skipped)"
    )
    print(f"release check OK: {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
