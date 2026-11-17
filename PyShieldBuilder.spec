# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

try:
    import Cython  # noqa: F401
except ImportError:
    raise SystemExit(
        "PyShieldBuilder.spec requires Cython>=3 so the packaged desktop "
        "builder can compile native runtimes: pip install 'Cython>=3'"
    )

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
elif sys.platform == "darwin":
    # pywebview on macOS talks to Cocoa/WebKit through pyobjc.
    hiddenimports = [
        "webview.platforms.cocoa",
    ]
    for module in ("objc", "AppKit", "Foundation", "WebKit", "Quartz", "CoreFoundation"):
        try:
            hiddenimports += collect_submodules(module)
        except Exception:  # pragma: no cover - pyobjc not installed
            pass
else:
    hiddenimports = [
        "webview.platforms.gtk",
    ]

if (ROOT / "pyshield" / "_tool_key.py").is_file():
    hiddenimports.append("pyshield._tool_key")

if (ROOT / "pyshield" / "_site.py").is_file():
    hiddenimports.append("pyshield._site")

# The desktop builder can compile a native runtime on the customer's build
# machine. Cython and setuptools therefore have to travel with the exe; they
# are still optional at build time for bytecode-only builds.
hiddenimports += [
    "Cython",
    "setuptools",
    "setuptools.command.build_ext",
    "distutils",
    "distutils.command.build_ext",
]
def _cython_module(name: str) -> bool:
    segments = name.split(".")
    return not any(part in {"Tests", "Debugger", "Coverage"} for part in segments)


hiddenimports += collect_submodules("Cython", filter=_cython_module)
datas += collect_data_files("Cython", excludes=["**/Tests/**", "**/Debugger/**"])
datas += collect_data_files("setuptools")

hiddenimports += [
    # Imported lazily, so PyInstaller cannot see them from the module graph.
    "cryptography",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "cryptography.hazmat.primitives.ciphers.aead",
    "pyshield.mcp_server",
    "pyshield.mcp_server.protocol",
    "pyshield.mcp_server.http_transport",
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
    "gui.mcp_host",
    "gui.instance",
]

excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "cefpython3",
    "pytest",
    "django",
]

if IS_WINDOWS:
    excludes.append("gi")

exe_options = {}
if IS_WINDOWS:
    exe_options["icon"] = str(ROOT / "gui" / "assets" / "icon.ico")
    exe_options["version"] = str(ROOT / "gui" / "assets" / "version_info.txt")
    # One binary, two lives: double-click shows only the window, while an MCP
    # client that spawns `PyShieldBuilder.exe --mcp` still gets real stdio
    # pipes. A windowed (console=False) build would set sys.stdout to None.
    exe_options["hide_console"] = "hide-early"

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
    # UPX is not a security boundary and is commonly flagged by antivirus.
    # Native runtime extensions are stripped by pyshield.builder instead.
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Console subsystem keeps stdin/stdout usable for `--mcp`; hide_console
    # keeps the window invisible for normal GUI launches.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **exe_options,
)
