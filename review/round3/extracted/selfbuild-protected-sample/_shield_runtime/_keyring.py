"""Generated per-build runtime key material.

Do not commit or reuse this file. The master key is split and XOR-masked so it
is not recoverable with a plain ``strings`` scan. This raises extraction cost;
it is not a confidentiality boundary.
"""

from __future__ import annotations

_MASK = bytes.fromhex("e09222b243d86e05e5c725319d37871bc1edef48ae478c35c70e9a537b402943")
_PARTS = (
    bytes.fromhex("3f8cf375"),
    bytes.fromhex("a417cfe5"),
    bytes.fromhex("52011aa8"),
    bytes.fromhex("f2e0079e"),
    bytes.fromhex("32778855"),
    bytes.fromhex("1e3bd872"),
    bytes.fromhex("96e8ba7d"),
    bytes.fromhex("7de0c701"),
)


def master_key() -> bytes:
    material = b"".join(_PARTS)
    return bytes(
        value ^ _MASK[index % len(_MASK)]
        for index, value in enumerate(material)
    )
