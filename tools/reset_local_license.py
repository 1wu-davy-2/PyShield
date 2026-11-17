#!/usr/bin/env python3
"""Back up and clear this machine's trial / tool-license state.

Used by ``tools/build_commercial.py --reset-local-license`` so every packaged
build starts from a clean, unactivated state. Files are *moved* into a backup
folder next to the state directory; nothing is deleted.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyshield import tool_license  # noqa: E402

TARGETS = (
    "install.json",
    "trial.json",
    "quota.json",
    "tool-license.json",
    "build-ledger.jsonl",
    "ledger.lock",
)


def reset(dry_run: bool = False) -> tuple[Path, list[str]]:
    state = Path(tool_license.state_dir())
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = state.parent / f"PyShield-license-backup-{stamp}"
    moved: list[str] = []
    if not state.exists():
        return backup, moved
    for name in TARGETS:
        source = state / name
        if not source.exists():
            continue
        if dry_run:
            moved.append(name)
            continue
        backup.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(backup / name))
        moved.append(name)
    return backup, moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    backup, moved = reset(dry_run=args.dry_run)
    if not moved:
        print("local license state already clean")
        return 0
    print(f"{'would move' if args.dry_run else 'moved'}: {', '.join(moved)}")
    if not args.dry_run:
        print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
