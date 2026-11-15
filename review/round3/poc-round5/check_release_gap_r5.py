"""Round 5 regression: check_release.py key coverage and pycache rejection.

Scenarios:
1. ``--temp`` with a real test key in a ``pyshield*`` entry and no key source
   -> must fail (cannot verify), instead of the round-4 silent pass.
2. same, with ``--secret`` -> must detect the planted key.
3. zip without a sibling secret and no ``--secret`` -> must fail.
4. same zip with ``--secret`` -> must detect the planted key.
5. a copy of the challenge zip with a planted ``__pycache__`` entry -> must fail.

The test master/build key is derived from the authorized sample tree and is
never printed.

Usage:
    py -3.12 check_release_gap_r5.py <selfbuild-protected-sample directory>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(os.environ.get("PYSHELD_REPO", Path(__file__).resolve().parents[3]))
SAMPLE = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else REPO / "review" / "round3" / "extracted" / "selfbuild-protected-sample"
)
CHECKER = REPO / "tools" / "check_release.py"
CHALLENGE_ZIP = REPO / "review" / "round3" / "round3-django-shielded.zip"

sys.path.insert(0, str(SAMPLE))
from _shield_runtime import _crypto, _keyring  # noqa: E402

build_id = json.loads((SAMPLE / ".pyshield-build.json").read_text(encoding="utf-8"))["build_id"]
master = _keyring.master_key()
build_key = _crypto.hkdf_sha256(
    master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
)


def run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def main() -> int:
    failures = []
    staging = REPO / "build"
    staging.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="pyshield-r5-check-", dir=staging))
    temp_dir = Path(tempfile.mkdtemp(prefix="pyshield-check-gap-r5-"))
    zip_path = root / "pyshield-check-gap-r5.zip"
    pycache_zip = root / "pyshield-pycache-r5.zip"
    secret_path = root / "test-build.shield-secret.json"
    try:
        secret_path.write_text(json.dumps({
            "format": 3,
            "tool": "pyshield 0.6.0",
            "build_id": build_id,
            "master_key": _crypto.b85encode(master),
            "build_key": _crypto.b85encode(build_key),
            "signing_key": _crypto.b85encode(os.urandom(32)),
            "license_format": 3,
        }), encoding="utf-8")
        (temp_dir / "master.txt").write_text(master.hex() + "\n", encoding="utf-8")

        cases = [
            ("TEMP without key source", ["--temp"], 1),
            ("TEMP with --secret", ["--temp", "--secret", str(secret_path)], 1),
        ]
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("master.txt", master.hex() + "\n")
        cases.append(("zip without sibling secret", [str(zip_path)], 1))
        cases.append(("zip with --secret", ["--secret", str(secret_path), str(zip_path)], 1))

        for label, argv, expected in cases:
            result = run(*argv)
            detected = "build key material" in (result.stdout + result.stderr)
            print(f"--- {label}: rc={result.returncode} detected={detected}")
            print((result.stdout + result.stderr).strip()[:700])
            if result.returncode != expected:
                failures.append(f"{label}: expected rc {expected}, got {result.returncode}")
            if "without" not in label and not detected:
                failures.append(f"{label}: key material was not reported")
            if label.startswith("TEMP without") and "cannot verify" not in (
                result.stdout + result.stderr
            ):
                failures.append(f"{label}: expected an explicit 'cannot verify' failure")

        if CHALLENGE_ZIP.is_file():
            with zipfile.ZipFile(CHALLENGE_ZIP) as source_zip, zipfile.ZipFile(
                pycache_zip, "w", compression=zipfile.ZIP_DEFLATED,
            ) as target_zip:
                for info in source_zip.infolist():
                    target_zip.writestr(info, source_zip.read(info))
                target_zip.writestr(
                    "round3-django-shielded/demo/__pycache__/__init__.cpython-312.pyc",
                    b"forged",
                )
            clean = run("--skip-key-scan", str(CHALLENGE_ZIP))
            poisoned = run("--skip-key-scan", str(pycache_zip))
            print(f"--- challenge zip: rc={clean.returncode}")
            print((clean.stdout + clean.stderr).strip()[:500])
            print(f"--- challenge zip + pycache: rc={poisoned.returncode}")
            print((poisoned.stdout + poisoned.stderr).strip()[:500])
            if clean.returncode != 0:
                failures.append("challenge zip should pass the bytecode scan")
            if poisoned.returncode == 0:
                failures.append("zip with a __pycache__ entry was accepted")

        if failures:
            print("FAILURES:")
            for item in failures:
                print("  -", item)
            return 1
        print("OK: key coverage fails closed, --secret detects planted keys, pycache zip rejected")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
