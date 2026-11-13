"""Collect WebKit2 typelibs for pywebview's GTK backend.

PyInstaller ships hooks for Gtk and Soup but not for WebKit2. Try the 4.1
typelib first (Ubuntu 24.04+) and fall back to 4.0 (Ubuntu 22.04).
"""

from PyInstaller.utils.hooks.gi import GiModuleInfo


def _collect(name, versions):
    for version in versions:
        module = GiModuleInfo(name, version)
        if module.available:
            return module.collect_typelib_data()
    return [], [], []


binaries, datas, hiddenimports = _collect("WebKit2", ("4.1", "4.0"))
