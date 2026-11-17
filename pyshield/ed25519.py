"""Ed25519 signatures (RFC 8032).

The pure Python implementation is always available, so license signing works
without third-party packages. When ``cryptography`` is installed its Ed25519
backend is preferred for speed; both produce the same 64-byte signatures.
"""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(value: int) -> int:
    return pow(value, _P - 2, _P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return 0 if sign == 0 else None
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _point_add(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    a = (left[1] - left[0]) * (right[1] - right[0]) % _P
    b = (left[1] + left[0]) * (right[1] + right[0]) % _P
    c = 2 * left[3] * right[3] * _D % _P
    d = 2 * left[2] * right[2] % _P
    e = b - a
    f = d - c
    g = d + c
    h = b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _point_mul(scalar: int, point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    result = (0, 1, 1, 0)
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def _point_equal(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return (
        (left[0] * right[2] - right[0] * left[2]) % _P == 0
        and (left[1] * right[2] - right[1] * left[2]) % _P == 0
    )


def _encode_point(point: tuple[int, int, int, int]) -> bytes:
    z_inv = _inv(point[2])
    x = point[0] * z_inv % _P
    y = point[1] * z_inv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode_point(data: bytes) -> tuple[int, int, int, int]:
    if len(data) != 32:
        raise ValueError("invalid Ed25519 point")
    value = int.from_bytes(data, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        raise ValueError("invalid Ed25519 point")
    return (x, y, 1, x * y % _P)


_BASE_Y = 4 * _inv(5) % _P
_BASE_X = _recover_x(_BASE_Y, 0)
_BASE = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % _P)


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    digest = _sha512(seed)
    scalar = bytearray(digest[:32])
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    return int.from_bytes(scalar, "little"), digest[32:]


def _pure_public_key(seed: bytes) -> bytes:
    scalar, _ = _expand(seed)
    return _encode_point(_point_mul(scalar, _BASE))


def _pure_sign(seed: bytes, message: bytes) -> bytes:
    scalar, prefix = _expand(seed)
    public = _encode_point(_point_mul(scalar, _BASE))
    nonce = int.from_bytes(_sha512(prefix + message), "little") % _L
    encoded_r = _encode_point(_point_mul(nonce, _BASE))
    challenge = int.from_bytes(_sha512(encoded_r + public + message), "little") % _L
    s = (nonce + challenge * scalar) % _L
    return encoded_r + s.to_bytes(32, "little")


def _pure_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    try:
        a = _decode_point(public)
        r = _decode_point(signature[:32])
    except ValueError:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    challenge = int.from_bytes(_sha512(signature[:32] + public + message), "little") % _L
    return _point_equal(_point_mul(s, _BASE), _point_add(r, _point_mul(challenge, a)))


def _native_classes():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        # A frozen build can ship the package without its serialization
        # submodule; validate the whole fast path before trusting it.
        from cryptography.hazmat.primitives.serialization import (  # noqa: F401
            Encoding,
            PublicFormat,
        )
    except (ImportError, AttributeError):
        return None
    return Ed25519PrivateKey, Ed25519PublicKey


def public_key(seed: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    classes = _native_classes()
    if classes is None:
        return _pure_public_key(seed)
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return classes[0].from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def sign(seed: bytes, message: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    classes = _native_classes()
    if classes is None:
        return _pure_sign(seed, message)
    return classes[0].from_private_bytes(seed).sign(message)


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    classes = _native_classes()
    if classes is None:
        return _pure_verify(public, message, signature)
    try:
        classes[1].from_public_bytes(public).verify(signature, message)
    except Exception:
        return False
    return True
