"""Cloud entitlement checks: quota consumption and periodic validation.

The portal API is still being specified (``docs/cloud-license-api.md``), so the
module is inert unless ``PYSHELD_CLOUD_API=1`` is set. It defines the contract
the desktop client will use so the build path and the UI can be wired up
before the server ships:

* ``preflight`` — ask the server whether this machine may start a build and
  how many quota units would be consumed.
* ``consume`` — reserve one unit right before a build starts.
* ``verify`` — periodic validation of ``license_id`` / ``serial`` / ``machine``;
  any mismatch must stop the app from building.

Nothing here talks to the network until the flag is on, and every helper
returns a plain dict so the GUI can serialise it directly.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.request
from typing import Callable

ENV_ENABLE = "PYSHELD_CLOUD_API"
ENV_BASE = "PYSHELD_CLOUD_API_BASE"
DEFAULT_INTERVAL = (60, 300)  # seconds, randomised per round


class CloudError(RuntimeError):
    """Raised when the cloud endpoint answers with an error payload."""


def enabled() -> bool:
    return os.environ.get(ENV_ENABLE, "").strip() in {"1", "true", "yes", "on"}


def base_url() -> str:
    return os.environ.get(ENV_BASE, "").strip().rstrip("/")


def _request(path: str, payload: dict, timeout: float = 8.0) -> dict:
    base = base_url()
    if not base:
        raise CloudError("未配置云端授权服务地址")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except OSError as exc:
        raise CloudError(f"云端授权服务不可达：{exc}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise CloudError("云端授权服务返回了非 JSON 响应") from exc


def identity(entitlement) -> dict:
    """Fields the server needs to bind a quota reservation to this install."""
    return {
        "license_id": getattr(entitlement, "license_id", "") if entitlement else "",
        "serial": getattr(entitlement, "serial", "") if entitlement else "",
        "machine": getattr(entitlement, "machine", "") if entitlement else "",
        "edition": getattr(entitlement, "edition", "") if entitlement else "",
        "revision": getattr(entitlement, "revision", 0) if entitlement else 0,
    }


def preflight(entitlement=None) -> dict:
    """Return ``{"ok": True, "skipped": True}`` until the API is enabled."""
    if not enabled():
        return {"ok": True, "skipped": True}
    return _request("/api/license/preflight", identity(entitlement))


def consume(entitlement=None, reason: str = "build") -> dict:
    if not enabled():
        return {"ok": True, "skipped": True}
    payload = identity(entitlement)
    payload["reason"] = reason
    return _request("/api/license/consume", payload)


def verify(entitlement=None) -> dict:
    if not enabled():
        return {"ok": True, "skipped": True}
    return _request("/api/license/verify", identity(entitlement))


def start_watchdog(
    entitlement_provider: Callable[[], object],
    on_blocked: Callable[[dict], None],
    on_error: Callable[[Exception], None] | None = None,
    interval: tuple[int, int] = DEFAULT_INTERVAL,
) -> threading.Thread | None:
    """Randomised 1–5 minute validation loop (disabled by default)."""
    if not enabled():
        return None

    def loop() -> None:
        while True:
            time.sleep(random.randint(*interval))
            try:
                result = verify(entitlement_provider())
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                if on_error is not None:
                    on_error(exc)
                continue
            if not result.get("ok", False):
                on_blocked(result)
                return

    thread = threading.Thread(target=loop, name="pyshield-cloud-check", daemon=True)
    thread.start()
    return thread
