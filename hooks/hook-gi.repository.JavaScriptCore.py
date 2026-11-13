"""Collect JavaScriptCore typelibs required by WebKit2.

The bundled hooks do not cover JavaScriptCore, which WebKit2 lists as a GI
dependency. Version numbers track the WebKitGTK ABI (4.1 / 4.0).
"""

from PyInstaller.utils.hooks.gi import GiModuleInfo


def _collect(name, versions):
    for version in versions:
        module = GiModuleInfo(name, version)
        if module.available:
            return module.collect_typelib_data()
    return [], [], []


binaries, datas, hiddenimports = _collect("JavaScriptCore", ("4.1", "4.0"))
