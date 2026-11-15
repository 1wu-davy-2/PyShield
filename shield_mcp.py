#!/usr/bin/env python3
"""Console entry point for the PyShield MCP build server."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _run_embedded_setup(argv: list[str]) -> bool:
    """Run ``setup.py build_ext`` inside a frozen onefile process.

    ``pyshield.builder`` invokes ``sys.executable --pyshield-setup ...`` for
    native runtime builds.  The console MCP binary needs the same hidden entry
    point as the desktop builder.
    """
    if len(argv) < 3 or argv[1] != "--pyshield-setup":
        return False
    import runpy

    script = argv[2]
    log_path = Path(script).with_name("native-setup.log")
    try:
        log = log_path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = log
    sys.stderr = log
    code = 0
    try:
        sys.argv = [script, *argv[3:]]
        runpy.run_path(script, run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        import traceback

        traceback.print_exc()
        code = 1
    finally:
        try:
            log.flush()
        except OSError:
            pass
    os._exit(code)


sys.path.insert(0, str(Path(__file__).resolve().parent))

if _run_embedded_setup(sys.argv):
    os._exit(1)

from pyshield.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
