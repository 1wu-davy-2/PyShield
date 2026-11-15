"""PyShield license portal.

A small, dependency-free issuing and validation service for PyShield tool
licenses.  It is deployed separately from the desktop builder and talks to the
same signed entitlement format, so the application only needs the public key.

The package deliberately imports the shared crypto and entitlement helpers from
the sibling ``pyshield`` package.  When deploying only this directory, keep the
repository root (or the ``pyshield`` package) next to it on ``PYTHONPATH``.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

__version__ = "0.1.0"
