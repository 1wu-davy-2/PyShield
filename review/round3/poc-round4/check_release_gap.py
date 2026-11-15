"""Demonstrate check_release.py master/build-key false negatives.

The checker only derives key needles from a sibling *.shield-secret.json and
passes an empty needle tuple to files scanned through --temp. This test writes
the authorized sample's test master/build key into a pyshield*-named TEMP dir
and into a zip without a sibling secret, then shows both scans return 0.

The key files are created under the system temp directory and removed in a
finally block. No key material is printed.
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
SAMPLE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO / "review" / "round3" / "extracted" / "selfbuild-protected-sample"
CHECKER = REPO / "tools" / "check_release.py"
sys.path.insert(0, str(SAMPLE))
from _shield_runtime import _crypto, _keyring  # noqa: E402

build_id = json.loads((SAMPLE / ".pyshield-build.json").read_text(encoding="utf-8"))["build_id"]
master = _keyring.master_key()
build_key = _crypto.hkdf_sha256(
    master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
)
temp_dir = Path(tempfile.mkdtemp(prefix="pyshield-check-gap-"))
zip_path = Path(tempfile.gettempdir()) / "pyshield-check-gap.zip"
try:
    (temp_dir / "master.txt").write_text(
        master.hex() + "\n" + build_key.hex() + "\n", encoding="utf-8"
    )
    temp_result = subprocess.run(
        [sys.executable, str(CHECKER), "--temp"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("master.txt", master.hex() + "\n")
    zip_result = subprocess.run(
        [sys.executable, str(CHECKER), str(zip_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print("TEMP result:", temp_result.returncode)
    print((temp_result.stdout + temp_result.stderr).strip())
    print("ZIP result:", zip_result.returncode)
    print((zip_result.stdout + zip_result.stderr).strip())
    if temp_result.returncode == 0 and zip_result.returncode == 0:
        print("GAP CONFIRMED: a real test master/build key in a pyshield* TEMP entry and a zip without a sibling secret is not detected")
        raise SystemExit(0)
    print("GAP NOT CONFIRMED: checker rejected one of the key files")
    raise SystemExit(1)
finally:
    shutil.rmtree(temp_dir, ignore_errors=True)
    try:
        zip_path.unlink()
    except OSError:
        pass
