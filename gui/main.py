"""Launch the PyShield desktop builder window."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import core  # noqa: E402
from gui import instance  # noqa: E402
from gui.backend import Api  # noqa: E402


def resource_root() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    if globals().get("__compiled__") is not None:
        # Nuitka onefile runs as <temp>/onefile_*/python.exe; its parent holds
        # the extracted data files. containing_dir points at the original exe.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _handle_link(api: Api, window, link: str) -> None:
    try:
        api.trial_handle_link(link)
    except Exception:  # noqa: BLE001 - a broken link must not close the app
        pass
    try:
        window.evaluate_js("window.pyshieldTrialRefresh && window.pyshieldTrialRefresh()")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    import webview

    link = instance.deep_link_from_argv(sys.argv)
    if link and instance.send_to_primary({"link": link}):
        # Another window is already running; it takes over from here.
        return 0

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

    listener = instance.LinkServer(
        lambda payload: _handle_link(api, window, str(payload.get("link") or ""))
    )
    listener.start()
    instance.register_protocol()

    if link:
        def _consume() -> None:
            _handle_link(api, window, link)

        window.events.loaded += lambda: threading.Thread(target=_consume, daemon=True).start()

    start_options = {
        "debug": bool(os.environ.get("PYSHELD_GUI_DEBUG")),
        "private_mode": False,
    }
    if sys.platform != "win32":
        icon = resource_root() / "gui" / "assets" / "icon.png"
        if icon.is_file():
            start_options["icon"] = str(icon)
    try:
        webview.start(**start_options)
    finally:
        listener.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
