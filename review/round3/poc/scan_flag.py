import hashlib
from pathlib import Path

FLAG = b"PSH-ROUND3-c32ae3df08a0237b95d472b8413c54f8"
TARGET = b"46b5119e53dcb631ca73426deb23910af129b589c8a9f618da1663aa9448e3bc"
paths = [
    Path(r"E:\tmp\workbuild\code\分析\round3\dst\round3-django-shielded"),
    Path(r"E:\tmp\workbuild\code\PyShield\review\round3\round3-django-shielded.zip"),
]
for root in paths:
    hits = []
    files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    for path in files:
        data = path.read_bytes()
        if FLAG in data:
            hits.append((str(path), "flag"))
        if TARGET in data:
            hits.append((str(path), "hash"))
    print(root.name, "files:", len(files), "hits:", hits)

source = Path(r"C:\Users\ww\AppData\Local\Temp\pyshield-round3-project-ybevj8q8\review-demo\demo\hidden_flag.py")
text = source.read_text(encoding="utf-8")
value = [line for line in text.splitlines() if line.startswith("FLAG")][0].split('"')[1]
print("workspace source flag:", value)
print("sha256:", hashlib.sha256(value.encode()).hexdigest())
