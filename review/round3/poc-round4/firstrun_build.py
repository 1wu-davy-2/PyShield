"""Build a small project whose license uses bind_mode=first-run."""
import os
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(os.environ.get("PYSHELD_REPO", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(REPO))
from tests.test_builder import make_project  # noqa: E402
from pyshield import builder, licensing  # noqa: E402

BASE = Path(__file__).with_name("run") / "firstrun"
shutil.rmtree(BASE, ignore_errors=True)
project = BASE / "project"
project.mkdir(parents=True)
make_project(project)
result = builder.build(
    builder.BuildOptions(
        project=str(project),
        output=str(BASE / "dist"),
        cipher="chacha",
        bind_first_run=True,
        no_verify=True,
    )
)
secret = licensing.read_secret(result.secret)
print({
    "output": str(result.output),
    "build_id": result.build_id,
    "license": str(result.license),
    "bind_mode": secret.get("license_format"),
    "secret": str(result.secret),
})
