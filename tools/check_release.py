#!/usr/bin/env python3
"""Fail a release check when secrets or challenge flags are exposed.

The builder intentionally writes ``<output>.shield-secret.json`` next to the
release tree so licenses can be reissued. That sibling file must not be shipped
inside the release, and it is used here only to derive key needles for a leak
scan of the release bytes.

Usage:
    python tools/check_release.py dist-demo release/
    python tools/check_release.py --temp

``--temp`` checks ``%TEMP%`` (or ``$TMPDIR``) for leftover ``pyshield*`` /
``psh*`` staging entries and fails if any contains a real build secret or a
challenge flag.
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


def _secret_needles(secret_path: Path) -> tuple[bytes, ...]:
    try:
        secret = licensing.read_secret(secret_path)
        master = crypto.b85decode(secret["master_key"])
        build = crypto.b85decode(secret["build_key"])
    except (OSError, KeyError, ValueError, TypeError):
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
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if info.filename.endswith(".shield-secret.json"):
                    failures.append(f"build secret file inside archive {path}: {info.filename}")
                    continue
                try:
                    data = archive.read(info)
                except (OSError, RuntimeError):
                    continue
                _scan_bytes(f"{path}!/{info.filename}", data, needles, failures)
    except (OSError, zipfile.BadZipFile) as exc:
        failures.append(f"cannot read archive {path}: {exc}")


def _scan_target(
    target: Path, needles: tuple[bytes, ...], failures: list[str]
) -> None:
    if target.is_dir():
        for path in sorted(target.rglob("*")):
            if not path.is_file():
                continue
            if path.name.endswith(".shield-secret.json"):
                failures.append(f"build secret file inside release tree: {path}")
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                failures.append(f"cannot read {path}: {exc}")
                continue
            _scan_bytes(str(path), data, needles, failures)
        return
    if target.suffix.lower() == ".zip":
        _scan_zip(target, needles, failures)
        return
    try:
        data = target.read_bytes()
    except OSError as exc:
        failures.append(f"cannot read {target}: {exc}")
        return
    _scan_bytes(str(target), data, needles, failures)


def _scan_temp(failures: list[str]) -> int:
    root = Path(tempfile.gettempdir())
    entries = [
        path for path in root.iterdir()
        if path.name.startswith("pyshield") or path.name.startswith("psh")
    ]
    for path in entries:
        if path.is_file() and path.name.endswith(".shield-secret.json"):
            if _looks_like_real_secret(path):
                failures.append(f"real build secret left in TEMP: {path}")
            continue
        if path.is_file():
            try:
                data = path.read_bytes()
            except OSError:
                continue
            _scan_bytes(str(path), data, (), failures)
            continue
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            if child.name.endswith(".shield-secret.json") and _looks_like_real_secret(child):
                failures.append(f"real build secret left in TEMP: {child}")
                continue
            try:
                data = child.read_bytes()
            except OSError:
                continue
            _scan_bytes(str(child), data, (), failures)
    return len(entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "targets", nargs="*", help="release files or directories to scan",
    )
    parser.add_argument(
        "--temp", action="store_true",
        help="scan leftover pyshield*/psh* entries in the system temp directory",
    )
    args = parser.parse_args(argv)
    if not args.targets and not args.temp:
        parser.error("provide at least one target or --temp")

    failures: list[str] = []
    for raw in args.targets:
        target = Path(raw)
        if not target.exists():
            failures.append(f"target does not exist: {target}")
            continue
        secret_path = licensing.secret_path_for(target)
        needles = _secret_needles(secret_path) if secret_path.is_file() else ()
        _scan_target(target, needles, failures)

    if args.temp:
        count = _scan_temp(failures)
        print(f"TEMP scan: checked {count} pyshield*/psh* entries")

    if failures:
        print("release check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("release check OK: no challenge flag, build secret or private key material found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
