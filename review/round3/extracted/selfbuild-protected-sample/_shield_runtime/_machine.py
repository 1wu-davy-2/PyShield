"""Stable machine fingerprint used for license device binding.

The same file is shipped inside the runtime package, so it must only depend on
the standard library and must produce identical results in both places.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

_FP_PREFIX = b"pyshield-machine-v1|"


def _windows_identifier() -> str | None:
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access) as key:
            machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        if machine_guid:
            return f"windows-guid:{machine_guid}"
    except OSError:
        pass

    try:
        import ctypes

        volume = ctypes.c_ulong(0)
        root = os.environ.get("SystemDrive", "C:") + "\\"
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), None, 0, ctypes.byref(volume), None, None, None, 0
        )
        if ok:
            return f"windows-volume:{volume.value:08x}"
    except (OSError, AttributeError):
        pass
    return None


def _linux_identifier() -> str | None:
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return f"linux-machine-id:{value}"
    return None


def _macos_identifier() -> str | None:
    try:
        output = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in output.splitlines():
        if "IOPlatformUUID" in line:
            value = line.split("=", 1)[-1].strip().strip('"')
            if value:
                return f"macos-uuid:{value}"
    return None


def _raw_identifier() -> str:
    if sys.platform.startswith("win"):
        value = _windows_identifier()
    elif sys.platform == "darwin":
        value = _macos_identifier()
    else:
        value = _linux_identifier()
    if value:
        return value
    return f"host:{platform.node()}|mac:{uuid.getnode():012x}"


def machine_fingerprint() -> str:
    """Return a 32 character hex fingerprint that is stable across reboots."""
    raw = _raw_identifier()
    return hashlib.sha256(_FP_PREFIX + raw.encode("utf-8")).hexdigest()[:32]
