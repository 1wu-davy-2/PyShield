# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the experimental self-protected builder.

This spec is normally launched by ``tools/selfbuild.py``. It expects:

  PYSHELD_SELFBUILD_ROOT  protected output produced by tools/selfbuild.py
  PYSHELD_SELFBUILD_REPO  PyShield repository root (for custom hooks)
  PYSHELD_SELFBUILD_NAME  output executable name

The protected tree is the only Python source path; the normal repository
source must not be on ``pathex`` or PyInstaller could package the originals.
"""

import json
import os
import sys
from pathlib import Path

PROTECTED_ENV = os.environ.get("PYSHELD_SELFBUILD_ROOT", "")
if not PROTECTED_ENV:
    raise SystemExit("PYSHELD_SELFBUILD_ROOT is not set; run tools/selfbuild.py")
PROTECTED = Path(PROTECTED_ENV).resolve()
if not (PROTECTED / "manage.py").is_file():
    raise SystemExit(f"protected tree has no manage.py: {PROTECTED}")

REPO = Path(os.environ.get("PYSHELD_SELFBUILD_REPO", ".")).resolve()
NAME = os.environ.get("PYSHELD_SELFBUILD_NAME", "PyShieldBuilder-selfbuild")
IS_WINDOWS = sys.platform == "win32"
CONSOLE = os.environ.get("PYSHELD_SELFBUILD_CONSOLE", "").strip().lower() in {
    "1", "true", "yes", "on",
}


def module_names(package: str) -> list[str]:
    base = PROTECTED / package
    names: list[str] = []
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(PROTECTED).with_suffix("")
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if not parts or parts[-1] == "__main__":
            continue
        names.append(".".join(parts))
    return names


# Stubs have no statically visible imports, so every protected module must be
# listed explicitly. PyInstaller then compiles the stubs into the PYZ; the
# payloads stay encrypted until the runtime executes them.
hiddenimports = set(module_names("pyshield") + module_names("gui"))
hiddenimports.add("_shield_runtime")
imports_file = os.environ.get("PYSHELD_SELFBUILD_IMPORTS", "")
if imports_file:
    # Imports inside encrypted stubs are invisible to PyInstaller. The
    # selfbuild driver scans the original source with ast and passes them
    # here; this catches stdlib binary modules such as sqlite3.
    hiddenimports.update(json.loads(Path(imports_file).read_text(encoding="utf-8")))
hiddenimports.update([
    "cryptography",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "cryptography.hazmat.primitives.serialization",
])
hiddenimports = sorted(hiddenimports)
if IS_WINDOWS:
    hiddenimports.extend([
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr",
        "clr_loader",
        "pythonnet",
    ])
else:
    hiddenimports.extend([
        "webview",
        "webview.platforms.gtk",
        "gi",
    ])

datas = [
    (str(PROTECTED / "gui" / "web"), "gui/web"),
    (str(PROTECTED / "gui" / "assets" / "icon.png"), "gui/assets"),
    # The self-license is deliberately inside the exe. It is authorization
    # data, not a module key, and the format 3 license carries no build_key.
    (str(PROTECTED / "pyshield.lic"), "."),
]

if IS_WINDOWS:
    icon = PROTECTED / "gui" / "assets" / "icon.ico"
    if icon.is_file():
        datas.append((str(icon), "gui/assets"))

excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "cefpython3",
    "Cython",
    "setuptools",
    "pytest",
    "django",
]
if IS_WINDOWS:
    excludes.append("gi")

exe_options = {}
if IS_WINDOWS:
    icon = PROTECTED / "gui" / "assets" / "icon.ico"
    version = PROTECTED / "gui" / "assets" / "version_info.txt"
    if icon.is_file():
        exe_options["icon"] = str(icon)
    if version.is_file():
        exe_options["version"] = str(version)

a = Analysis(
    [str(PROTECTED / "manage.py")],
    pathex=[str(PROTECTED)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(REPO / "hooks")],
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
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=CONSOLE,
    # The PoC is smoke-tested by checking that the process stays alive. Do not
    # turn startup failures into a modal dialog that would look like success.
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **exe_options,
)
