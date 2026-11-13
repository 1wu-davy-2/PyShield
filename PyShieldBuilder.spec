# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "gui" / "web"), "gui/web"),
    (str(ROOT / "gui" / "assets" / "icon.png"), "gui/assets"),
    (str(ROOT / "pyshield" / "runtime_template"), "pyshield/runtime_template"),
    (str(ROOT / "pyshield" / "crypto.py"), "pyshield"),
    (str(ROOT / "pyshield" / "fingerprint.py"), "pyshield"),
    (str(ROOT / "pyshield" / "ed25519.py"), "pyshield"),
]

hiddenimports = [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
]

excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "gi",
    "cefpython3",
    "Cython",
    "setuptools",
    "pytest",
    "django",
]

a = Analysis(
    [str(ROOT / "gui" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
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
    icon=str(ROOT / "gui" / "assets" / "icon.ico"),
    version=str(ROOT / "gui" / "assets" / "version_info.txt"),
)
