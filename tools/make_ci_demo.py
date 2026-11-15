#!/usr/bin/env python3
"""Create a tiny project for native-runtime CI checks.

Kept self-contained so CI does not depend on importing the ``tests`` namespace.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0] if args else "ci-native-project")
    package = root / "mypkg"
    package.mkdir(parents=True, exist_ok=True)
    (root / "manage.py").write_text(
        '"""CI-only project; the builder runs this to prove the runtime imports."""\n'
        "import sys\n"
        "from mypkg.tools import double\n\n"
        "def main():\n"
        "    assert double(21) == 42\n"
        "    print('native ci project ok')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    (package / "tools.py").write_text(
        "def double(value):\n    return value * 2\n", encoding="utf-8",
    )
    (root / "standalone.py").write_text(
        "def ping():\n    return 'pong'\n", encoding="utf-8",
    )
    (package / "data.txt").write_text("native ci data\n", encoding="utf-8")
    print(f"created {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
