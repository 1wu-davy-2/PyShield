"""Fetch a self-contained CPython from a domestic mirror.

The builder only needs an external interpreter to run ``manage.py check`` on
the protected copy.  Customer machines frequently have no Python at all, so
this module downloads the official *embeddable* distribution from a mirror,
unpacks it under the user's application data directory, and optionally
bootstraps pip + Django into it so the interpreter is immediately usable.

Nothing outside ``runtime_root()`` is touched: no registry entries, no PATH
changes, no system-wide install.  Removing the directory uninstalls it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__all__ = [
    "InstallCancelled",
    "InstallError",
    "InstallJob",
    "MIRRORS",
    "available_versions",
    "detect_django_spec",
    "install",
    "installed_interpreters",
    "mirrors",
    "runtime_root",
]

USER_AGENT = "PyShieldRuntime/1.0 (+https://github.com/1wu-davy-2/PyShield)"

MIRRORS: tuple[dict, ...] = (
    {
        "id": "tuna",
        "label": "清华 TUNA",
        "note": "国内直连，速度最快",
        "base": "https://mirrors.tuna.tsinghua.edu.cn/python",
        "pypi": "https://pypi.tuna.tsinghua.edu.cn/simple",
    },
    {
        "id": "huawei",
        "label": "华为云",
        "note": "备用线路",
        "base": "https://mirrors.huaweicloud.com/python",
        "pypi": "https://repo.huaweicloud.com/repository/pypi/simple",
    },
    {
        "id": "official",
        "label": "官方源",
        "note": "python.org，海外网络",
        "base": "https://www.python.org/ftp/python",
        "pypi": "https://pypi.org/simple",
    },
)

# Python has no formal LTS channel.  These are the minor lines that still get
# bug/security fixes and that the embeddable runtime is shipped for.
MAINTAINED_MINORS: tuple[str, ...] = ("3.10", "3.11", "3.12", "3.13", "3.14")

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

_VERSION_LINK = re.compile(r'href="(3)\.(\d+)\.(\d+)/"')
_DJANGO_LINE = re.compile(
    r"^\s*django(?:\s*\[[^\]]*\])?\s*([<>=!~]=?\s*[0-9][^;#\s]*)?\s*(?:;|#|$)",
    re.IGNORECASE | re.MULTILINE,
)


class InstallError(RuntimeError):
    """Raised when the runtime cannot be prepared."""


class InstallCancelled(InstallError):
    """Raised when the user cancelled the download."""


def runtime_root() -> Path:
    """Directory that holds downloaded interpreters."""
    override = os.environ.get("PYSHELD_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    home = os.environ.get("PYSHELD_HOME")
    if home:
        return Path(home).expanduser() / "runtimes"
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "PyShield" / "runtimes"
    return Path.home() / ".pyshield" / "runtimes"


def mirrors() -> list[dict]:
    return [dict(item) for item in MIRRORS]


def mirror_by_id(mirror: str) -> dict:
    for item in MIRRORS:
        if item["id"] == mirror:
            return dict(item)
    raise InstallError(f"未知的下载源：{mirror}")


def app_minor() -> str:
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def embed_suffix() -> str:
    """Windows embeddable archive suffix for the current architecture."""
    machine = (os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
    if machine in {"x86", "x86_64", "amd64"} and sys.maxsize <= 2**32:
        return "win32"
    return "amd64"


def target_dir(version: str) -> Path:
    return runtime_root() / f"python-{version}"


def interpreter_path(version: str) -> Path:
    folder = target_dir(version)
    return folder / ("python.exe" if os.name == "nt" else "bin/python3")


def installed_interpreters() -> list[dict]:
    """Interpreters previously downloaded by this module."""
    if not runtime_root().is_dir():
        return []
    result: list[dict] = []
    for entry in sorted(runtime_root().glob("python-3.*")):
        if not entry.is_dir():
            continue
        exe = entry / ("python.exe" if os.name == "nt" else "bin/python3")
        if not exe.is_file():
            continue
        result.append(
            {
                "version": entry.name.removeprefix("python-"),
                "executable": str(exe),
                "path": str(entry),
            }
        )
    return result


# ---------------------------------------------------------------- version list


def parse_versions(html: str) -> dict[str, str]:
    """Map ``3.12`` -> newest patch found in a mirror directory listing."""
    best: dict[str, tuple[int, str]] = {}
    for major, minor, patch in _VERSION_LINK.findall(html or ""):
        key = f"{major}.{minor}"
        number = int(patch)
        if key not in best or number > best[key][0]:
            best[key] = (number, f"{key}.{number}")
    return {key: value[1] for key, value in best.items()}


def _describe(version: str, minor: str, base: str) -> dict:
    compatible = minor == app_minor()
    return {
        "version": version,
        "minor": minor,
        "compatible": compatible,
        "recommended": compatible,
        "url": f"{base.rstrip('/')}/{version}/python-{version}-embed-{embed_suffix()}.zip",
        "target": str(target_dir(version)),
        "installed": interpreter_path(version).is_file(),
        "label": f"Python {minor}",
        "tag": "推荐" if compatible else "版本不匹配",
    }


def _read(url: str, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _head(url: str, timeout: float = 10.0) -> int:
    """Return the payload size, or 0 when the file is missing.

    Some proxies reject HEAD outright, so fall back to a one-byte range GET
    before declaring the artifact missing.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.headers.get("Content-Length") or 1)
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 405, 501}:
            return 0
    except (urllib.error.URLError, OSError):
        return 0
    probe = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(probe, timeout=timeout) as response:
            total = response.headers.get("Content-Range") or ""
            return int(total.rsplit("/", 1)[-1]) if "/" in total else 1
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return 0


