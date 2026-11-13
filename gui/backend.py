"""pywebview backend for the PyShield desktop builder.

The build engine runs in-process so the packaged exe is self-contained. Only
``manage.py check`` uses an external interpreter (the project's own virtual
environment), because deployment dependencies belong to the target project.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from pyshield import __version__, builder, fingerprint

from . import core

STAGE_LABELS = {
    "init": "校验配置",
    "copy": "复制项目文件",
    "encrypt": "加密 Python 模块",
    "runtime": "安装受保护运行时",
    "license": "签发许可证",
    "manifest": "写入签名清单",
    "publish": "发布输出目录",
    "secret": "写入构建密钥",
    "verify": "校验受保护项目",
    "cleanup": "清理缓存",
    "done": "构建完成",
}

MAX_LOGS = 2000


class BuildManager:
    """Run one build at a time and expose a poll-friendly snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel: threading.Event | None = None
        self._probe_cache: dict[tuple[str, float], core.PythonInfo | None] = {}
        self._state: dict = self._idle_state()

    @staticmethod
    def _idle_state() -> dict:
        return {
            "status": "idle",
            "stage": "",
            "stage_label": "",
            "percent": 0,
            "logs": [],
            "error": None,
            "started": None,
            "finished": None,
            "summary": None,
            "output": None,
            "secret": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            data = dict(self._state)
            data["logs"] = list(self._state["logs"])
            data["summary"] = dict(self._state["summary"]) if self._state["summary"] else None
            return data

    def running(self) -> bool:
        with self._lock:
            return self._state["status"] == "running"

    def start(self, request: core.BuildRequest, settings: dict) -> dict:
        with self._lock:
            if self._state["status"] == "running":
                return {"ok": False, "error": "已有构建任务正在运行。"}

        verify_python, verify_note = self._pick_verify_python(request, settings)
        if not request.output.strip() and request.project.strip():
            request.output = str(core.default_output(request.project, settings["output_suffix"]))
        errors = core.validate_request(request)
        if request.verify_python.strip() and not request.no_verify and verify_python is None:
            errors.append(
                "校验用 Python 不可用或与内置 CPython "
                f"{core.EMBEDDED_PYTHON_MINOR} 不匹配、未安装 Django。"
            )
        if errors:
            return {"ok": False, "errors": errors}

        options = builder.BuildOptions(
            project=str(Path(request.project).expanduser().resolve()),
            output=str(Path(request.output).expanduser().resolve()),
            excludes=list(request.excludes),
            mutable_patterns=list(request.mutable_patterns),
            encrypt_migrations=request.encrypt_migrations,
            encrypt_tests=request.encrypt_tests,
            expire=request.expire_value(),
            grace_days=int(str(request.grace_days).strip() or 0),
            bind_device=list(request.fingerprints) if request.bind_mode == "fixed" else [],
            bind_first_run=request.bind_mode == "first-run",
            cipher=request.cipher,
            customer=request.customer or None,
            integrity=request.integrity,
            runtime_pyc=request.runtime == "pyc",
            no_verify=request.no_verify or verify_python is None,
            force=request.force,
            verify_python=verify_python,
            cancel=threading.Event(),
            progress=self._on_progress,
            log_sink=self._on_log,
        )

        with self._lock:
            self._cancel = options.cancel
            self._state = self._idle_state()
            self._state.update({
                "status": "running",
                "started": datetime.now().isoformat(timespec="seconds"),
            })
        if verify_note:
            self._on_log(verify_note)
        self._on_log(
            f"内置 CPython {core.EMBEDDED_PYTHON}；受保护包需部署在同一 Python "
            f"{core.EMBEDDED_PYTHON_MINOR}.x 小版本上"
        )
        self._thread = threading.Thread(
            target=self._run, args=(options, request, verify_python), daemon=True,
        )
        self._thread.start()
        return {"ok": True, "verify_python": verify_python, "note": verify_note}

    def cancel(self) -> dict:
        with self._lock:
            if self._state["status"] != "running" or self._cancel is None:
                return {"ok": False, "error": "当前没有正在运行的构建。"}
            self._cancel.set()
            self._state["stage_label"] = "正在取消构建…"
        self._on_log("收到取消请求，正在安全停止…")
        return {"ok": True}

    def _run(self, options: builder.BuildOptions, request: core.BuildRequest, verify_python: str | None) -> None:
        try:
            result = builder.build(options)
        except builder.BuildCancelled:
            self._finish("cancelled", None, "构建已取消。", request)
        except Exception as exc:
            self._finish("failed", None, str(exc), request)
        else:
            summary = self._summary(result, request)
            self._finish("success", summary, None, request)
        finally:
            with self._lock:
                self._cancel = None

    def _finish(self, status: str, summary: dict | None, error: str | None, request: core.BuildRequest) -> None:
        finished = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._state["status"] = status
            self._state["finished"] = finished
            self._state["error"] = error
            self._state["summary"] = summary
            if summary:
                self._state["output"] = summary.get("output")
                self._state["secret"] = summary.get("secret")
            if status == "success":
                self._state["percent"] = 100
                self._state["stage"] = "done"
                self._state["stage_label"] = "构建完成"

        entry = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "created": finished,
            "status": status,
            "project": str(Path(request.project).expanduser()),
            "project_name": Path(request.project).expanduser().name,
            "output": (summary or {}).get("output") or str(Path(request.output).expanduser()),
            "secret": (summary or {}).get("secret"),
            "license": (summary or {}).get("license"),
            "cipher": (summary or {}).get("cipher") or request.cipher,
            "integrity": request.integrity,
            "runtime": request.runtime,
            "modules": (summary or {}).get("modules"),
            "size": (summary or {}).get("size"),
            "size_text": (summary or {}).get("size_text"),
            "elapsed": (summary or {}).get("elapsed"),
            "expires": (summary or {}).get("expires"),
            "binding": request.bind_mode,
            "customer": request.customer or None,
            "error": error,
        }
        history = core.load_history()
        history.insert(0, entry)
        try:
            core.save_history(history)
        except OSError:
            pass

    def _summary(self, result: builder.BuildResult, request: core.BuildRequest) -> dict:
        expires = request.expire_value() or "永久"
        return {
            "project": str(Path(request.project).expanduser().resolve()),
            "output": str(result.output),
            "secret": str(result.secret),
            "license": str(result.license),
            "build_id": result.build_id,
            "modules": result.modules,
            "cipher": request.cipher,
            "integrity": request.integrity,
            "runtime": request.runtime,
            "elapsed": round(result.elapsed, 1),
            "size": result.output_size,
            "size_text": core.format_bytes(result.output_size),
            "expires": expires,
            "binding": request.bind_mode,
            "customer": request.customer or None,
        }

    def _pick_verify_python(self, request: core.BuildRequest, settings: dict) -> tuple[str | None, str]:
        if request.no_verify:
            return None, "已选择跳过 manage.py check"
        if request.verify_python.strip():
            info = self._probe(request.verify_python)
            if info is None or not info.compatible_with_embedded() or not info.django:
                return None, "校验用 Python 不可用或环境不匹配，构建将跳过 manage.py check"
            return info.executable, f"将使用 {info.executable} 执行 manage.py check"
        if not settings.get("auto_detect_python", True):
            return None, "自动检测已关闭，构建将跳过 manage.py check"
        for candidate in core.find_python_candidates(request.project):
            info = self._probe(candidate)
            if info is not None and info.django and info.compatible_with_embedded():
                return info.executable, f"自动检测到校验解释器：{info.executable}"
        return None, "未找到安装了 Django 且版本匹配的项目解释器，构建将跳过 manage.py check"

    def _probe(self, executable: str) -> core.PythonInfo | None:
        try:
            key = (os.path.normcase(executable), os.stat(executable).st_mtime)
        except OSError:
            return None
        if key in self._probe_cache:
            return self._probe_cache[key]
        try:
            info = core.probe_python(executable)
        except RuntimeError:
            info = None
        self._probe_cache[key] = info
        return info

    def _on_progress(self, stage: str, percent: int) -> None:
        with self._lock:
            self._state["stage"] = stage
            self._state["stage_label"] = STAGE_LABELS.get(stage, stage)
            self._state["percent"] = max(0, min(100, int(percent)))

    def _on_log(self, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        with self._lock:
            logs = self._state["logs"]
            logs.append(line)
            if len(logs) > MAX_LOGS:
                del logs[: len(logs) - MAX_LOGS]


class Api:
    """Methods exposed to the webview through ``window.pywebview.api``."""

    def __init__(self) -> None:
        self.manager = BuildManager()
        self._window = None

    def bind(self, window) -> None:
        self._window = window

    def bootstrap(self) -> dict:
        settings = core.load_settings()
        history = core.load_history()
        self._mark_missing(history)
        try:
            machine = fingerprint.machine_fingerprint()
        except Exception:
            machine = ""
        return {
            "ok": True,
            "app": {
                "title": core.APP_TITLE,
                "version": __version__,
                "python": core.EMBEDDED_PYTHON,
                "python_minor": core.EMBEDDED_PYTHON_MINOR,
                "frozen": bool(getattr(sys, "frozen", False)),
                "config_dir": str(core.config_dir()),
                "platform": sys.platform,
            },
            "defaults": {
                "output_suffix": settings["output_suffix"],
                "cipher": settings["cipher"],
                "integrity": settings["integrity"],
                "runtime": settings["runtime"],
                "auto_detect_python": settings["auto_detect_python"],
                "no_verify": settings["no_verify"],
            },
            "settings": settings,
            "history": history,
            "machine": machine,
            "state": self.manager.snapshot(),
        }

    def inspect(self, payload: dict | None = None) -> dict:
        request = core.BuildRequest.from_payload(payload)
        data = payload if isinstance(payload, dict) else {}
        settings = core.load_settings()
        if not request.output.strip() and request.project.strip():
            request.output = str(core.default_output(request.project, settings["output_suffix"]))
        errors = core.validate_request(request)
        project = Path(request.project).expanduser() if request.project else None

        project_info: dict = {"valid": False, "modules": 0, "packages": 0, "name": ""}
        if project is not None and (project / "manage.py").is_file():
            try:
                patterns = builder.default_excludes(
                    request.encrypt_migrations, request.encrypt_tests,
                ) + list(request.excludes)
                modules = builder.discover_modules(project, patterns)
                packages = builder.find_packages(project)
            except (builder.BuildError, OSError):
                modules = []
                packages = []
            project_info = {
                "valid": True,
                "name": project.name,
                "modules": len(modules),
                "packages": [entry.name for entry in packages],
                "python": self._detect_project_python(project),
            }

        candidates = core.find_python_candidates(request.project)
        verify = None
        verify_error = None
        selected = request.verify_python.strip()
        if selected:
            try:
                info = core.probe_python(selected)
            except RuntimeError as exc:
                verify_error = str(exc)
            else:
                verify = info.to_dict()
                if not info.compatible_with_embedded():
                    verify_error = (
                        f"该解释器为 Python {info.version}，内置运行时为 "
                        f"{core.EMBEDDED_PYTHON}，受保护模块无法在其他小版本加载。"
                    )
                elif not info.django:
                    verify_error = "该解释器未安装 Django，无法执行 manage.py check。"

        suggested = None
        if settings.get("auto_detect_python", True):
            for candidate in candidates:
                if verify is not None and candidate == verify.get("executable"):
                    continue
                info = self.manager._probe(candidate)
                if info is not None and info.django and info.compatible_with_embedded():
                    suggested = info.to_dict()
                    break

        output = request.output
        return {
            "ok": True,
            "errors": errors,
            "warnings": [],
            "project": project_info,
            "output": output,
            "secret": str(core.secret_path_for(output)) if output else "",
            "python_candidates": candidates,
            "verify": verify,
            "verify_error": verify_error,
            "suggested_verify": suggested,
            "machine": self._machine(),
            "settings": settings,
            "extras": {
                "mutable_patterns": data.get("mutable_patterns") or "",
                "excludes": data.get("excludes") or "",
            },
        }

    def browse_project(self) -> dict:
        selected = self._dialog(self._folder_dialog())
        if not selected:
            return {"ok": False, "cancelled": True}
        project = Path(selected)
        settings = core.load_settings()
        output = core.default_output(str(project), settings["output_suffix"]) if project.name else ""
        return {
            "ok": True,
            "project": str(project),
            "output": str(output) if output else "",
            "name": project.name,
        }

    def browse_output(self, project: str = "", current: str = "") -> dict:
        start = ""
        if current:
            start = str(Path(current).expanduser().parent)
        elif project:
            start = str(Path(project).expanduser().parent)
        selected = self._dialog(self._folder_dialog(start))
        if not selected:
            return {"ok": False, "cancelled": True}
        target = Path(selected)
        settings = core.load_settings()
        suffix = settings["output_suffix"]
        name = Path(project).name if project else "project"
        if target.name == f"{name}{suffix}":
            return {"ok": True, "output": str(target)}
        if target.name.startswith(name + suffix):
            return {"ok": True, "output": str(target)}
        return {"ok": True, "output": str(target / f"{name}{suffix}")}

    def browse_python(self) -> dict:
        try:
            import webview
        except ImportError:
            return {"ok": False, "error": "当前环境缺少 pywebview。"}
        window = self._window or (webview.windows[0] if webview.windows else None)
        if window is None:
            return {"ok": False, "error": "窗口尚未就绪。"}
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Python 解释器 (*.exe;python*;python3*)", "所有文件 (*.*)"),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            info = core.probe_python(path)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "python": info.to_dict()}

    def fingerprint(self) -> dict:
        return {"ok": True, "machine": self._machine()}

    def start_build(self, payload: dict | None = None) -> dict:
        request = core.BuildRequest.from_payload(payload)
        settings = core.load_settings()
        return self.manager.start(request, settings)

    def snapshot(self) -> dict:
        return {"ok": True, "state": self.manager.snapshot()}

    def get_history(self) -> dict:
        history = core.load_history()
        self._mark_missing(history)
        return {"ok": True, "history": history}

    def cancel_build(self) -> dict:
        return self.manager.cancel()

    def save_settings(self, payload: dict | None = None) -> dict:
        try:
            settings = core.save_settings(payload or {})
        except OSError as exc:
            return {"ok": False, "error": f"无法保存设置：{exc}"}
        return {"ok": True, "settings": settings}

    def reveal(self, path: str = "") -> dict:
        target = Path(str(path or "")).expanduser()
        if not str(path).strip() or not target.exists():
            return {"ok": False, "error": "路径不存在。"}
        self._reveal(target)
        return {"ok": True}

    def clear_history(self) -> dict:
        try:
            core.save_history([])
        except OSError as exc:
            return {"ok": False, "error": f"无法清空记录：{exc}"}
        return {"ok": True, "history": []}

    def window_action(self, action: str = "", maximized: bool = False) -> dict:
        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口尚未就绪。"}
        if action == "minimize":
            window.minimize()
        elif action == "maximize":
            window.restore() if maximized else window.maximize()
        elif action == "close":
            if self.manager.running():
                return {"ok": False, "error": "构建进行中，请先取消或等待完成。"}
            window.destroy()
        else:
            return {"ok": False, "error": f"未知窗口操作：{action}"}
        return {"ok": True}

    def _dialog(self, dialog) -> str:
        if dialog is None:
            return ""
        result = dialog
        if not result:
            return ""
        path = result[0] if isinstance(result, (list, tuple)) else result
        return str(path)

    def _folder_dialog(self, start: str = ""):
        try:
            import webview
        except ImportError:
            return None
        window = self._window or (webview.windows[0] if webview.windows else None)
        if window is None:
            return None
        kwargs = {}
        if start and Path(start).is_dir():
            kwargs["directory"] = start
        return window.create_file_dialog(webview.FOLDER_DIALOG, **kwargs)

    def _machine(self) -> str:
        try:
            return fingerprint.machine_fingerprint()
        except Exception:
            return ""

    def _detect_project_python(self, project: Path) -> str | None:
        for relative in (
            ".venv/Scripts/python.exe",
            "venv/Scripts/python.exe",
            "env/Scripts/python.exe",
            ".venv/bin/python",
            "venv/bin/python",
            "env/bin/python",
        ):
            candidate = project / relative
            if candidate.is_file():
                return str(candidate)
        return None

    @staticmethod
    def _reveal(target: Path) -> None:
        if os.name == "nt":
            if target.is_dir():
                os.startfile(str(target))  # noqa: S606 - expected Windows behaviour
            else:
                subprocess.Popen(["explorer", "/select,", str(target)])
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(target if target.is_dir() else target.parent)])

    @staticmethod
    def _mark_missing(history: list[dict]) -> None:
        for entry in history:
            output = entry.get("output")
            if output and not Path(output).exists():
                entry["missing"] = True
