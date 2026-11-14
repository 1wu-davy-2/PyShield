"""Windows tray integration and taskbar window behaviour.

pywebview creates frameless windows without ``WS_MINIMIZEBOX``, which stops the
Windows taskbar button from toggling minimise/restore.  The same module installs
a tray icon so the close button can hide the window instead of exiting.

The module is import-safe on Linux/macOS: pythonnet is imported lazily and only
when :func:`install` runs on Windows.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Callable

GWL_STYLE = -16
WS_MINIMIZEBOX = 0x00020000


def _hwnd(window) -> int:
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is None:
        return 0
    try:
        return int(handle.ToInt64())
    except AttributeError:
        return int(handle)


class TrayController:
    """Own the WinForms ``NotifyIcon`` and intercept the window close event."""

    def __init__(
        self,
        window,
        icon_path: Path | None = None,
        can_quit: Callable[[], bool] | None = None,
    ) -> None:
        self._window = window
        self._icon_path = icon_path
        self._can_quit = can_quit
        self._notify = None
        self._ready = False
        self._quitting = False
        self._balloon_shown = False
        self._minimized_state = None
        self._close_reason = None
        self._tooltip_info = None
        self._tooltip_warning = None

    # pywebview raises ``shown`` on a worker thread; WinForms objects must be
    # created on the form's UI thread.
    def _on_shown(self, *_args) -> None:
        native = getattr(self._window, "native", None)
        if native is None:
            return
        try:
            from System import Func, Type

            if native.InvokeRequired:
                native.Invoke(Func[Type](self._setup))
            else:
                self._setup()
        except Exception:  # noqa: BLE001 - tray support must never break startup
            pass

    def _setup(self) -> None:
        try:
            import clr  # noqa: F401 - pythonnet loads the CLR
            from System.Drawing import Icon, SystemIcons
            from System.Windows.Forms import (
                CloseReason,
                ContextMenuStrip,
                NotifyIcon,
                ToolStripMenuItem,
                ToolStripSeparator,
                ToolTipIcon,
                FormWindowState,
            )
        except Exception:  # noqa: BLE001 - non-Windows or missing pythonnet
            return

        self._minimized_state = FormWindowState.Minimized
        self._close_reason = CloseReason
        self._tooltip_info = ToolTipIcon.Info
        self._tooltip_warning = ToolTipIcon.Warning

        native = getattr(self._window, "native", None)
        if native is None:
            return

        # Restore the standard taskbar button behaviour lost with FormBorderStyle.None.
        try:
            hwnd = _hwnd(self._window)
            if hwnd:
                user32 = ctypes.windll.user32
                style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
                if not style & WS_MINIMIZEBOX:
                    user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_MINIMIZEBOX)
        except Exception:  # noqa: BLE001
            pass

        icon = SystemIcons.Application
        try:
            if self._icon_path is not None and self._icon_path.is_file():
                icon = Icon(str(self._icon_path))
        except Exception:  # noqa: BLE001
            icon = SystemIcons.Application

        try:
            notify = NotifyIcon()
            notify.Icon = icon
            notify.Text = "PyShield 加密构建器"

            menu = ContextMenuStrip()
            open_item = ToolStripMenuItem("打开 PyShield")
            open_item.Click += self._menu_open
            quit_item = ToolStripMenuItem("退出 PyShield")
            quit_item.Click += self._menu_quit
            menu.Items.Add(open_item)
            menu.Items.Add(ToolStripSeparator())
            menu.Items.Add(quit_item)

            notify.ContextMenuStrip = menu
            notify.DoubleClick += self._tray_double_click
            notify.Visible = True
            native.FormClosing += self._on_form_closing
        except Exception:  # noqa: BLE001
            try:
                notify.Dispose()
            except Exception:  # noqa: BLE001
                pass
            return

        self._notify = notify
        self._ready = True

    def _menu_open(self, *_args) -> None:
        self.show()

    def _menu_quit(self, *_args) -> None:
        self.quit()

    def _tray_double_click(self, *_args) -> None:
        self.show()

    def _on_form_closing(self, sender, args) -> None:
        if self._quitting:
            return
        try:
            reason = args.CloseReason
            if reason in (
                self._close_reason.WindowsShutDown,
                self._close_reason.TaskManagerClosing,
                self._close_reason.ApplicationExitCall,
            ):
                return
        except Exception:  # noqa: BLE001
            pass
        if self.hide_to_tray():
            args.Cancel = True

    def show(self) -> None:
        native = getattr(self._window, "native", None)
        minimized = False
        try:
            minimized = native is not None and native.WindowState == self._minimized_state
        except Exception:  # noqa: BLE001
            pass
        try:
            self._window.show()
            if minimized:
                self._window.restore()
        except Exception:  # noqa: BLE001
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = _hwnd(self._window)
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass

    def hide_to_tray(self) -> bool:
        if not self._ready:
            return False
        try:
            native = getattr(self._window, "native", None)
            if native is not None:
                native.Hide()
            else:
                self._window.hide()
        except Exception:  # noqa: BLE001
            return False
        if not self._balloon_shown and self._notify is not None:
            self._balloon_shown = True
            try:
                self._notify.ShowBalloonTip(
                    2500,
                    "PyShield 仍在运行",
                    "双击右下角托盘图标可重新打开窗口，右键可退出。",
                    self._tooltip_info,
                )
            except Exception:  # noqa: BLE001
                pass
        return True

    def quit(self) -> None:
        if self._can_quit is not None:
            try:
                if not self._can_quit():
                    if self._notify is not None:
                        self._notify.ShowBalloonTip(
                            2500,
                            "构建进行中",
                            "请先取消或等待构建完成。",
                            self._tooltip_warning,
                        )
                    return
            except Exception:  # noqa: BLE001
                pass
        self._quitting = True
        self._dispose()
        try:
            self._window.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _dispose(self) -> None:
        notify = self._notify
        self._notify = None
        self._ready = False
        if notify is None:
            return
        try:
            notify.Visible = False
            notify.Dispose()
        except Exception:  # noqa: BLE001
            pass


def install(
    window,
    icon_path: Path | str | None = None,
    can_quit: Callable[[], bool] | None = None,
) -> TrayController | None:
    """Attach Windows tray support to *window*. No-op on other platforms."""
    if sys.platform != "win32":
        return None
    controller = TrayController(
        window,
        Path(icon_path) if icon_path is not None else None,
        can_quit,
    )
    window.events.shown += controller._on_shown
    return controller
