# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

ROOT = Path(SPECPATH)
IS_WINDOWS = sys.platform == "win32"

datas = [
    (str(ROOT / "gui" / "web"), "gui/web"),
    (str(ROOT / "gui" / "assets" / "icon.png"), "gui/assets"),
    # Runtime sources are embedded in pyshield/_runtime_bundle.py and compiled
    # into the PYZ. Do not add runtime_template or crypto/fingerprint/ed25519
    # as datas: that would put the decryption runtime back into the exe as
    # plaintext files.
]

if IS_WINDOWS:
    datas.append((str(ROOT / "gui" / "assets" / "icon.ico"), "gui/assets"))
    hiddenimports = [
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ]
else:
    hiddenimports = [
        "webview.platforms.gtk",
    ]

if (ROOT / "pyshield" / "_tool_key.py").is_file():
    hiddenimports.append("pyshield._tool_key")

if (ROOT / "pyshield" / "_site.py").is_file():
    hiddenimports.append("pyshield._site")

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
    exe_options["icon"] = str(ROOT / "gui" / "assets" / "icon.ico")
    exe_options["version"] = str(ROOT / "gui" / "assets" / "version_info.txt")

a = Analysis(
    [str(ROOT / "gui" / "main.py")],
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
    name="PyShieldBuilder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **exe_options,
)
