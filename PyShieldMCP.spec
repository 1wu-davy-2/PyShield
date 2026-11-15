# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)
IS_WINDOWS = sys.platform == "win32"

datas = []
hiddenimports = [
    "pyshield.mcp_server",
    "pyshield.mcp_server.protocol",
    "pyshield.mcp_server.schemas",
    "pyshield.mcp_server.tools",
    "pyshield.mcp_server.resources",
    "pyshield.build_service",
    "pyshield.build_service.models",
    "pyshield.build_service.validation",
    "pyshield.build_service.engine",
    "pyshield.build_service.jobs",
    "pyshield.build_service.artifacts",
    "pyshield.build_service.verification",
    "pyshield.build_spec",
]

if (ROOT / "pyshield" / "_tool_key.py").is_file():
    hiddenimports.append("pyshield._tool_key")
if (ROOT / "pyshield" / "_site.py").is_file():
    hiddenimports.append("pyshield._site")

# Native runtime builds spawn this same frozen executable with a hidden
# --pyshield-setup entry point, so Cython/setuptools must travel with it.
try:
    import Cython  # noqa: F401
except ImportError:
    pass
else:
    def _cython_module(name: str) -> bool:
        segments = name.split(".")
        return not any(part in {"Tests", "Debugger", "Coverage"} for part in segments)

    hiddenimports += collect_submodules("Cython", filter=_cython_module)
    datas += collect_data_files("Cython", excludes=["**/Tests/**", "**/Debugger/**"])
    hiddenimports += [
        "setuptools",
        "setuptools.command.build_ext",
        "distutils",
        "distutils.command.build_ext",
    ]
    datas += collect_data_files("setuptools")

excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "cefpython3",
    "pytest",
    "django",
    "webview",
    "gui",
]

if IS_WINDOWS:
    excludes.append("gi")

exe_options = {}
if IS_WINDOWS:
    exe_options["icon"] = str(ROOT / "gui" / "assets" / "icon.ico")
    exe_options["version"] = str(ROOT / "gui" / "assets" / "version_info_mcp.txt")

a = Analysis(
    [str(ROOT / "shield_mcp.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PyShieldMCP",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **exe_options,
)