def _patches(html: str, minor: str) -> list[str]:
    """All patch releases of one minor line, newest first."""
    numbers = sorted(
        (int(patch) for major, line, patch in _VERSION_LINK.findall(html or "") if f"{major}.{line}" == minor),
        reverse=True,
    )
    return [f"{minor}.{number}" for number in numbers]


_VERSION_CACHE: dict[str, tuple[float, list[dict]]] = {}
_VERSION_TTL = 600.0


def available_versions(mirror: str = "tuna") -> list[dict]:
    """List maintained CPython releases that actually ship a Windows build.

    CPython publishes security fixes as source-only releases, so the newest
    patch of a line frequently has no embeddable archive.  Probe candidates
    from newest to oldest and keep the first downloadable one per line.
    """
    cached = _VERSION_CACHE.get(mirror)
    if cached and time.time() - cached[0] < _VERSION_TTL:
        return [dict(item) for item in cached[1]]

    source = mirror_by_id(mirror)
    try:
        html = _read(source["base"].rstrip("/") + "/").decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise InstallError(f"无法读取 {source['label']} 的版本列表：{exc}") from exc

    pending = {minor: _patches(html, minor) for minor in MAINTAINED_MINORS}
    chosen: dict[str, tuple[str, int]] = {}
    for _ in range(12):
        batch = [(minor, versions[0]) for minor, versions in pending.items() if versions][:8]
        if not batch:
            break

        def probe(item: tuple[str, str]) -> tuple[str, str, int]:
            minor, version = item
            url = f"{source['base'].rstrip('/')}/{version}/python-{version}-embed-{embed_suffix()}.zip"
            return minor, version, _head(url)

        with _ThreadPoolExecutor(max_workers=min(8, len(batch))) as pool:
            results = list(pool.map(probe, batch))
        for minor, version, size in results:
            if size:
                chosen[minor] = (version, size)
                pending.pop(minor, None)
            else:
                pending[minor].pop(0)

    ordered = sorted(chosen.items(), key=lambda item: tuple(int(p) for p in item[0].split(".")), reverse=True)
    versions = []
    for minor, (version, size) in ordered:
        item = _describe(version, minor, source["base"])
        item["size"] = size
        item["size_text"] = f"{size / 1048576:.1f} MB" if size else ""
        versions.append(item)
    _VERSION_CACHE[mirror] = (time.time(), versions)
    return [dict(item) for item in versions]


# ---------------------------------------------------------------- django spec


def detect_django_spec(project: str | Path | None) -> str:
    """Best-effort Django requirement from the project's manifest files."""
    if not project:
        return ""
    root = Path(project).expanduser()
    candidates = [root / "requirements.txt", root / "Pipfile", root / "pyproject.toml"]
    requirements_dir = root / "requirements"
    if requirements_dir.is_dir():
        candidates.extend(sorted(requirements_dir.glob("*.txt")))
    for path in candidates:
        try:
            if not path.is_file() or path.stat().st_size > 512 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _DJANGO_LINE.search(text)
        if match:
            spec = (match.group(1) or "").replace(" ", "")
            return f"Django{spec}" if spec else "Django"
    return "Django"


# ---------------------------------------------------------------- download


