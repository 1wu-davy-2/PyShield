"""Transport-neutral validation primitives for PyShield builds.

The desktop builder historically kept its form validation in ``gui.core``.  The
MCP build service must not import ``gui``, so the pure, dependency-free parts
live here and both callers share them.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator


CIPHERS = ("auto", "chacha-pure", "chacha", "aesgcm")
INTEGRITY_MODES = ("protected", "full", "off")
RUNTIME_MODES = ("auto", "source", "pyc", "native")
BINDING_MODES = ("any", "first-run", "fixed")
EXPIRY_MODES = ("never", "days", "date")

MAX_GRACE_DAYS = 3650
MAX_EXPIRE_DAYS = 36500
MAX_FINGERPRINTS = 100
MAX_PATTERNS = 100
MAX_PATTERN_LENGTH = 200
MAX_CUSTOMER_LENGTH = 200
MAX_OUTPUT_NAME_LENGTH = 120

# Keep this in sync with ``pyshield.builder``.  It mirrors the copy filter so a
# preflight scan sees the same files the build would actually publish.
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "staticfiles", "_shield_runtime",
}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".log", ".sqlite3"}
IGNORED_FILES = {
    "db.sqlite3", "pyshield.lic", ".pyshield-build.json",
    ".pyshield-manifest.json",
}
SECRET_SUFFIX = ".shield-secret.json"

SENSITIVE_NAME_PATTERNS = (
    ".env", ".env.*", ".netrc", ".git-credentials", ".npmrc", ".pypirc",
    ".htpasswd", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
    "id_ed25519*", "credentials*", "secrets.json", "*.kdbx",
    "*.shield-secret.json", "tool-license.json", "trial.json", "quota.json",
    "revocations.json",
)

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_BAD_NAME_RE = re.compile(r'[<>:"/\\|?*]')
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{32}$")


class SpecError(ValueError):
    """A structured preflight failure."""

    def __init__(self, field: str, code: str, message: str):
        super().__init__(message)
        self.field = field
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SpecIssue:
    field: str
    code: str
    message: str


@dataclass
class ProjectScan:
    root: Path
    files: list[Path] = field(default_factory=list)
    total_bytes: int = 0
    max_depth: int = 0
    sensitive_files: list[str] = field(default_factory=list)


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


def normalize_fingerprints(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = split_fingerprints(values)
    result: list[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text and text not in result:
            result.append(text)
    return result


def normpath(path: str | Path) -> str:
    """Return a normalized absolute path for containment checks."""
    raw = os.fspath(path)
    try:
        real = os.path.realpath(raw)
    except OSError:
        real = os.path.abspath(raw)
    return os.path.normcase(os.path.normpath(real))


def is_within(child: str | Path, parent: str | Path) -> bool:
    """Case/separator-safe containment check using real paths."""
    child_norm = normpath(child)
    parent_norm = normpath(parent)
    if child_norm == parent_norm:
        return True
    try:
        return os.path.commonpath([child_norm, parent_norm]) == parent_norm
    except (ValueError, OSError):
        return False


def is_reparse_point(path: str | Path) -> bool:
    """Detect symlinks, Windows junctions and other reparse points."""
    try:
        st = os.lstat(os.fspath(path))
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    if os.name == "nt":
        attrs = getattr(st, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if reparse and attrs & reparse:
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if isjunction is not None:
            try:
                return bool(isjunction(path))
            except (OSError, ValueError):
                return False
    return False


def path_has_reparse(path: str | Path, stop: str | Path | None = None) -> bool:
    """Walk existing components up to ``stop`` and report any reparse point."""
    target = Path(path)
    stop_path = Path(stop) if stop is not None else None
    current = target
    while True:
        if current.exists() and is_reparse_point(current):
            return True
        if stop_path is not None and normpath(current) == normpath(stop_path):
            return False
        parent = current.parent
        if parent == current:
            return False
        if stop_path is not None and not is_within(parent, stop_path):
            return False
        current = parent


def resolve_existing_directory(value: str | Path, field_name: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SpecError(field_name, "PATH_MISSING", f"{value} 不存在或无法解析") from exc
    if not path.is_dir():
        raise SpecError(field_name, "PATH_NOT_DIR", f"{path} 不是目录")
    if path_has_reparse(path):
        raise SpecError(field_name, "PATH_REPARSE", f"{path} 或其父目录是链接/重解析点")
    return path


def validate_project_relative(value: str) -> SpecIssue | None:
    text = str(value or "").strip()
    if not text:
        return SpecIssue("project", "PROJECT_REQUIRED", "project 不能为空")
    if _CONTROL_RE.search(text):
        return SpecIssue("project", "PROJECT_INVALID", "project 含控制字符")
    if os.path.isabs(text) or text.startswith(("/", "\\")):
        return SpecIssue("project", "PROJECT_ABSOLUTE", "project 必须是 project_root 内的相对路径")
    if re.match(r"^[A-Za-z]:", text):
        return SpecIssue("project", "PROJECT_ABSOLUTE", "project 不能包含盘符")
    parts = Path(text).parts
    if any(part in ("..", "") for part in parts):
        return SpecIssue("project", "PROJECT_TRAVERSAL", "project 不能包含 '..'")
    return None


def validate_output_name(value: str) -> SpecIssue | None:
    text = str(value or "")
    if not text.strip():
        return SpecIssue("output_name", "OUTPUT_REQUIRED", "output_name 不能为空")
    if text != text.strip():
        return SpecIssue("output_name", "OUTPUT_INVALID", "output_name 不能有首尾空格")
    if len(text) > MAX_OUTPUT_NAME_LENGTH:
        return SpecIssue("output_name", "OUTPUT_TOO_LONG", "output_name 过长")
    if text in (".", ".."):
        return SpecIssue("output_name", "OUTPUT_INVALID", "output_name 不能是 . 或 ..")
    if _CONTROL_RE.search(text) or _WINDOWS_BAD_NAME_RE.search(text):
        return SpecIssue("output_name", "OUTPUT_INVALID", "output_name 含非法字符")
    if text.endswith((" ", ".")):
        return SpecIssue("output_name", "OUTPUT_INVALID", "output_name 不能以空格或点结尾")
    stem = text.split(".", 1)[0].lower()
    if stem in _WINDOWS_RESERVED:
        return SpecIssue("output_name", "OUTPUT_RESERVED", "output_name 是 Windows 保留名")
    return None


def validate_cipher(value: str) -> SpecIssue | None:
    if value not in CIPHERS:
        return SpecIssue("cipher", "CIPHER_INVALID", f"未知加密算法：{value}")
    return None


def validate_integrity(value: str) -> SpecIssue | None:
    if value not in INTEGRITY_MODES:
        return SpecIssue("integrity", "INTEGRITY_INVALID", f"未知完整性模式：{value}")
    return None


def validate_runtime(value: str, allow_source: bool = False) -> SpecIssue | None:
    allowed = RUNTIME_MODES if allow_source else tuple(m for m in RUNTIME_MODES if m != "source")
    if value not in allowed:
        if value == "source":
            return SpecIssue("runtime", "RUNTIME_SOURCE_DISABLED", "MCP 默认禁止 source runtime")
        return SpecIssue("runtime", "RUNTIME_INVALID", f"未知 runtime 模式：{value}")
    return None


def validate_grace_days(value: int) -> SpecIssue | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return SpecIssue("grace_days", "GRACE_INVALID", "宽限天数必须是整数")
    if not 0 <= number <= MAX_GRACE_DAYS:
        return SpecIssue(
            "grace_days", "GRACE_RANGE",
            f"宽限天数必须在 0 到 {MAX_GRACE_DAYS} 之间",
        )
    return None


def validate_expiry_mode(mode: str, days: str | int | None, day_text: str | None,
                         today: date | None = None) -> SpecIssue | None:
    today = today or date.today()
    if mode not in EXPIRY_MODES:
        return SpecIssue("expire", "EXPIRE_MODE_INVALID", f"未知到期设置：{mode}")
    if mode == "never":
        return None
    if mode == "days":
        try:
            number = int(str(days).strip())
        except (TypeError, ValueError):
            return SpecIssue("expire", "EXPIRE_INVALID", "有效天数必须是整数")
        if not 1 <= number <= MAX_EXPIRE_DAYS:
            return SpecIssue(
                "expire", "EXPIRE_RANGE",
                f"有效天数必须在 1 到 {MAX_EXPIRE_DAYS} 之间",
            )
        return None
    text = str(day_text or "").strip()
    try:
        value = date.fromisoformat(text)
    except ValueError:
        return SpecIssue("expire", "EXPIRE_INVALID", "到期日期格式应为 YYYY-MM-DD")
    if value < today:
        return SpecIssue("expire", "EXPIRE_IN_PAST", "到期日期不能早于今天")
    return None


def validate_bind_mode(mode: str, fingerprints: Iterable[str]) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    values = normalize_fingerprints(fingerprints)
    if mode not in BINDING_MODES:
        return [SpecIssue("bind_mode", "BIND_INVALID", f"未知设备绑定方式：{mode}")]
    if mode == "fixed":
        if not values:
            issues.append(SpecIssue(
                "fingerprints", "FINGERPRINT_REQUIRED",
                "选择“指定指纹”时至少填写一个机器指纹",
            ))
    elif values:
        issues.append(SpecIssue(
            "fingerprints", "FINGERPRINT_UNEXPECTED",
            "any / first-run 模式下不能携带机器指纹",
        ))
    if len(values) > MAX_FINGERPRINTS:
        issues.append(SpecIssue(
            "fingerprints", "FINGERPRINT_TOO_MANY",
            f"机器指纹最多 {MAX_FINGERPRINTS} 个",
        ))
    for value in values:
        if not _FINGERPRINT_RE.fullmatch(value):
            issues.append(SpecIssue(
                "fingerprints", "FINGERPRINT_INVALID",
                f"机器指纹格式不正确（应为 32 位十六进制）：{value}",
            ))
    return issues


def validate_customer(value: str | None) -> SpecIssue | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > MAX_CUSTOMER_LENGTH:
        return SpecIssue("customer", "CUSTOMER_TOO_LONG", "客户名称过长")
    if _CONTROL_RE.search(text):
        return SpecIssue("customer", "CUSTOMER_INVALID", "客户名称含控制字符")
    return None


def validate_patterns(values: Iterable[str], field_name: str) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    patterns = [str(item).strip() for item in values]
    patterns = [item for item in patterns if item]
    if len(patterns) > MAX_PATTERNS:
        issues.append(SpecIssue(
            field_name, "PATTERN_TOO_MANY", f"最多 {MAX_PATTERNS} 条模式",
        ))
    for pattern in patterns:
        if len(pattern) > MAX_PATTERN_LENGTH:
            issues.append(SpecIssue(
                field_name, "PATTERN_TOO_LONG", f"模式过长：{pattern[:40]}…",
            ))
            continue
        if _CONTROL_RE.search(pattern):
            issues.append(SpecIssue(
                field_name, "PATTERN_INVALID", "模式含控制字符",
            ))
            continue
        normalized = pattern.replace("\\", "/")
        lowered = normalized.lstrip("./").lower()
        parts = Path(normalized).parts
        if os.path.isabs(pattern) or re.match(r"^[A-Za-z]:", pattern):
            issues.append(SpecIssue(
                field_name, "PATTERN_ABSOLUTE", f"模式必须是相对路径：{pattern}",
            ))
        if ".." in parts:
            issues.append(SpecIssue(
                field_name, "PATTERN_TRAVERSAL", f"模式不能包含 '..'：{pattern}",
            ))
        if (
            parts
            and parts[-1].lower() in {
                "manage.py", "_shield_runtime", "pyshield.lic",
                ".pyshield-build.json", ".pyshield-manifest.json",
            }
        ) or lowered in {
            "manage.py", "_shield_runtime", "pyshield.lic",
            ".pyshield-build.json", ".pyshield-manifest.json",
        }:
            issues.append(SpecIssue(
                field_name, "PATTERN_PROTECTED",
                f"不能排除受保护元数据：{pattern}",
            ))
        if lowered.endswith(SECRET_SUFFIX):
            issues.append(SpecIssue(
                field_name, "PATTERN_SECRET", "不能把构建密钥写入排除模式",
            ))
    return issues


def is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in SENSITIVE_NAME_PATTERNS)


def _entry_is_reparse(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True
    if os.name == "nt":
        is_junction = getattr(entry, "is_junction", None)
        if is_junction is not None:
            try:
                if is_junction():
                    return True
            except OSError:
                return True
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            return True
        attrs = getattr(st, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if reparse and attrs & reparse:
            return True
    return False


def _walk_entries(root: Path) -> Iterator[tuple[Path, int]]:
    """Yield (relative file path, depth) without following links."""
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if _entry_is_reparse(entry):
                    raise SpecError(
                        "project", "PATH_REPARSE",
                        f"项目中存在符号链接/重解析点：{entry.path}",
                    )
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError as exc:
                    raise SpecError(
                        "project", "PATH_UNREADABLE",
                        f"无法读取项目条目：{entry.path}",
                    ) from exc
                if is_dir:
                    if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                        continue
                    stack.append((Path(entry.path), depth + 1))
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError as exc:
                    raise SpecError(
                        "project", "PATH_UNREADABLE",
                        f"无法读取项目条目：{entry.path}",
                    ) from exc
                path = Path(entry.path)
                if path.suffix in IGNORED_SUFFIXES:
                    continue
                if entry.name in IGNORED_FILES or entry.name.endswith(SECRET_SUFFIX):
                    continue
                yield path.relative_to(root), depth


def assert_no_reparse_tree(root: str | Path) -> None:
    """Reject any symlink/junction anywhere under ``root`` (including ignored dirs)."""
    base = Path(root)
    stack = [base]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if _entry_is_reparse(entry):
                    raise SpecError(
                        "project", "PATH_REPARSE",
                        f"目录树中存在符号链接/重解析点：{entry.path}",
                    )
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                except OSError as exc:
                    raise SpecError(
                        "project", "PATH_UNREADABLE",
                        f"无法读取目录条目：{entry.path}",
                    ) from exc


def scan_project(root: str | Path, *, max_files: int = 20_000,
                 max_bytes: int = 1024 ** 3, max_depth: int = 32,
                 allow_sensitive: bool = False) -> ProjectScan:
    """Walk a project safely, enforcing size/depth limits and flagging secrets."""
    base = Path(root)
    scan = ProjectScan(root=base)
    for relative, depth in _walk_entries(base):
        scan.files.append(relative)
        scan.max_depth = max(scan.max_depth, depth)
        try:
            size = (base / relative).stat(follow_symlinks=False).st_size
        except OSError as exc:
            raise SpecError(
                "project", "PATH_UNREADABLE",
                f"无法读取项目文件：{relative}",
            ) from exc
        scan.total_bytes += size
        if len(scan.files) > max_files:
            raise SpecError(
                "project", "LIMIT_FILES",
                f"项目文件数超过上限（{max_files}）",
            )
        if scan.total_bytes > max_bytes:
            raise SpecError(
                "project", "LIMIT_BYTES",
                f"项目总大小超过上限（{max_bytes} 字节）",
            )
        if depth > max_depth:
            raise SpecError(
                "project", "LIMIT_DEPTH",
                f"项目目录深度超过上限（{max_depth}）",
            )
        if is_sensitive_name(relative.name):
            scan.sensitive_files.append(relative.as_posix())
    if scan.sensitive_files and not allow_sensitive:
        preview = ", ".join(scan.sensitive_files[:5])
        more = "" if len(scan.sensitive_files) <= 5 else "…"
        raise SpecError(
            "project", "SENSITIVE_FILE",
            f"项目包含敏感文件（{preview}{more}），请移除或显式允许",
        )
    return scan


def check_python_syntax(root: str | Path, relative_files: Iterable[Path]) -> list[SpecIssue]:
    """Compile project Python sources without executing them."""
    base = Path(root)
    issues: list[SpecIssue] = []
    for relative in relative_files:
        if relative.suffix != ".py":
            continue
        path = base / relative
        try:
            source = path.read_bytes()
        except OSError as exc:
            issues.append(SpecIssue(
                "project", "PATH_UNREADABLE",
                f"无法读取 {relative}: {exc}",
            ))
            continue
        try:
            compile(source, relative.as_posix(), "exec", dont_inherit=True)
        except SyntaxError as exc:
            location = f"{relative}:{exc.lineno or '?'}:{exc.offset or '?'}"
            issues.append(SpecIssue(
                "project", "PROJECT_SYNTAX",
                f"项目代码语法错误 {location}: {exc.msg}",
            ))
        except (ValueError, UnicodeError) as exc:
            issues.append(SpecIssue(
                "project", "PROJECT_SYNTAX",
                f"项目代码无法编译 {relative}: {exc}",
            ))
    return issues


def free_bytes(path: str | Path) -> int | None:
    try:
        usage = shutil.disk_usage(os.fspath(path))
    except OSError:
        return None
    return int(usage.free)
