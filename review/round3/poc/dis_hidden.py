import dis
import json
import marshal
import re
import sys
import zlib
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(r"E:\tmp\workbuild\code\分析\round3\dst\round3-django-shielded")
sys.path.insert(0, str(ROOT))
from _shield_runtime import _crypto, _keyring  # noqa: E402

build_id = json.loads((ROOT / ".pyshield-build.json").read_text(encoding="utf-8"))["build_id"]
master = _keyring.master_key()
build_key = _crypto.hkdf_sha256(
    master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
)

for path in sorted(ROOT.rglob("*.py")):
    if path.name == "manage.py" or "demo" in path.parts or "sampleproj" in path.parts:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "PyShield protected module" not in text:
            continue
        module_id = re.findall(r"^\s{4}'([^']+)',\s*$", text, re.M)[-1]
        chunks = "".join(re.findall(r'^\s{8}"([^"]+)",?$', text, re.M))
        raw = _crypto.open_envelope(
            _crypto.derive_module_key(build_key, module_id),
            _crypto.b85decode(chunks),
            _crypto.module_aad(build_id, module_id),
        )
        code = marshal.loads(zlib.decompress(raw))
        print(f"===== {module_id} =====")
        print("names:", code.co_names)
        print("consts:", [repr(c)[:100] for c in code.co_consts])
        if module_id == "demo.hidden_flag":
            dis.dis(code)