def _progress(callback: Callable[[str, int, str], None] | None, stage: str, percent: int, message: str) -> None:
    if callback is not None:
        callback(stage, max(0, min(100, percent)), message)


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    base = destination.resolve()
    for member in archive.infolist():
        name = member.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise InstallError(f"压缩包包含非法路径：{member.filename}")
        target = (base / name).resolve()
        if base != target and base not in target.parents:
            raise InstallError(f"压缩包包含非法路径：{member.filename}")
    archive.extractall(base)


def _download(url: str, destination: Path, cancel: threading.Event | None, report) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            with destination.open("wb") as handle:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise InstallCancelled("下载已取消")
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if total:
                        report(5 + int(50 * received / total), f"下载 {received / 1048576:.1f} / {total / 1048576:.1f} MB")
                    else:
                        report(5, f"下载 {received / 1048576:.1f} MB")
    except InstallCancelled:
        raise
    except (urllib.error.URLError, OSError) as exc:
        raise InstallError(f"下载失败：{exc}") from exc


def _enable_site_packages(folder: Path) -> None:
    """Uncomment ``import site`` and register ``Lib\\site-packages``."""
    pth_files = sorted(folder.glob("python*._pth"))
    if not pth_files:
        return
    path_file = pth_files[0]
    try:
        lines = path_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InstallError(f"无法读取 {path_file.name}：{exc}") from exc
    wanted = [".", "Lib\\site-packages", "Lib/site-packages"]
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and stripped.lstrip("#").strip() == "import site":
            continue
        if stripped in wanted[1:]:
            continue
        cleaned.append(line)
    if "import site" not in cleaned:
        cleaned.append("import site")
    cleaned.append("Lib\\site-packages")
    path_file.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
    (folder / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)


def _run(
    command: list[str],
    cancel: threading.Event | None,
    report,
    start: int,
    span: int,
    stage: str,
) -> str:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    output: list[str] = []
    while True:
        if cancel is not None and cancel.is_set():
            process.terminate()
            raise InstallCancelled("安装已取消")
        line = process.stdout.readline() if process.stdout else ""
        if line:
            output.append(line.rstrip())
            report(start, line.strip()[:160] or stage)
        if process.poll() is not None:
            break
        if not line:
            time.sleep(0.05)
    remainder = process.stdout.read() if process.stdout else ""
    if remainder:
        output.extend(remainder.splitlines())
    if process.returncode != 0:
        details = "\n".join(output[-14:]).strip() or f"退出码 {process.returncode}"
        raise InstallError(f"{stage}失败：{details}")
    report(start + span, stage)
    return "\n".join(output)


