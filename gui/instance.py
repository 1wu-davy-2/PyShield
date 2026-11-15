"""Single-instance coordination and ``pyshield://`` protocol registration.

The trial site redirects back to the desktop app with ``pyshield://activate``.
Windows starts a second process for that URL, so the process either forwards the
link to the instance that is already running or becomes the primary instance and
listens on a loopback port.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import sys
import threading
from pathlib import Path

from . import core

INSTANCE_FILE = "instance.json"
INSTANCE_NAME = "PyShieldBuilder.SingleInstance"
ENV_ALLOW_MULTI = "PYSHELD_ALLOW_MULTI_INSTANCE"
PROTOCOL_SCHEME = "pyshield"
ENV_SKIP_PROTOCOL = "PYSHELD_SKIP_PROTOCOL"
MAX_MESSAGE = 64 * 1024


class InstanceLock:
    """Process-lifetime single-instance lock.

    A named Windows mutex (or a POSIX flock) prevents a second process from
    starting a second window even when the config directories differ. The
    loopback pointer remains responsible only for forwarding deep links to the
    elected primary.
    """

    def __init__(self, handle=None, file=None, bypassed: bool = False) -> None:
        self._handle = handle
        self._file = file
        self._bypassed = bypassed

    @property
    def bypassed(self) -> bool:
        return self._bypassed

    def release(self) -> None:
        if self._handle is not None:
            try:
                import ctypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel32.CloseHandle.restype = ctypes.c_int
                kernel32.CloseHandle(self._handle)
            except (ImportError, OSError):
                pass
            self._handle = None
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None


def _acquire_windows_mutex(name: str) -> InstanceLock | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
        if not handle:
            # A broken/restricted mutex must not make the GUI unusable.
            return InstanceLock()
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return None
        return InstanceLock(handle=handle)
    except (ImportError, OSError):
        return InstanceLock()


def _acquire_posix_lock(name: str) -> InstanceLock | None:
    try:
        import fcntl

        base = Path(os.environ.get("XDG_RUNTIME_DIR") or (Path.home() / ".pyshield"))
        base.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        handle = open(base / f"{safe}.lock", "a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n".encode("ascii"))
        handle.flush()
        return InstanceLock(file=handle)
    except (ImportError, OSError, RuntimeError):
        return InstanceLock()


def acquire_single_instance(name: str | None = None) -> InstanceLock | None:
    """Elect one primary GUI process.

    Returns ``None`` when another process owns the lock. Set
    ``PYSHELD_ALLOW_MULTI_INSTANCE=1`` only for automated parallel UI tests.
    """
    value = os.environ.get(ENV_ALLOW_MULTI, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return InstanceLock(bypassed=True)
    instance_name = name or INSTANCE_NAME
    if os.name == "nt":
        return _acquire_windows_mutex(instance_name)
    return _acquire_posix_lock(instance_name)


def pointer_path() -> Path:
    return core.config_dir() / INSTANCE_FILE


def _read_pointer() -> dict | None:
    try:
        payload = json.loads(pointer_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_pointer(port: int, token: str) -> None:
    target = pointer_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {"port": port, "token": token, "pid": os.getpid(), "version": 1}
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _clear_pointer() -> None:
    try:
        pointer_path().unlink()
    except OSError:
        pass


def send_to_primary(payload: dict, timeout: float = 3.0) -> bool:
    """Forward *payload* to the running instance; returns True when accepted."""
    pointer = _read_pointer()
    if not pointer:
        return False
    port = int(pointer.get("port") or 0)
    token = str(pointer.get("token") or "")
    if not port or not token:
        return False
    message = json.dumps({**payload, "token": token}, ensure_ascii=False) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
            connection.sendall(message.encode("utf-8"))
            connection.settimeout(timeout)
            reply = connection.recv(64)
    except OSError:
        return False
    return reply.strip() == b"ok"


class LinkServer:
    """Loopback listener that hands deep links to the running window."""

    def __init__(self, on_link) -> None:
        self._on_link = on_link
        self._socket: socket.socket | None = None
        self._token = secrets.token_hex(16)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._socket.getsockname()[1] if self._socket else 0

    def start(self) -> bool:
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(8)
        except OSError:
            return False
        self._socket = server
        _write_pointer(self.port, self._token)
        self._thread = threading.Thread(target=self._serve, name="pyshield-links", daemon=True)
        self._thread.start()
        return True

    def _serve(self) -> None:
        assert self._socket is not None
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            with connection:
                try:
                    connection.settimeout(5.0)
                    raw = connection.recv(MAX_MESSAGE)
                    message = json.loads(raw.decode("utf-8"))
                    if not isinstance(message, dict) or message.get("token") != self._token:
                        connection.sendall(b"denied")
                        continue
                    connection.sendall(b"ok")
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
                link = str(message.get("link") or "")
                if link:
                    try:
                        self._on_link({"link": link})
                    except Exception:  # noqa: BLE001 - never kill the listener
                        pass

    def stop(self) -> None:
        _clear_pointer()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None


def protocol_registered() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL_SCHEME}") as key:
            command, _ = winreg.QueryValueEx(key, r"shell\open\command")
    except OSError:
        return False
    return bool(str(command).strip())


def register_protocol(executable: str | None = None) -> bool:
    """Register ``pyshield://`` for the current user (frozen builds only)."""
    if os.name != "nt" or os.environ.get(ENV_SKIP_PROTOCOL):
        return False
    if not getattr(sys, "frozen", False):
        return False
    target = executable or sys.executable
    if not target:
        return False
    try:
        import winreg
    except ImportError:
        return False
    command = f'"{target}" "%1"'
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL_SCHEME}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:PyShield Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL_SCHEME}\shell\open\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    except OSError:
        return False
    return True


def deep_link_from_argv(argv: list[str]) -> str:
    for item in argv[1:]:
        if str(item).lower().startswith(f"{PROTOCOL_SCHEME}://"):
            return str(item)
    return ""


def serial_from_link(link: str) -> str:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(link)
    if parsed.scheme.lower() != PROTOCOL_SCHEME:
        return ""
    values = parse_qs(parsed.query).get("serial") or []
    return str(values[0]).strip() if values else ""
