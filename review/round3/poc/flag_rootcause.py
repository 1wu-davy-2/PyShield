import dis
import json
import marshal
import re
import sys
import zlib
from pathlib import Path

sys.dont_write_bytecode = True

TREE = Path(r"E:\tmp\workbuild\code\分析\round3\dst\round3-django-shielded")
SOURCE = Path(r"C:\Users\ww\AppData\Local\Temp\pyshield-round3-project-ybevj8q8\review-demo\demo\hidden_flag.py")
sys.path.insert(0, str(TREE))
from _shield_runtime import _crypto, _keyring  # noqa: E402

build_id = json.loads((TREE / ".pyshield-build.json").read_text(encoding="utf-8"))["build_id"]
master = _keyring.master_key()
build_key = _crypto.hkdf_sha256(master, salt=b"pyshield-build-key-v1", info=build_id.encode(), length=32)
stub = (TREE / "demo" / "hidden_flag.py").read_text(encoding="utf-8")
payload = "".join(re.findall(r'^\s{8}"([^"]+)",?$', stub, re.M))
payload_code = marshal.loads(zlib.decompress(_crypto.open_envelope(
    _crypto.derive_module_key(build_key, "demo.hidden_flag"),
    _crypto.b85decode(payload),
    _crypto.module_aad(build_id, "demo.hidden_flag"),
)))

source_text = SOURCE.read_text(encoding="utf-8")
source_code = compile(source_text, "demo/hidden_flag.py", "exec")
print("workspace source:")
print(source_text)
print("compiled-from-source names:", source_code.co_names)
print("compiled-from-source consts:", source_code.co_consts)
print("payload code object names:", payload_code.co_names)
print("payload code object consts:", payload_code.co_consts)
print("co_code equal:", source_code.co_code == payload_code.co_code)
print("co_linetable equal:", source_code.co_linetable == payload_code.co_linetable)
print("FLAG in names:", "FLAG" in payload_code.co_names)
print("flag string in consts:", any(isinstance(c, str) and "PSH-ROUND3" in c for c in payload_code.co_consts))
print("disassembly of payload:")
dis.dis(payload_code)