def install(
    version: str,
    mirror: str = "tuna",
    with_django: bool = True,
    django_spec: str = "",
    cancel: threading.Event | None = None,
    progress: Callable[[str, int, str], None] | None = None,
) -> Path:
    """Download and prepare one interpreter, returning its ``python.exe``."""
    source = mirror_by_id(mirror)
    version = version.strip()
    if not re.fullmatch(r"3\.\d+\.\d+", version):
        raise InstallError(f"版本号不合法：{version}")
    folder = target_dir(version)
    executable = folder / ("python.exe" if os.name == "nt" else "bin/python3")
    if executable.is_file():
        _progress(progress, "done", 100, "已安装，直接使用")
        return executable

    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    url = f"{source['base'].rstrip('/')}/{version}/python-{version}-embed-{embed_suffix()}.zip"

    _progress(progress, "download", 3, f"连接 {source['label']}")
    with tempfile.TemporaryDirectory(prefix="pyshield-runtime-") as temp:
        temp_dir = Path(temp)
        archive = temp_dir / "python.zip"
        _download(
            url,
            archive,
            cancel,
            lambda percent, message: _progress(progress, "download", percent, message),
        )
        if cancel is not None and cancel.is_set():
            raise InstallCancelled("安装已取消")
        _progress(progress, "extract", 58, "解压运行时")
        try:
            with zipfile.ZipFile(archive) as bundle:
                _safe_extract(bundle, temp_dir / "unpacked")
        except zipfile.BadZipFile as exc:
            raise InstallError("下载的文件不是有效的 ZIP，请更换下载源重试") from exc

        unpacked = temp_dir / "unpacked"
        if not unpacked.is_dir():
            raise InstallError("压缩包结构异常，未找到解压目录")
        staging = root / f".staging-{version}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.move(str(unpacked), str(staging))
        _enable_site_packages(staging)

        if cancel is not None and cancel.is_set():
            shutil.rmtree(staging, ignore_errors=True)
            raise InstallCancelled("安装已取消")
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        staging.rename(folder)

    _progress(progress, "unpack", 66, "运行时已就位")
    if not executable.is_file():
        raise InstallError(f"解压后未找到 {executable.name}，请更换下载源重试")

    if with_django:
        spec = (django_spec or "Django").strip()
        _progress(progress, "pip", 70, "安装 pip")
        with tempfile.TemporaryDirectory(prefix="pyshield-pip-") as temp:
            get_pip = Path(temp) / "get-pip.py"
            try:
                get_pip.write_bytes(_read(GET_PIP_URL, timeout=45))
            except (urllib.error.URLError, OSError) as exc:
                raise InstallError(f"无法下载 pip 引导脚本：{exc}") from exc
            _run(
                [
                    str(executable),
                    str(get_pip),
                    "--no-warn-script-location",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--index-url",
                    source["pypi"],
                ],
                cancel,
                lambda percent, message: _progress(progress, "pip", percent, message),
                70,
                12,
                "安装 pip",
            )
        _progress(progress, "django", 84, f"安装 {spec}")
        _run(
            [
                str(executable),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--no-warn-script-location",
                "--index-url",
                source["pypi"],
                spec,
            ],
            cancel,
            lambda percent, message: _progress(progress, "django", percent, message),
            84,
            14,
            f"安装 {spec}",
        )

    _progress(progress, "verify", 98, "校验运行时")
    try:
        completed = subprocess.run(
            [str(executable), "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"运行时无法启动：{exc}") from exc
    if completed.returncode != 0:
        raise InstallError(f"运行时无法启动：{(completed.stderr or '').strip()[:200]}")
    _progress(progress, "done", 100, f"Python {version} 可用")
    return executable


# ---------------------------------------------------------------- job runner


@dataclass
class InstallJob:
    """Background download with a snapshot API for the GUI."""

    version: str = ""
    mirror: str = "tuna"
    with_django: bool = True
    django_spec: str = ""
    status: str = "idle"  # idle | running | done | failed | cancelled
    percent: int = 0
    stage: str = ""
    message: str = ""
    error: str = ""
    executable: str = ""
    logs: list[str] = field(default_factory=list)
    started: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "percent": self.percent,
                "stage": self.stage,
                "message": self.message,
                "error": self.error,
                "executable": self.executable,
                "version": self.version,
                "mirror": self.mirror,
                "with_django": self.with_django,
                "elapsed": round(time.time() - self.started, 1) if self.started else 0.0,
                "logs": list(self.logs[-200:]),
            }

    @property
    def running(self) -> bool:
        return self.status == "running"

    def cancel(self) -> None:
        self._cancel.set()

    def start(
        self,
        version: str,
        mirror: str = "tuna",
        with_django: bool = True,
        django_spec: str = "",
    ) -> None:
        if self.running:
            raise InstallError("已有下载任务在进行中")
        self.version = version
        self.mirror = mirror
        self.with_django = with_django
        self.django_spec = django_spec
        self.status = "running"
        self.percent = 0
        self.stage = "start"
        self.message = "准备下载"
        self.error = ""
        self.executable = ""
        self.logs = []
        self.started = time.time()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pyshield-runtime", daemon=True)
        self._thread.start()

    def _report(self, stage: str, percent: int, message: str) -> None:
        with self._lock:
            self.stage = stage
            self.percent = percent
            self.message = message
            if not self.logs or self.logs[-1] != message:
                self.logs.append(message)

    def _run(self) -> None:
        try:
            executable = install(
                version=self.version,
                mirror=self.mirror,
                with_django=self.with_django,
                django_spec=self.django_spec,
                cancel=self._cancel,
                progress=self._report,
            )
        except InstallCancelled:
            with self._lock:
                self.status = "cancelled"
                self.message = "已取消"
        except InstallError as exc:
            with self._lock:
                self.status = "failed"
                self.error = str(exc)
                self.message = str(exc)
                self.logs.append(f"ERROR {exc}")
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures
            with self._lock:
                self.status = "failed"
                self.error = f"安装失败：{exc}"
                self.message = self.error
                self.logs.append(f"ERROR {exc}")
        else:
            with self._lock:
                self.status = "done"
                self.executable = str(executable)
                self.percent = 100
                self.message = f"Python {self.version} 已就绪"
                self.logs.append(self.message)
