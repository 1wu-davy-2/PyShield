#!/usr/bin/env python3
"""Run the unittest suite and surface failures as workflow annotations.

CI cannot be read anonymously, so failures are re-emitted as ``::error::``
annotations that ``GET /check-runs/{id}/annotations`` exposes without auth.

Usage:
    python tools/run_tests.py [unittest args...]
"""
from __future__ import annotations

import subprocess
import sys

MAX_ANNOTATION = 3800
MAX_ANNOTATIONS = 6


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else ["discover", "-s", "tests", "-v"])
    result = subprocess.run(
        [sys.executable, "-m", "unittest", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    output = (result.stdout or "") + (result.stderr or "")
    sys.stdout.write(output[-16000:])
    sys.stdout.flush()
    if result.returncode == 0:
        return 0
    lines = [line for line in output.splitlines() if line.strip()]
    interesting = [
        line for line in lines
        if line.startswith(("FAIL:", "ERROR:", "SKIP:")) or "AssertionError" in line
        or line.startswith(("Ran ", "OK", "FAILED"))
    ]
    tail = "\n".join(interesting[:20]) or "\n".join(lines[-30:])
    print(f"::error::unittest exited with {result.returncode}")
    chunks = [tail[i:i + MAX_ANNOTATION] for i in range(0, len(tail), MAX_ANNOTATION)]
    for chunk in chunks[:MAX_ANNOTATIONS]:
        print("::error::" + chunk.replace("\r", "").replace("\n", "%0A"))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
