"""Collect Soup typelibs for pywebview's GTK backend.

Override PyInstaller's Soup hook, which only checks libsoup 3.0, so builds on
Ubuntu 22.04 (libsoup 2.4 / WebKit2GTK 4.0) still work.
"""

from PyInstaller.utils.hooks.gi import GiModuleInfo


def _collect(name, versions):
    for version in versions:
        module = GiModuleInfo(name, version)
        if module.available:
            return module.collect_typelib_data()
    return [], [], []


binaries, datas, hiddenimports = _collect("Soup", ("3.0", "2.4"))
