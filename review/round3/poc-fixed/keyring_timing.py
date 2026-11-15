"""Release tree -> master key -> every business module, with timings."""
import hashlib
import json
import marshal
import re
import statistics
import sys
import time
import zlib
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TREE = Path(sys.argv[1]).resolve()
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
TIMING = []


def one_round():
    t0 = time.perf_counter()
    build = json.loads((TREE / ".pyshield-build.json").read_text(encoding="utf-8"))
    build_id = build["build_id"]
    keyring_raw = (TREE / "_shield_runtime" / "_keyring.pyc").read_bytes()
    code = marshal.loads(keyring_raw[16:])
    namespace = {}
    exec(code, namespace)
    master = namespace["master_key"]()
    t_key = time.perf_counter()

    sys.path.insert(0, str(TREE))
    from _shield_runtime import _crypto  # noqa: E402

    build_key = _crypto.hkdf_sha256(
        master, salt=b"pyshield-build-key-v1", info=build_id.encode("utf-8"), length=32
    )
    t_derive = time.perf_counter()

    timings = []
    recovered = {}
    flag = None
    for path in sorted(TREE.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("# PyShield protected module"):
            continue
        module_id = re.findall(r"^\s{4}'([^']+)',\s*$", text, re.M)[-1]
        chunks = "".join(re.findall(r'^\s{8}"([^"]+)",?$', text, re.M))
        start = time.perf_counter()
        raw = _crypto.open_envelope(
            _crypto.derive_module_key(build_key, module_id),
            _crypto.b85decode(chunks),
            _crypto.module_aad(build_id, module_id),
        )
        module_code = marshal.loads(zlib.decompress(raw))
        timings.append(time.perf_counter() - start)
        recovered[module_id] = module_code
        for const in module_code.co_consts:
            if isinstance(const, str) and const.startswith("PSH-"):
                flag = const
    t_all = time.perf_counter()
    return {
        "build_id": build_id,
        "master_sha256": hashlib.sha256(master).hexdigest(),
        "master_len": len(master),
        "build_key_sha256": hashlib.sha256(build_key).hexdigest(),
        "modules": len(recovered),
        "flag": flag,
        "flag_sha256": hashlib.sha256(flag.encode()).hexdigest() if flag else None,
        "key_ms": round(1000 * (t_key - t0), 3),
        "derive_ms": round(1000 * (t_derive - t_key), 3),
        "decrypt_all_ms": round(1000 * (t_all - t_derive), 3),
        "decrypt_median_ms": round(1000 * statistics.median(timings), 3),
        "total_ms": round(1000 * (t_all - t0), 3),
    }


for _ in range(ROUNDS):
    TIMING.append(one_round())

summary = {}
for key in ("key_ms", "derive_ms", "decrypt_all_ms", "decrypt_median_ms", "total_ms"):
    summary[key] = round(statistics.median(item[key] for item in TIMING), 3)
print(json.dumps({"runs": TIMING, "median": summary}, ensure_ascii=False, indent=2))
