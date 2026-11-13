"""Local trial site used by development builds.

A build that ships no root public key cannot talk to the real licensing service,
so it is by definition a development build. Such builds start a throwaway trial
server on loopback with their own root key, which makes the whole registration
flow testable without any manual configuration:

* app starts -> the site is listening on 127.0.0.1:8090
* user clicks "apply trial" -> browser opens the local site
* the site signs a trial entitlement with the local dev key
* the deep link brings the app back and the trial is active

Release builds embed the root public key and never touch this module's server.
"""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

from . import ed25519, license_admin, tool_license, trial_server

ENV_DEV = "PYSHELD_DEV"
DEFAULT_PORT = 8090
DEFAULT_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
HEALTH_TIMEOUT = 0.4

_lock = threading.Lock()
_running: dict = {"server": None, "thread": None, "url": ""}


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def has_public_key() -> bool:
    try:
        return tool_license.active_public_key() is not None
    except tool_license.ToolLicenseError:
        # A broken key file is still a configured key: do not silently fall back.
        return True


def enabled() -> bool:
    """Development builds have no injected root key (or opt in explicitly)."""
    return _env_flag(ENV_DEV) or not has_public_key()


def data_dir() -> Path:
    return tool_license.state_dir() / "dev-site"


def _listening(url: str = DEFAULT_URL) -> bool:
    host, _, port_text = url.rpartition(":")
    host = host.split("//")[-1]
    try:
        port = int(port_text)
    except ValueError:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(HEALTH_TIMEOUT)
        return probe.connect_ex((host, port)) == 0


def running() -> bool:
    return _running["server"] is not None


def url() -> str:
    return str(_running.get("url") or "")


def public_key() -> bytes | None:
    """Public key of the local dev site, used to verify its entitlements."""
    key_path = data_dir() / "root.key"
    if not key_path.is_file():
        return None
    try:
        return ed25519.public_key(license_admin.load_root_key(key_path))
    except (license_admin.LicenseAdminError, OSError):
        return None


def ensure(port: int = DEFAULT_PORT) -> str:
    """Start the loopback site once and return its base URL."""
    with _lock:
        if _running["server"] is not None:
            return str(_running["url"])

        directory = data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        key_path = directory / "root.key"
        if not key_path.is_file():
            license_admin.generate_root(directory)
        root_key = license_admin.load_root_key(key_path)

        server = None
        for candidate in (port, 0):
            try:
                server = trial_server.create_server(
                    "127.0.0.1", candidate, root_key, directory / "data",
                )
                break
            except OSError:
                continue
        if server is None:
            raise OSError("cannot start the local trial site")

        thread = threading.Thread(target=server.serve_forever, name="pyshield-dev-site", daemon=True)
        thread.start()
        host, actual = server.server_address[:2]
        url = f"http://{host}:{actual}"
        _running.update(server=server, thread=thread, url=url)
        return url


def stop() -> None:
    with _lock:
        server = _running.get("server")
        _running.update(server=None, thread=None, url="")
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
                server.store.close()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
