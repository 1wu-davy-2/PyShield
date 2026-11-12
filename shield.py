#!/usr/bin/env python3
"""PyShield command line entry point.

Usage:
    python shield.py build -p <project> -o <output>
    python shield.py fingerprint
    python shield.py issue --secret <output>.shield-secret.json -e 30
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyshield.cli import main

if __name__ == "__main__":
    sys.exit(main())
