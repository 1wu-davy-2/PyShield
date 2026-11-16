"""Shared helpers for the PyShield desktop builder.

The desktop app runs the protection engine in-process, so these helpers only
cover form validation, interpreter discovery, defaults and persisted settings.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import state as state_db

APP_TITLE = "PyShield 加密构建器"
APP_DIR_NAME = "PyShieldBuilder"
DEFAULT_OUTPUT_SUFFIX = "-shielded"
SECRET_SUFFIX = ".shield-secret.json"
MARKER_FILE = ".pyshield-build.json"
CONFIG_ENV = "PYSHELD_GUI_CONFIG_DIR"
SETTINGS_FILENAME = "settings.json"
HISTORY_FILENAME = "history.json"

CIPHERS = ("chacha-pure", "chacha", "aesgcm")
INTEGRITY_MODES = ("protected", "full", "off")
RUNTIME_MODES = ("source", "pyc", "native")
EXPIRY_MODES = ("never", "days", "date")
BINDING_MODES = ("any", "first-run", "fixed")
MIN_PYTHON = (3, 10)
MAX_GRACE_DAYS = 3650
MAX_EXPIRE_DAYS = 36500
HISTORY_LIMIT = 100
MCP_DEFAULT_PORT = 8790

DEFAULT_CIPHER = "chacha-pure"
DEFAULT_INTEGRITY = "protected"
DEFAULT_RUNTIME = "native"

EMBEDDED_PYTHON = f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"
EMBEDDED_PYTHON_MINOR = f"{sys.version_info[0]}.{sys.version_info[1]}"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_SETTINGS = {
    "cipher": DEFAULT_CIPHER,
    "integrity": DEFAULT_INTEGRITY,
    "runtime": DEFAULT_RUNTIME,
    "output_suffix": DEFAULT_OUTPUT_SUFFIX,
    "auto_detect_python": True,
    "no_verify": False,
    "activation_server": "",
    "trial_server": "",
    # Embedded MCP endpoint (see gui/mcp_host.py).
    "mcp_enabled": True,
    "mcp_port": MCP_DEFAULT_PORT,
    "mcp_allow_build": True,
    "mcp_project_root": "",
    "onboarding_seen": False,
}

PROBE_SCRIPT = r"""
import importlib.util
import json
import sys

