"""Launch the PyShield desktop builder window."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import core  # noqa: E402
from gui import instance  # noqa: E402
from gui import tray  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gui.backend import Api


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


def _dispatch_message(api: Api, window, payload: dict) -> None:
    """Handle a message forwarded by a second launch or a deep link."""
    if payload.get("show"):
        api._raise_window()
    link = str(payload.get("link") or "")
    if link:
        _handle_link(api, window, link)


def _run_embedded_setup(argv: list[str]) -> bool:
    """Run a build script inside the frozen builder process.

    ``pyshield.builder`` needs to execute ``setup.py build_ext`` for native
    runtime builds. In a PyInstaller onefile app ``sys.executable`` is the app
    itself, so the app exposes this hidden entry point instead of requiring an
    external Python installation.
    """
    if len(argv) < 3 or argv[1] != "--pyshield-setup":
        return False
    import runpy

    script = argv[2]
    log_path = Path(script).with_name("native-setup.log")
    try:
        log = log_path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w", encoding="utf-8")
    # Windowed PyInstaller builds have sys.stdout/stderr set to None. Cython's
    # compiled modules can block when they try to write progress to them, so
    # give the nested setup process a real stream.
    sys.stdout = log
    sys.stderr = log
    code = 0
    try:
        sys.argv = [script, *argv[3:]]
        runpy.run_path(script, run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        import traceback

        traceback.print_exc()
        code = 1
    finally:
        try:
            log.flush()
        except OSError:
            pass
    # Some compiler/setuptools paths leave non-daemon threads behind. This is
    # a short-lived helper process, so terminate it deterministically.
    os._exit(code)


def _run_mcp(argv: list[str]) -> bool:
    """Run the MCP stdio server from the same executable.

    ``PyShieldBuilder.exe --mcp ...`` turns the GUI binary into a headless
    stdio MCP server so a single download can serve both humans and agents.
    """
    if len(argv) < 2 or argv[1] != "--mcp":
        return False
    from pyshield.mcp_server import main as mcp_main

    raise SystemExit(mcp_main(argv[2:]))


def _wait_for_primary(link: str, timeout: float = 5.0) -> bool:
    """Wait for a starting primary to publish its loopback pointer."""
    payload = {"link": link, "show": True}
    deadline = time.monotonic() + timeout
    while True:
        if instance.send_to_primary(payload):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def main() -> int:
    if _run_embedded_setup(sys.argv):
        return 0
    _run_mcp(sys.argv)
    import webview

    from gui.backend import Api

    link = instance.deep_link_from_argv(sys.argv)
    if instance.send_to_primary({"link": link, "show": True}):
        # Another window is already running (possibly hidden in the tray); it
        # takes over from here.
        return 0

    lock = instance.acquire_single_instance()
    if lock is None:
        # Another process is starting but has not written instance.json yet.
        # Give it a short window to become reachable, then exit without opening
        # a second window.
        _wait_for_primary(link)
        return 0

    try:
        index = resource_root() / "gui" / "web" / "index.html"
        if not index.is_file():
            raise SystemExit(f"界面资源缺失：{index}")

        api = Api()
        # Start the loopback MCP endpoint before the window so the port is
        # ready as soon as the first paint shows the AI 接入 page.
        try:
            api.start_mcp()
        except Exception:  # noqa: BLE001 - a busy port must not block the GUI
            pass
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
        tray.install(
            window,
            resource_root() / "gui" / "assets" / "icon.ico",
            can_quit=lambda: not api.manager.running(),
        )

        listener = instance.LinkServer(
            lambda payload: _dispatch_message(api, window, payload)
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
            try:
                api.shutdown_mcp()
            except Exception:  # noqa: BLE001 - shutdown is best effort
                pass
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
