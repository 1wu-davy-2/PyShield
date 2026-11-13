#!/usr/bin/env python3
"""Render gui/assets/logo.svg into icon.png and a multi-size icon.ico.

Windows headless Edge/Chrome is used as the rasteriser so the build machine
needs no Python imaging dependency. Run it after editing logo.svg::

    python tools/make_icon.py
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"
SVG = ASSETS / "logo.svg"
PNG = ASSETS / "icon.png"
ICO = ASSETS / "icon.ico"
SIZES = (256, 128, 64, 48, 32, 16)
BROWSERS = (
    Path(r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path(r"C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
)


class IconError(Exception):
    """Raised when the icon cannot be produced."""


def browser() -> Path:
    override = os.environ.get("PYSHELD_BROWSER")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate
        raise IconError(f"PYSHELD_BROWSER does not exist: {candidate}")
    for candidate in BROWSERS:
        if candidate.is_file():
            return candidate
    raise IconError("no Edge/Chrome executable found; set PYSHELD_BROWSER")


def render(svg_text: str, size: int, target: Path) -> None:
    markup = svg_text.replace('width="64"', f'width="{size}"').replace('height="64"', f'height="{size}"')
    with tempfile.TemporaryDirectory() as temp:
        page = Path(temp) / "logo.html"
        page.write_text(
            "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
            "html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
            f"svg{{display:block;width:{size}px;height:{size}px}}</style></head>"
            f"<body>{markup}</body></html>",
            encoding="utf-8",
        )
        command = [
            str(browser()),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--default-background-color=00000000",
            f"--window-size={size},{size}",
            "--virtual-time-budget=2000",
            f"--screenshot={target}",
            page.as_uri(),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0 or not target.is_file():
            details = (completed.stdout + completed.stderr).strip()[-800:]
            raise IconError(f"failed to render {size}px icon:\n{details}")


def package_ico(frames: list[tuple[int, bytes]], target: Path) -> None:
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = bytearray()
    payload = bytearray()
    for size, data in frames:
        width = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", width, width, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    target.write_bytes(header + bytes(entries) + bytes(payload))


def main() -> int:
    try:
        svg_text = SVG.read_text(encoding="utf-8")
        frames: list[tuple[int, bytes]] = []
        with tempfile.TemporaryDirectory() as temp:
            for size in SIZES:
                target = Path(temp) / f"icon-{size}.png"
                render(svg_text, size, target)
                data = target.read_bytes()
                frames.append((size, data))
                if size == 256:
                    PNG.write_bytes(data)
        package_ico(frames, ICO)
    except (IconError, OSError) as exc:
        print(f"[make_icon] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"icon.png  {PNG.stat().st_size} bytes")
    print(f"icon.ico  {ICO.stat().st_size} bytes ({len(frames)} sizes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