def has(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False

def version(name):
    try:
        from importlib.metadata import version as dist_version
        return dist_version(name)
    except Exception:
        return None

print("PYSHELD_PROBE:" + json.dumps({
    "version": sys.version.split()[0],
    "major_minor": list(sys.version_info[:2]),
    "cryptography": has("cryptography"),
    "cryptography_version": version("cryptography"),
    "django": has("django"),
    "django_version": version("django"),
}))
"""


@dataclass
class BuildRequest:
    project: str = ""
    output: str = ""
    verify_python: str = ""
    cipher: str = DEFAULT_CIPHER
    integrity: str = DEFAULT_INTEGRITY
    runtime: str = DEFAULT_RUNTIME
    expiry_mode: str = "never"
    expire_days: str = "30"
    expire_date: str = ""
    grace_days: str = "0"
    bind_mode: str = "any"
    fingerprints: list[str] = field(default_factory=list)
    customer: str = ""
    encrypt_migrations: bool = False
    encrypt_tests: bool = False
    mutable_patterns: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    no_verify: bool = False
    force: bool = False

    @classmethod
    def from_payload(cls, payload: dict | None) -> "BuildRequest":
        data = payload if isinstance(payload, dict) else {}
        request = cls()
        request.project = str(data.get("project") or "").strip()
        request.output = str(data.get("output") or "").strip()
        request.verify_python = str(data.get("verify_python") or "").strip()
        request.cipher = str(data.get("cipher") or DEFAULT_CIPHER)
        request.integrity = str(data.get("integrity") or DEFAULT_INTEGRITY)
        request.runtime = str(data.get("runtime") or DEFAULT_RUNTIME)
        request.expiry_mode = str(data.get("expiry_mode") or "never")
        request.expire_days = str(data.get("expire_days") or "30").strip()
        request.expire_date = str(data.get("expire_date") or "").strip()
        request.grace_days = str(data.get("grace_days") or "0").strip()
        request.fingerprints = [
            str(item).strip().lower()
            for item in (data.get("fingerprints") or [])
            if str(item).strip()
        ]
        bind_mode = str(data.get("bind_mode") or "").strip()
        if bind_mode in BINDING_MODES:
            request.bind_mode = bind_mode
        else:
            request.bind_mode = "fixed" if request.fingerprints else "any"
        if request.bind_mode != "fixed":
            request.fingerprints = []
        request.customer = str(data.get("customer") or "").strip()
        request.encrypt_migrations = bool(data.get("encrypt_migrations"))
        request.encrypt_tests = bool(data.get("encrypt_tests"))
        request.mutable_patterns = split_patterns(str(data.get("mutable_patterns") or ""))
        request.excludes = split_patterns(str(data.get("excludes") or ""))
        request.no_verify = bool(data.get("no_verify"))
        request.force = bool(data.get("force"))
        return request

    def expire_value(self) -> str | None:
        if self.expiry_mode == "days":
            return str(self.expire_days).strip() or None
        if self.expiry_mode == "date":
            return str(self.expire_date).strip() or None
        return None


@dataclass
class PythonInfo:
    executable: str
    version: str
    major_minor: tuple[int, int]
    cryptography: bool = False
    cryptography_version: str | None = None
    django: bool = False
    django_version: str | None = None

    def summary(self) -> str:
        parts = [f"Python {self.version}"]
        if self.django:
            parts.append(f"Django {self.django_version}" if self.django_version else "Django 已安装")
        else:
            parts.append("Django 未安装")
        if self.cryptography:
            parts.append(
                f"cryptography {self.cryptography_version}" if self.cryptography_version
                else "cryptography 已安装"
            )
        return " · ".join(parts)

    def compatible_with_embedded(self) -> bool:
        return tuple(self.major_minor) == tuple(sys.version_info[:2])

    def to_dict(self) -> dict:
        return {
            "executable": self.executable,
            "version": self.version,
            "summary": self.summary(),
            "django": self.django,
            "django_version": self.django_version,
            "cryptography": self.cryptography,
            "cryptography_version": self.cryptography_version,
            "compatible": self.compatible_with_embedded(),
        }


def default_output(project: str, suffix: str = DEFAULT_OUTPUT_SUFFIX) -> Path:
    path = Path(project).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    name = resolved.name or "project"
    return resolved.parent / f"{name}{suffix or DEFAULT_OUTPUT_SUFFIX}"


def secret_path_for(output: str | Path) -> Path:
    target = Path(output)
    return target.parent / (target.name + SECRET_SUFFIX)


def split_patterns(text: str) -> list[str]:
    parts = re.split(r"[,;\n，；]+", text or "")
    result: list[str] = []
    for part in parts:
        value = part.strip()
        if value and value not in result:
            result.append(value)
    return result


def split_fingerprints(text: str) -> list[str]:
    parts = re.split(r"[\s,;，；]+", text or "")
    result: list[str] = []
    for part in parts:
        value = part.strip().lower()
        if value and value not in result:
            result.append(value)
    return result


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def is_within(child: Path | str, parent: Path | str) -> bool:
    child_norm = _norm(child)
    parent_norm = _norm(parent)
    if child_norm == parent_norm:
        return True
    return child_norm.startswith(parent_norm.rstrip("\\/") + os.sep)


def resolve_python(value: str) -> str | None:
    text = (value or "").strip().strip('"')
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_file():
        return str(path)
    if os.sep in text or "/" in text or (os.altsep and os.altsep in text):
        return None
    found = shutil.which(text)
    return found or None


def find_python_candidates(project: str | None = None) -> list[str]:
    candidates: list[Path] = []
    override = os.environ.get("PYSHELD_PYTHON")
    if override:
        candidates.append(Path(override))
    if project:
        root = Path(project).expanduser()
        for relative in (
            ".venv/Scripts/python.exe",
            "venv/Scripts/python.exe",
            "env/Scripts/python.exe",
            ".virtualenv/Scripts/python.exe",
            ".venv/bin/python",
            "venv/bin/python",
            "env/bin/python",
        ):
            candidates.append(root / relative)
    if not getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable))
    for name in ("python.exe", "python3.exe", "python", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            key = os.path.normcase(str(candidate.resolve()))
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(str(candidate))
    return result


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def probe_python(executable: str, timeout: float = 20.0) -> PythonInfo:
    resolved = resolve_python(executable)
    if not resolved:
        raise RuntimeError(f"找不到 Python 解释器：{executable}")
    try:
        completed = subprocess.run(
            [resolved, "-c", PROBE_SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_child_env(), creationflags=CREATE_NO_WINDOW, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"无法运行 Python：{exc}") from exc
    if completed.returncode != 0:
        details = (completed.stdout + completed.stderr).strip()
        raise RuntimeError(details or f"Python 退出码 {completed.returncode}")
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("PYSHELD_PROBE:"):
            payload = json.loads(line[len("PYSHELD_PROBE:"):])
            return PythonInfo(
                executable=resolved,
                version=str(payload.get("version", "?")),
                major_minor=tuple(payload.get("major_minor", [0, 0])),
                cryptography=bool(payload.get("cryptography")),
                cryptography_version=payload.get("cryptography_version"),
                django=bool(payload.get("django")),
                django_version=payload.get("django_version"),
            )
    raise RuntimeError("无法解析 Python 环境信息")


def validate_request(request: BuildRequest) -> list[str]:
    errors: list[str] = []
    project = Path(request.project).expanduser()
    output = Path(request.output).expanduser()

    if not request.project.strip():
        errors.append("请选择 Django 项目目录。")
    elif not (project / "manage.py").is_file():
        errors.append(f"项目目录下找不到 manage.py：{project}")

    if not request.output.strip():
        errors.append("请填写输出目录。")
    if request.project.strip() and request.output.strip():
        if is_within(output, project) or is_within(project, output):
            errors.append("输出目录不能与项目目录相同、位于项目内部或包含项目目录。")

    if request.output.strip():
        if output.exists() and output.is_file():
            errors.append(f"输出路径已存在且不是目录：{output}")
        parent = output.parent
        if not parent.is_dir():
            errors.append(f"输出目录的上级目录不存在：{parent}")
        if output.is_dir():
            try:
                entries = any(output.iterdir())
            except OSError:
                entries = False
            if entries and not (output / MARKER_FILE).is_file() and not request.force:
                errors.append(
                    "输出目录非空且不是 PyShield 构建；请更换目录，"
                    "或勾选“允许覆盖非 PyShield 目录”。"
                )

    if request.verify_python.strip() and not resolve_python(request.verify_python):
        errors.append(f"校验用 Python 解释器无效：{request.verify_python}")

    if request.cipher not in CIPHERS:
        errors.append(f"未知加密算法：{request.cipher}")
    if request.integrity not in INTEGRITY_MODES:
        errors.append(f"未知完整性模式：{request.integrity}")
    if request.runtime not in RUNTIME_MODES:
        errors.append(f"未知 runtime 模式：{request.runtime}")
    if request.expiry_mode not in EXPIRY_MODES:
        errors.append(f"未知到期设置：{request.expiry_mode}")
    if request.bind_mode not in BINDING_MODES:
        errors.append(f"未知设备绑定方式：{request.bind_mode}")
    elif request.bind_mode == "fixed" and not request.fingerprints:
        errors.append("选择“指定指纹”时至少填写一个机器指纹。")

    if request.expiry_mode == "days":
        text = str(request.expire_days).strip()
        try:
            days = int(text)
        except ValueError:
            errors.append("有效天数必须是整数。")
        else:
            if not 1 <= days <= MAX_EXPIRE_DAYS:
                errors.append(f"有效天数必须在 1 到 {MAX_EXPIRE_DAYS} 之间。")
    elif request.expiry_mode == "date":
        text = str(request.expire_date).strip()
        try:
            value = date.fromisoformat(text)
        except ValueError:
            errors.append("到期日期格式应为 YYYY-MM-DD。")
        else:
            if value < date.today():
                errors.append("到期日期不能早于今天。")

    try:
        grace = int(str(request.grace_days).strip())
    except ValueError:
        errors.append("宽限天数必须是整数。")
    else:
        if not 0 <= grace <= MAX_GRACE_DAYS:
            errors.append(f"宽限天数必须在 0 到 {MAX_GRACE_DAYS} 之间。")

    for fingerprint in request.fingerprints:
        if not re.fullmatch(r"[0-9a-f]{32}", fingerprint):
            errors.append(f"机器指纹格式不正确（应为 32 位十六进制）：{fingerprint}")
    return errors


def format_bytes(size: int) -> str:
    value = float(max(0, int(size)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def config_dir() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILENAME


def history_path() -> Path:
    return config_dir() / HISTORY_FILENAME


def _normalized_settings(payload: dict | None) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(payload, dict):
        for key in DEFAULT_SETTINGS:
            if key in payload:
                settings[key] = payload[key]
    if settings.get("cipher") not in CIPHERS:
        settings["cipher"] = DEFAULT_CIPHER
    if settings.get("integrity") not in INTEGRITY_MODES:
        settings["integrity"] = DEFAULT_INTEGRITY
    if settings.get("runtime") not in RUNTIME_MODES:
        settings["runtime"] = DEFAULT_RUNTIME
    suffix = str(settings.get("output_suffix") or "").strip()
    settings["output_suffix"] = suffix or DEFAULT_OUTPUT_SUFFIX
    settings["auto_detect_python"] = bool(settings.get("auto_detect_python"))
    settings["no_verify"] = bool(settings.get("no_verify"))
    settings["activation_server"] = str(settings.get("activation_server") or "").strip().rstrip("/")
    settings["trial_server"] = str(settings.get("trial_server") or "").strip().rstrip("/")
    settings["mcp_enabled"] = bool(settings.get("mcp_enabled", True))
    settings["mcp_allow_build"] = bool(settings.get("mcp_allow_build", True))
    settings["mcp_project_root"] = str(settings.get("mcp_project_root") or "").strip()
    settings["onboarding_seen"] = bool(settings.get("onboarding_seen", False))
    try:
        port = int(settings.get("mcp_port") or MCP_DEFAULT_PORT)
    except (TypeError, ValueError):
        port = MCP_DEFAULT_PORT
    settings["mcp_port"] = port if 1024 <= port <= 65535 else MCP_DEFAULT_PORT
    return settings


def state_store() -> state_db.StateStore:
    """Return the dual SQLite store used for settings and build history."""
    user_path, install_path = state_db.default_paths(config_dir())
    return state_db.get_store(
        user_path,
        install_path,
        legacy_settings_path=settings_path(),
        legacy_history_path=history_path(),
    )


def state_status() -> dict:
    return state_store().status()


def state_repair(source: str) -> dict:
    return state_store().repair(source)


def load_settings() -> dict:
    return _normalized_settings(state_store().load_settings())


def save_settings(payload: dict) -> dict:
    # Merge over the stored values: callers often submit only the fields they
    # own, and an omitted key must not fall back to its default.
    merged = dict(load_settings())
    if isinstance(payload, dict):
        merged.update(payload)
    settings = _normalized_settings(merged)
    state_store().save_settings(settings)
    # Keep the legacy JSON export for users who inspect or back it up manually;
    # SQLite remains the source of truth.
    try:
        _write_json(settings_path(), settings)
    except OSError:
        pass
    return settings


def load_history() -> list[dict]:
    entries = state_store().load_history()
    return [entry for entry in entries if isinstance(entry, dict)][:HISTORY_LIMIT]


def save_history(entries: list[dict]) -> None:
    trimmed = [entry for entry in entries if isinstance(entry, dict)][:HISTORY_LIMIT]
    state_store().save_history(trimmed)
    try:
        _write_json(history_path(), trimmed)
    except OSError:
        pass


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)
