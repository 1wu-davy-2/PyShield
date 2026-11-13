"""Launch the PyShield desktop builder window."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import core  # noqa: E402
from gui.backend import Api  # noqa: E402


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def main() -> int:
    import webview

    index = resource_root() / "gui" / "web" / "index.html"
    if not index.is_file():
        raise SystemExit(f"界面资源缺失：{index}")

    api = Api()
    window = webview.create_window(
        core.APP_TITLE,
        url=index.as_uri(),
        js_api=api,
        width=1440,
        height=920,
        min_size=(1120, 760),
        frameless=True,
        easy_drag=False,
        background_color="#f4f5f7",
        text_select=True,
    )
    api.bind(window)
    start_options = {
        "debug": bool(os.environ.get("PYSHELD_GUI_DEBUG")),
        "private_mode": False,
    }
    if sys.platform != "win32":
        icon = resource_root() / "gui" / "assets" / "icon.png"
        if icon.is_file():
            start_options["icon"] = str(icon)
    webview.start(**start_options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
