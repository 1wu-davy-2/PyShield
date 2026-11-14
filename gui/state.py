"""Dual SQLite state store for the desktop builder.

The canonical user data (settings and build history) lives in the user's
profile.  When the app runs from a writable install directory, a second copy is
kept next to the executable.  Both copies carry a digest of the same logical
state; if they disagree, the GUI treats the installation as tampered and blocks
state-changing operations until the user picks a copy to restore.

This is tamper *evidence*, not tamper prevention: an attacker who can modify
both copies consistently can bypass it.  It is deliberately local, small and
dependency-free (SQLite from the standard library).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import sys
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STATE_FILENAME = "state.sqlite3"
PENDING_FILENAME = "state.pending.json"
INCIDENT_DIRNAME = "state-incidents"
ENV_INSTALL_DIR = "PYSHELD_GUI_INSTALL_DIR"
SCHEMA_VERSION = 1
HISTORY_LIMIT = 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    position INTEGER PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


class StateError(Exception):
    """Base class for local state failures."""


class StateCorruptError(StateError):
    """A copy exists but is not a usable PyShield state database."""


class StateUnavailableError(StateError):
    """A copy cannot be read or written (for example, a read-only install)."""


class StateTamperError(StateError):
    """The two copies disagree, or a copy fails its own digest check."""


@dataclass(frozen=True)
class StateStatus:
    mode: str
    message: str
    user_path: str
    install_path: str
    install_available: bool
    generation: int = 0
    incident_dir: str = ""
    repair_sources: tuple[str, ...] = ()

    @property
    def tampered(self) -> bool:
        return self.mode == "tampered"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "tampered": self.tampered,
            "message": self.message,
            "user_path": self.user_path,
            "install_path": self.install_path,
            "install_available": self.install_available,
            "generation": self.generation,
            "incident_dir": self.incident_dir,
            "repair_sources": list(self.repair_sources),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _app_version() -> str:
    try:
        from pyshield import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - only when imported outside the app
        return "unknown"


def install_dir() -> Path | None:
    """Return the directory that contains the executable, if it is meaningful.

    In a source checkout there is no installation boundary, so the store runs
    in user-only mode unless ``PYSHELD_GUI_INSTALL_DIR`` is set.  PyInstaller
    sets ``sys.frozen`` and ``sys.executable`` points at the outer executable
    (not the temporary extraction directory).  Nuitka's onefile build exposes
    the original directory through ``__compiled__.containing_dir``.
    """
    override = os.environ.get(ENV_INSTALL_DIR)
    if override:
        try:
            return Path(override).expanduser().resolve()
        except OSError:
            return Path(override).expanduser().absolute()

    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except OSError:
            return Path(sys.executable).absolute().parent

    compiled = globals().get("__compiled__")
    containing = getattr(compiled, "containing_dir", None)
    if containing:
        try:
            return Path(containing).resolve()
        except OSError:
            return Path(containing).absolute()
    return None


def default_paths(config_dir: Path | str) -> tuple[Path, Path | None]:
    """Return ``(user_db, install_db)`` for a config directory."""
    user = Path(config_dir).expanduser() / STATE_FILENAME
    base = install_dir()
    if base is None:
        return user, None
    try:
        same = base.resolve() == Path(config_dir).expanduser().resolve()
    except OSError:
        same = False
    if same:
        return user, None
    return user, base / STATE_FILENAME


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _ensure_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(_connect(path)) as connection:
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
    except (OSError, sqlite3.Error) as exc:
        raise StateUnavailableError(f"无法创建状态库 {path}: {exc}") from exc


def _json_value(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_payload(document: dict) -> dict:
    meta = document.get("meta") or {}
    return {
        "schema_version": int(meta.get("schema_version") or 0),
        "install_id": str(meta.get("install_id") or ""),
        "created_at": str(meta.get("created_at") or ""),
        "settings": document.get("settings") or {},
        "history": document.get("history") or [],
    }


def _digest(document: dict) -> str:
    body = _json_value(_canonical_payload(document)).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _read_document(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with closing(_connect(path)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise StateCorruptError(f"状态库完整性检查失败：{path}")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, SCHEMA_VERSION):
                raise StateCorruptError(f"不支持的状态库版本：{version}")
            meta = {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key, value FROM meta")
            }
            if str(meta.get("schema_version") or "") != str(SCHEMA_VERSION):
                raise StateCorruptError("状态库缺少正确的 schema_version")
            settings = {
                str(row["key"]): json.loads(row["value"])
                for row in connection.execute("SELECT key, value FROM settings")
            }
            history = [
                json.loads(row["payload"])
                for row in connection.execute(
                    "SELECT payload FROM history ORDER BY position"
                )
            ]
    except StateError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise StateCorruptError(f"无法读取状态库 {path}: {exc}") from exc

    if not isinstance(settings, dict):
        raise StateCorruptError(f"状态库 settings 不是对象：{path}")
    if not all(isinstance(entry, dict) for entry in history):
        raise StateCorruptError(f"状态库 history 包含非对象条目：{path}")

    document = {"meta": meta, "settings": settings, "history": history}
    expected = str(meta.get("digest") or "")
    actual = _digest(document)
    if not expected or not hmac.compare_digest(expected, actual):
        raise StateTamperError(f"状态库内容摘要不匹配：{path}")
    try:
        int(meta.get("generation") or "0")
    except ValueError as exc:
        raise StateCorruptError(f"状态库 generation 无效：{path}") from exc
    return document


def _seal(document: dict, generation: int) -> dict:
    meta = dict(document.get("meta") or {})
    meta["schema_version"] = str(SCHEMA_VERSION)
    meta["app_version"] = _app_version()
    meta["updated_at"] = _now()
    meta["generation"] = str(generation)
    meta["digest"] = ""
    sealed = {
        "meta": meta,
        "settings": dict(document.get("settings") or {}),
        "history": [dict(entry) for entry in (document.get("history") or [])][:HISTORY_LIMIT],
    }
    meta["digest"] = _digest(sealed)
    return sealed


def _write_document(path: Path, document: dict) -> None:
    _ensure_schema(path)
    meta = document["meta"]
    try:
        with closing(_connect(path)) as connection:
            with connection:
                connection.execute("DELETE FROM settings")
                connection.execute("DELETE FROM history")
                connection.executemany(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    [(key, _json_value(value)) for key, value in document["settings"].items()],
                )
                connection.executemany(
                    "INSERT INTO history (position, payload) VALUES (?, ?)",
                    [
                        (position, _json_value(entry))
                        for position, entry in enumerate(document["history"])
                    ],
                )
                for key in (
                    "schema_version",
                    "install_id",
                    "app_version",
                    "created_at",
                    "updated_at",
                    "generation",
                    "digest",
                ):
                    connection.execute(
                        "INSERT INTO meta (key, value) VALUES (?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, str(meta[key])),
                    )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except (OSError, sqlite3.Error) as exc:
        raise StateUnavailableError(f"无法写入状态库 {path}: {exc}") from exc


def _new_document(settings: dict | None = None, history: list[dict] | None = None) -> dict:
    now = _now()
    return {
        "meta": {
            "schema_version": str(SCHEMA_VERSION),
            "install_id": secrets.token_hex(16),
            "app_version": _app_version(),
            "created_at": now,
            "updated_at": now,
            "generation": "0",
            "digest": "",
        },
        "settings": dict(settings or {}),
        "history": [dict(entry) for entry in (history or [])][:HISTORY_LIMIT],
    }


def _copy_document(document: dict) -> dict:
    return {
        "meta": dict(document.get("meta") or {}),
        "settings": dict(document.get("settings") or {}),
        "history": [dict(entry) for entry in (document.get("history") or [])],
    }


def _generation(document: dict | None) -> int:
    if not document:
        return 0
    try:
        return int((document.get("meta") or {}).get("generation") or 0)
    except (TypeError, ValueError):
        return 0


def _copy_with_sidecars(source: Path, target_dir: Path) -> None:
    if not source.is_file():
        return
    shutil.copy2(source, target_dir / source.name)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = source.with_name(source.name + suffix)
        if sidecar.is_file():
            try:
                shutil.copy2(sidecar, target_dir / sidecar.name)
            except OSError:
                pass


def _probe_writable(directory: Path) -> bool:
    probe = directory / f".pyshield-state-{secrets.token_hex(4)}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink()
        except OSError:
            pass
        return False


def _marker_path(directory: Path) -> Path:
    return directory / PENDING_FILENAME


def _read_marker(directory: Path) -> dict | None:
    path = _marker_path(directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_marker(directory: Path, marker: dict) -> bool:
    path = _marker_path(directory)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary.write_text(_json_value(marker) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False


def _remove_marker(directory: Path) -> None:
    try:
        _marker_path(directory).unlink()
    except OSError:
        pass


class StateStore:
    """Read and write the user/install pair of state databases."""

    def __init__(
        self,
        user_path: Path,
        install_path: Path | None = None,
        legacy_settings_path: Path | None = None,
        legacy_history_path: Path | None = None,
    ) -> None:
        self.user_path = Path(user_path)
        self.install_path = Path(install_path) if install_path else None
        self.legacy_settings_path = Path(legacy_settings_path) if legacy_settings_path else None
        self.legacy_history_path = Path(legacy_history_path) if legacy_history_path else None
        self._lock = threading.RLock()
        self._tamper_signature: tuple | None = None
        self._tamper_status: StateStatus | None = None

    # -- public API -------------------------------------------------------

    def load_settings(self) -> dict:
        with self._lock:
            document = self._sync()["document"]
        return dict(document.get("settings") or {}) if document else {}

    def load_history(self) -> list[dict]:
        with self._lock:
            document = self._sync()["document"]
        return [dict(entry) for entry in (document.get("history") or [])] if document else []

    def status(self) -> dict:
        with self._lock:
            return self._sync()["status"].to_dict()

    def save_settings(self, settings: dict) -> None:
        with self._lock:
            context = self._sync()
            self._require_not_tampered(context["status"])
            self._commit(settings=settings, history=None, context=context)

    def save_history(self, history: list[dict]) -> None:
        with self._lock:
            context = self._sync()
            self._require_not_tampered(context["status"])
            self._commit(settings=None, history=history, context=context)

    def repair(self, source: str) -> dict:
        with self._lock:
            source = str(source or "").strip().lower()
            paths = {"user": self.user_path, "install": self.install_path}
            if source not in paths or paths[source] is None:
                raise StateError("请选择要保留的状态副本。")
            source_path = paths[source]
            source_document = _read_document(source_path)
            if source_document is None:
                raise StateError("所选副本不存在。")
            if self.install_path is not None and not _probe_writable(self.install_path.parent):
                raise StateUnavailableError("安装目录不可写，无法完成双副本修复。")

            generation = max(
                _generation(_read_safely(self.user_path)),
                _generation(_read_safely(self.install_path)) if self.install_path else 0,
                _generation(source_document),
            ) + 1
            document = _seal(source_document, generation)
            self._write_pair(document, generation, previous=None)
            self._clear_tamper()
            self._log("repair", f"source={source} generation={generation}")
            return self.status()

    # -- consistency ------------------------------------------------------

    def _sync(self) -> dict:
        user_document = None
        install_document = None
        user_error: StateError | None = None
        install_error: StateError | None = None

        try:
            user_document = _read_document(self.user_path)
        except StateUnavailableError as exc:
            user_error = exc
        except StateError as exc:
            user_error = exc

        install_available = self.install_path is not None and _probe_writable(
            self.install_path.parent
        )
        if self.install_path is not None:
            try:
                install_document = _read_document(self.install_path)
            except StateUnavailableError as exc:
                install_error = exc
            except StateError as exc:
                install_error = exc

        if user_document is None and install_document is None:
            if user_error is None and install_error is None:
                return self._bootstrap()
            signature = self._signature(None, None, user_error, install_error)
            return self._tampered("用户目录与安装目录都没有可用的状态库。", signature)

        if user_error is not None or install_error is not None:
            if (
                install_error is not None
                and user_document is not None
                and isinstance(install_error, StateUnavailableError)
            ):
                return self._single_copy(
                    "安装目录状态库不可读，仅使用用户目录副本。",
                    user_document,
                )
            if (
                user_error is not None
                and install_document is not None
                and isinstance(user_error, StateUnavailableError)
            ):
                return self._single_copy(
                    "用户目录状态库不可读，仅使用安装目录副本。",
                    install_document,
                )
            signature = self._signature(user_document, install_document, user_error, install_error)
            return self._tampered(
                "其中一个状态副本损坏，请选择可用副本修复。",
                signature,
                document=user_document or install_document,
            )

        if user_document is None and install_document is not None:
            return self._recover_from(
                install_document,
                self.user_path,
                "用户目录副本缺失，已从安装目录恢复。",
            )

        if install_document is None and user_document is not None:
            if install_available:
                return self._recover_from(
                    user_document,
                    self.install_path,
                    "安装目录副本缺失，已重新创建。",
                )
            return self._single_copy(
                "安装目录不可写，仅使用用户目录副本。",
                user_document,
            )

        assert user_document is not None and install_document is not None
        if not install_available:
            return self._single_copy(
                "安装目录不可写，未参与双副本校验；仅使用用户目录副本。",
                user_document,
            )

        marker = self._marker()
        if marker is not None:
            recovered = self._recover_pending_write(
                user_document, install_document, marker
            )
            if recovered is not None:
                return recovered

        user_meta = user_document["meta"]
        install_meta = install_document["meta"]
        if (
            str(user_meta.get("digest")) != str(install_meta.get("digest"))
            or _generation(user_document) != _generation(install_document)
            or str(user_meta.get("install_id")) != str(install_meta.get("install_id"))
        ):
            signature = self._signature(user_document, install_document, None, None)
            return self._tampered(
                "用户目录与安装目录状态库不一致，可能被篡改。请选择要保留的副本。",
                signature,
                document=user_document,
            )

        self._clear_tamper()
        return {
            "document": user_document,
            "status": StateStatus(
                mode="ok",
                message="用户目录与安装目录状态副本一致。",
                user_path=str(self.user_path),
                install_path=str(self.install_path or ""),
                install_available=install_available,
                generation=_generation(user_document),
                repair_sources=("user", "install") if self.install_path else ("user",),
            ),
        }

    def _bootstrap(self) -> dict:
        settings, history = self._legacy_seed()
        document = _seal(_new_document(settings, history), 1)
        install_available = self.install_path is not None and _probe_writable(
            self.install_path.parent
        )
        try:
            self._write_pair(document, 1, previous=None, install=install_available)
        except StateError as exc:
            return {
                "document": None,
                "status": StateStatus(
                    mode="error",
                    message=f"无法创建本机状态库：{exc}",
                    user_path=str(self.user_path),
                    install_path=str(self.install_path or ""),
                    install_available=False,
                    repair_sources=(),
                ),
            }
        self._log("bootstrap", "created user and install state")
        status = StateStatus(
            mode="ok" if install_available else "single-copy",
            message=(
                "已创建用户目录与安装目录状态副本。"
                if install_available
                else "已创建用户目录状态库；安装目录不可写，双副本校验未启用。"
            ),
            user_path=str(self.user_path),
            install_path=str(self.install_path or ""),
            install_available=install_available,
            generation=1,
            repair_sources=("user", "install") if install_available else ("user",),
        )
        return {"document": document, "status": status}

    def _recover_from(self, document: dict, target: Path, message: str) -> dict:
        try:
            _write_document(target, document)
        except StateError:
            return self._single_copy(
                "一个状态副本缺失，但无法写入目标目录；仅使用现有副本。",
                document,
            )
        self._log("mirror-restored", f"target={target}")
        return {
            "document": document,
            "status": StateStatus(
                mode="recovered",
                message=message,
                user_path=str(self.user_path),
                install_path=str(self.install_path or ""),
                install_available=self.install_path is not None,
                generation=_generation(document),
                repair_sources=("user", "install") if self.install_path else ("user",),
            ),
        }

    def _marker(self) -> dict | None:
        for directory in (self.user_path.parent, self.install_path.parent if self.install_path else None):
            if directory is None:
                continue
            marker = _read_marker(directory)
            if marker is not None:
                return marker
        return None

    def _recover_pending_write(
        self, user_document: dict, install_document: dict, marker: dict
    ) -> dict | None:
        try:
            new_generation = int(marker.get("generation") or 0)
            old_generation = int(marker.get("previous_generation") or 0)
            new_digest = str(marker.get("digest") or "")
            old_digest = str(marker.get("previous_digest") or "")
        except (TypeError, ValueError):
            return None

        user_new = (
            _generation(user_document) == new_generation
            and str(user_document["meta"].get("digest")) == new_digest
        )
        install_new = (
            _generation(install_document) == new_generation
            and str(install_document["meta"].get("digest")) == new_digest
        )
        user_old = (
            _generation(user_document) == old_generation
            and str(user_document["meta"].get("digest")) == old_digest
        )
        install_old = (
            _generation(install_document) == old_generation
            and str(install_document["meta"].get("digest")) == old_digest
        )

        if (user_new and install_new) or (user_old and install_old):
            self._clear_markers()
            if user_new and install_new:
                self._clear_tamper()
                return {
                    "document": user_document,
                    "status": StateStatus(
                        mode="ok",
                        message="状态副本一致。",
                        user_path=str(self.user_path),
                        install_path=str(self.install_path or ""),
                        install_available=self.install_path is not None,
                        generation=_generation(user_document),
                        repair_sources=("user", "install") if self.install_path else ("user",),
                    ),
                }
            return None

        if user_new and install_old:
            target = self.install_path
            document = user_document
        elif install_new and user_old:
            target = self.user_path
            document = install_document
        else:
            return None

        if target is None:
            return None
        try:
            _write_document(target, document)
        except StateError:
            return None
        self._clear_markers()
        self._clear_tamper()
        self._log("pending-write-recovered", f"target={target}")
        return {
            "document": document,
            "status": StateStatus(
                mode="recovered",
                message="检测到一次中断的双副本写入，已从较新的副本恢复。",
                user_path=str(self.user_path),
                install_path=str(self.install_path or ""),
                install_available=self.install_path is not None,
                generation=_generation(document),
                repair_sources=("user", "install") if self.install_path else ("user",),
            ),
        }

    def _single_copy(self, message: str, document: dict) -> dict:
        return {
            "document": document,
            "status": StateStatus(
                mode="single-copy",
                message=message,
                user_path=str(self.user_path),
                install_path=str(self.install_path or ""),
                install_available=False,
                generation=_generation(document),
                repair_sources=("user",),
            ),
        }

    # -- writes -----------------------------------------------------------

    def _commit(self, settings: dict | None, history: list[dict] | None, context: dict) -> None:
        current = context["document"] or _new_document()
        document = _copy_document(current)
        if settings is not None:
            document["settings"] = dict(settings)
        if history is not None:
            document["history"] = [dict(entry) for entry in history][:HISTORY_LIMIT]
        document["meta"]["schema_version"] = str(SCHEMA_VERSION)
        document["meta"]["app_version"] = _app_version()
        document["meta"]["install_id"] = str(document["meta"].get("install_id") or secrets.token_hex(16))
        document["meta"]["created_at"] = str(document["meta"].get("created_at") or _now())
        previous_generation = _generation(current)
        generation = previous_generation + 1
        sealed = _seal(document, generation)
        self._write_pair(sealed, generation, previous=current)
        self._clear_tamper()
        self._log("save", f"generation={generation}")

    def _write_pair(
        self,
        document: dict,
        generation: int,
        previous: dict | None,
        *,
        install: bool | None = None,
    ) -> None:
        install_enabled = (
            self.install_path is not None
            and _probe_writable(self.install_path.parent)
            if install is None
            else install
        )
        old_generation = _generation(previous)
        marker = {
            "at": _now(),
            "generation": generation,
            "digest": str(document["meta"].get("digest") or ""),
            "previous_generation": old_generation,
            "previous_digest": str((previous or {}).get("meta", {}).get("digest") or ""),
            "token": secrets.token_hex(8),
        }
        user_marker = _write_marker(self.user_path.parent, marker)
        install_marker = (
            _write_marker(self.install_path.parent, marker)
            if install_enabled and self.install_path is not None
            else False
        )
        if not user_marker:
            raise StateUnavailableError(f"无法写入用户目录状态库：{self.user_path}")

        try:
            if install_enabled and self.install_path is not None:
                _write_document(self.install_path, document)
            _write_document(self.user_path, document)
        except StateError:
            # Leave the pending marker in place; the next launch can recover
            # from the copy that carries the new generation.
            if install_enabled and self.install_path is not None and not install_marker:
                _remove_marker(self.install_path.parent)
            raise
        else:
            _remove_marker(self.user_path.parent)
            if self.install_path is not None:
                _remove_marker(self.install_path.parent)

    # -- helpers ----------------------------------------------------------

    def _legacy_seed(self) -> tuple[dict, list[dict]]:
        settings: dict = {}
        history: list[dict] = []
        if self.legacy_settings_path is not None:
            try:
                payload = json.loads(self.legacy_settings_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
            if isinstance(payload, dict):
                settings = dict(payload)
        if self.legacy_history_path is not None:
            try:
                payload = json.loads(self.legacy_history_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
            if isinstance(payload, list):
                history = [dict(entry) for entry in payload if isinstance(entry, dict)]
        return settings, history[:HISTORY_LIMIT]

    def _require_not_tampered(self, status: StateStatus) -> None:
        if status.tampered:
            raise StateTamperError(status.message)

    def _signature(
        self,
        user_document: dict | None,
        install_document: dict | None,
        user_error: StateError | None,
        install_error: StateError | None,
    ) -> tuple:
        def part(path: Path | None, document: dict | None, error: StateError | None):
            if document is not None:
                return (
                    str(document["meta"].get("generation")),
                    str(document["meta"].get("digest")),
                )
            if error is not None:
                return ("error", str(error))
            return ("missing", str(path) if path else "")

        return (
            part(self.user_path, user_document, user_error),
            part(self.install_path, install_document, install_error),
        )

    def _tampered(
        self,
        message: str,
        signature: tuple,
        *,
        document: dict | None = None,
    ) -> dict:
        if self._tamper_signature == signature and self._tamper_status is not None:
            return {"document": document, "status": self._tamper_status}

        incident = self._quarantine(message)
        status = StateStatus(
            mode="tampered",
            message=message,
            user_path=str(self.user_path),
            install_path=str(self.install_path or ""),
            install_available=self.install_path is not None,
            generation=_generation(document),
            incident_dir=str(incident or ""),
            repair_sources=tuple(
                source
                for source, path in (("user", self.user_path), ("install", self.install_path))
                if path is not None and _read_safely(path) is not None
            ),
        )
        self._tamper_signature = signature
        self._tamper_status = status
        self._log("mismatch", message)
        return {"document": document, "status": status}

    def _quarantine(self, message: str) -> Path | None:
        stamp = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + secrets.token_hex(4)
        )
        directory = self.user_path.parent / INCIDENT_DIRNAME / stamp
        try:
            directory.mkdir(parents=True, exist_ok=False)
            _copy_with_sidecars(self.user_path, directory)
            if self.install_path is not None:
                _copy_with_sidecars(self.install_path, directory)
            report = {
                "at": _now(),
                "message": message,
                "user_path": str(self.user_path),
                "install_path": str(self.install_path or ""),
                "winner": None,
            }
            (directory / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return directory
        except OSError:
            return None

    def _clear_tamper(self) -> None:
        self._tamper_signature = None
        self._tamper_status = None

    def _clear_markers(self) -> None:
        _remove_marker(self.user_path.parent)
        if self.install_path is not None:
            _remove_marker(self.install_path.parent)

    def _log(self, kind: str, detail: str) -> None:
        for path in (self.user_path, self.install_path):
            if path is None or not path.is_file():
                continue
            try:
                with closing(_connect(path)) as connection:
                    with connection:
                        connection.execute(
                            "INSERT INTO audit (at, kind, detail) VALUES (?, ?, ?)",
                            (_now(), kind, detail),
                        )
            except (OSError, sqlite3.Error):
                continue


def _read_safely(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        return _read_document(path)
    except StateError:
        return None


_STORES: dict[tuple[str, str, str, str], StateStore] = {}
_STORES_LOCK = threading.Lock()


def get_store(
    user_path: Path,
    install_path: Path | None = None,
    legacy_settings_path: Path | None = None,
    legacy_history_path: Path | None = None,
) -> StateStore:
    key = (
        str(user_path),
        str(install_path or ""),
        str(legacy_settings_path or ""),
        str(legacy_history_path or ""),
    )
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = StateStore(
                user_path,
                install_path,
                legacy_settings_path=legacy_settings_path,
                legacy_history_path=legacy_history_path,
            )
            _STORES[key] = store
        return store


def clear_store_cache() -> None:
    """Drop cached stores (used by tests and after changing state paths)."""
    with _STORES_LOCK:
        _STORES.clear()
