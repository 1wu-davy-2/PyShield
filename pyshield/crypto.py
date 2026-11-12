"""Authenticated encryption primitives used by PyShield.

Envelope format:
    MAGIC (4) | version (1) | cipher (1) | nonce_len (1) | tag_len (1)
    | nonce | ciphertext | tag

Cipher 1: AES-256-GCM (requires the optional ``cryptography`` package)
Cipher 2: ChaCha20 + HMAC-SHA256 (pure Python, always available)
Cipher 3: ChaCha20-Poly1305 (requires the optional ``cryptography`` package)

The header and caller supplied associated data are authenticated.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct

try:
    from . import _chacha_fast as _fast_chacha
except ImportError:
    _fast_chacha = None

MAGIC = b"PSX1"
VERSION = 1

CIPHER_AESGCM = 1
CIPHER_CHACHA = 2
CIPHER_CHACHA_POLY = 3

_TAG_LEN = {CIPHER_AESGCM: 16, CIPHER_CHACHA: 32, CIPHER_CHACHA_POLY: 16}
_NONCE_LEN = 12


class CryptoError(Exception):
    """Raised when an envelope cannot be authenticated or decrypted."""


def random_bytes(length: int) -> bytes:
    return secrets.token_bytes(length)


# ---------------------------------------------------------------------------
# HKDF / ChaCha20
# ---------------------------------------------------------------------------


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    if not salt:
        salt = b"\x00" * hashlib.sha256().digest_size
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    output = b""
    block = b""
    counter = 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:length]


def _chacha20_block_words(key_words: tuple[int, ...], counter: int, nonce_words: tuple[int, ...]) -> bytes:
    mask = 0xFFFFFFFF
    x0, x1, x2, x3 = 0x61707865, 0x3320646E, 0x79622D32, 0x6B206574
    x4, x5, x6, x7, x8, x9, x10, x11 = key_words
    x12 = counter
    x13, x14, x15 = nonce_words
    s0, s1, s2, s3, s4, s5, s6, s7 = x0, x1, x2, x3, x4, x5, x6, x7
    s8, s9, s10, s11, s12, s13, s14, s15 = x8, x9, x10, x11, x12, x13, x14, x15

    for _ in range(10):
        x0 = (x0 + x4) & mask; x12 ^= x0; x12 = ((x12 << 16) | (x12 >> 16)) & mask
        x8 = (x8 + x12) & mask; x4 ^= x8; x4 = ((x4 << 12) | (x4 >> 20)) & mask
        x0 = (x0 + x4) & mask; x12 ^= x0; x12 = ((x12 << 8) | (x12 >> 24)) & mask
        x8 = (x8 + x12) & mask; x4 ^= x8; x4 = ((x4 << 7) | (x4 >> 25)) & mask

        x1 = (x1 + x5) & mask; x13 ^= x1; x13 = ((x13 << 16) | (x13 >> 16)) & mask
        x9 = (x9 + x13) & mask; x5 ^= x9; x5 = ((x5 << 12) | (x5 >> 20)) & mask
        x1 = (x1 + x5) & mask; x13 ^= x1; x13 = ((x13 << 8) | (x13 >> 24)) & mask
        x9 = (x9 + x13) & mask; x5 ^= x9; x5 = ((x5 << 7) | (x5 >> 25)) & mask

        x2 = (x2 + x6) & mask; x14 ^= x2; x14 = ((x14 << 16) | (x14 >> 16)) & mask
        x10 = (x10 + x14) & mask; x6 ^= x10; x6 = ((x6 << 12) | (x6 >> 20)) & mask
        x2 = (x2 + x6) & mask; x14 ^= x2; x14 = ((x14 << 8) | (x14 >> 24)) & mask
        x10 = (x10 + x14) & mask; x6 ^= x10; x6 = ((x6 << 7) | (x6 >> 25)) & mask

        x3 = (x3 + x7) & mask; x15 ^= x3; x15 = ((x15 << 16) | (x15 >> 16)) & mask
        x11 = (x11 + x15) & mask; x7 ^= x11; x7 = ((x7 << 12) | (x7 >> 20)) & mask
        x3 = (x3 + x7) & mask; x15 ^= x3; x15 = ((x15 << 8) | (x15 >> 24)) & mask
        x11 = (x11 + x15) & mask; x7 ^= x11; x7 = ((x7 << 7) | (x7 >> 25)) & mask

        x0 = (x0 + x5) & mask; x15 ^= x0; x15 = ((x15 << 16) | (x15 >> 16)) & mask
        x10 = (x10 + x15) & mask; x5 ^= x10; x5 = ((x5 << 12) | (x5 >> 20)) & mask
        x0 = (x0 + x5) & mask; x15 ^= x0; x15 = ((x15 << 8) | (x15 >> 24)) & mask
        x10 = (x10 + x15) & mask; x5 ^= x10; x5 = ((x5 << 7) | (x5 >> 25)) & mask

        x1 = (x1 + x6) & mask; x12 ^= x1; x12 = ((x12 << 16) | (x12 >> 16)) & mask
        x11 = (x11 + x12) & mask; x6 ^= x11; x6 = ((x6 << 12) | (x6 >> 20)) & mask
        x1 = (x1 + x6) & mask; x12 ^= x1; x12 = ((x12 << 8) | (x12 >> 24)) & mask
        x11 = (x11 + x12) & mask; x6 ^= x11; x6 = ((x6 << 7) | (x6 >> 25)) & mask

        x2 = (x2 + x7) & mask; x13 ^= x2; x13 = ((x13 << 16) | (x13 >> 16)) & mask
        x8 = (x8 + x13) & mask; x7 ^= x8; x7 = ((x7 << 12) | (x7 >> 20)) & mask
        x2 = (x2 + x7) & mask; x13 ^= x2; x13 = ((x13 << 8) | (x13 >> 24)) & mask
        x8 = (x8 + x13) & mask; x7 ^= x8; x7 = ((x7 << 7) | (x7 >> 25)) & mask

        x3 = (x3 + x4) & mask; x14 ^= x3; x14 = ((x14 << 16) | (x14 >> 16)) & mask
        x9 = (x9 + x14) & mask; x4 ^= x9; x4 = ((x4 << 12) | (x4 >> 20)) & mask
        x3 = (x3 + x4) & mask; x14 ^= x3; x14 = ((x14 << 8) | (x14 >> 24)) & mask
        x9 = (x9 + x14) & mask; x4 ^= x9; x4 = ((x4 << 7) | (x4 >> 25)) & mask

    return struct.pack(
        "<16I",
        (x0 + s0) & mask, (x1 + s1) & mask, (x2 + s2) & mask, (x3 + s3) & mask,
        (x4 + s4) & mask, (x5 + s5) & mask, (x6 + s6) & mask, (x7 + s7) & mask,
        (x8 + s8) & mask, (x9 + s9) & mask, (x10 + s10) & mask, (x11 + s11) & mask,
        (x12 + s12) & mask, (x13 + s13) & mask, (x14 + s14) & mask, (x15 + s15) & mask,
    )


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    return _chacha20_block_words(struct.unpack("<8I", key), counter, struct.unpack("<3I", nonce))


def chacha20_xor(key: bytes, nonce: bytes, data: bytes, counter: int = 1) -> bytes:
    if _fast_chacha is not None:
        return _fast_chacha.chacha20_xor(key, nonce, data, counter)
    if len(key) != 32:
        raise ValueError("ChaCha20 key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("ChaCha20 nonce must be 12 bytes")
    if not data:
        return b""
    key_words = struct.unpack("<8I", key)
    nonce_words = struct.unpack("<3I", nonce)
    blocks = (len(data) + 63) // 64
    keystream = bytearray(blocks * 64)
    for index in range(blocks):
        keystream[index * 64:(index + 1) * 64] = _chacha20_block_words(
            key_words, (counter + index) & 0xFFFFFFFF, nonce_words
        )
    stream = bytes(keystream[:len(data)])
    return (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(len(data), "little")


# ---------------------------------------------------------------------------
# AES-256-GCM through the optional ``cryptography`` package
# ---------------------------------------------------------------------------


def _has_cryptography() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


def aesgcm_available() -> bool:
    return _has_cryptography()


def _aesgcm_encrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    sealed = AESGCM(key).encrypt(nonce, data, aad)
    return sealed[:-16], sealed[-16:]


def _aesgcm_decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes, tag: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        return AESGCM(key).decrypt(nonce, data + tag, aad)
    except Exception as exc:
        raise CryptoError("invalid or corrupted data") from exc


def _chacha_poly_encrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    sealed = ChaCha20Poly1305(key).encrypt(nonce, data, aad)
    return sealed[:-16], sealed[-16:]


def _chacha_poly_decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes, tag: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    try:
        return ChaCha20Poly1305(key).decrypt(nonce, data + tag, aad)
    except Exception as exc:
        raise CryptoError("invalid or corrupted data") from exc


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def _derive_pair(key: bytes) -> tuple[bytes, bytes]:
    material = hkdf_sha256(key, salt=b"pyshield-envelope-v1", info=b"encryption", length=64)
    return material[:32], material[32:]


def resolve_cipher(cipher: str = "auto") -> str:
    if cipher == "auto":
        return "aesgcm" if aesgcm_available() else "chacha"
    if cipher == "chacha":
        return "chacha20poly1305" if aesgcm_available() else "chacha"
    if cipher == "chacha-pure":
        return "chacha"
    if cipher == "aesgcm":
        if not aesgcm_available():
            raise ValueError("AES-GCM requires the 'cryptography' package; use --cipher chacha")
        return "aesgcm"
    if cipher == "chacha20poly1305":
        if not aesgcm_available():
            raise ValueError("ChaCha20-Poly1305 requires the 'cryptography' package; use --cipher chacha-pure")
        return "chacha20poly1305"
    raise ValueError("cipher must be auto, chacha, chacha-pure, or aesgcm")


def seal(key: bytes, plaintext: bytes, aad: bytes = b"", cipher: str = "auto") -> bytes:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    cipher = resolve_cipher(cipher)
    encryption_key, mac_key = _derive_pair(key)
    nonce = random_bytes(_NONCE_LEN)
    if cipher == "aesgcm":
        cipher_id = CIPHER_AESGCM
    elif cipher == "chacha20poly1305":
        cipher_id = CIPHER_CHACHA_POLY
    else:
        cipher_id = CIPHER_CHACHA
    tag_length = _TAG_LEN[cipher_id]
    header = MAGIC + bytes([VERSION, cipher_id, _NONCE_LEN, tag_length])
    authenticated = header + aad

    if cipher_id == CIPHER_AESGCM:
        ciphertext, tag = _aesgcm_encrypt(encryption_key, nonce, plaintext, authenticated)
    elif cipher_id == CIPHER_CHACHA_POLY:
        ciphertext, tag = _chacha_poly_encrypt(encryption_key, nonce, plaintext, authenticated)
    else:
        ciphertext = chacha20_xor(encryption_key, nonce, plaintext)
        tag = hmac.new(mac_key, authenticated + ciphertext, hashlib.sha256).digest()

    return header + nonce + ciphertext + tag


def open_envelope(key: bytes, envelope: bytes, aad: bytes = b"") -> bytes:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    if not isinstance(envelope, (bytes, bytearray)) or len(envelope) < 8:
        raise CryptoError("invalid or corrupted data")
    if envelope[:4] != MAGIC:
        raise CryptoError("invalid or corrupted data")

    version, cipher_id, nonce_length, tag_length = envelope[4], envelope[5], envelope[6], envelope[7]
    if version != VERSION or cipher_id not in _TAG_LEN or tag_length != _TAG_LEN[cipher_id] or nonce_length != _NONCE_LEN:
        raise CryptoError("invalid or corrupted data")
    if len(envelope) < 8 + nonce_length + tag_length:
        raise CryptoError("invalid or corrupted data")

    nonce = bytes(envelope[8:8 + nonce_length])
    ciphertext = bytes(envelope[8 + nonce_length:-tag_length])
    tag = bytes(envelope[-tag_length:])
    authenticated = bytes(envelope[:8]) + aad
    encryption_key, mac_key = _derive_pair(key)

    if cipher_id == CIPHER_AESGCM:
        if not aesgcm_available():
            raise CryptoError("this build uses AES-GCM; install the 'cryptography' package")
        return _aesgcm_decrypt(encryption_key, nonce, ciphertext, authenticated, tag)

    if cipher_id == CIPHER_CHACHA_POLY:
        if not aesgcm_available():
            raise CryptoError("this build uses ChaCha20-Poly1305; install the 'cryptography' package")
        return _chacha_poly_decrypt(encryption_key, nonce, ciphertext, authenticated, tag)

    expected = hmac.new(mac_key, authenticated + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise CryptoError("invalid or corrupted data")
    return chacha20_xor(encryption_key, nonce, ciphertext)


def derive_module_key(build_key: bytes, module_id: str) -> bytes:
    salt = hashlib.sha256(b"pyshield-module-v1" + module_id.encode("utf-8")).digest()
    return hkdf_sha256(build_key, salt=salt, info=b"pyshield-module-key", length=32)


def module_aad(module_id: str) -> bytes:
    return b"pyshield-module:" + module_id.encode("utf-8")


LICENSE_SIGNATURE_PREFIX = b"pyshield-license-v2:"


def license_signing_message(payload: dict) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    data = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return LICENSE_SIGNATURE_PREFIX + data


def b85encode(data: bytes) -> str:
    return base64.b85encode(data).decode("ascii")


def b85decode(text: str) -> bytes:
    if isinstance(text, bytes):
        text = text.decode("ascii")
    return base64.b85decode(text.encode("ascii"))


# ---------------------------------------------------------------------------
# Self test
# ---------------------------------------------------------------------------


def selftest() -> str:
    key = bytes(range(32))
    nonce = bytes.fromhex("000000090000004a00000000")
    expected = bytes.fromhex(
        "10f1e7e4d13b5915500fdd1fa32071c4"
        "c7d1f4c733c068030422aa9ac3d46c4e"
        "d2826446079faa0914c2d705d98b02a2"
        "b5129cd1de164eb9cbd083e8a2503c4e"
    )
    if _chacha20_block(key, 1, nonce) != expected:
        raise AssertionError("ChaCha20 block vector mismatch")

    ciphers = ["chacha"]
    if aesgcm_available():
        ciphers.append("chacha20poly1305")
        ciphers.append("aesgcm")
    for cipher in ciphers:
        requested = "chacha-pure" if cipher == "chacha" else cipher
        for size in (0, 1, 14, 63, 64, 65, 4096):
            plaintext = bytes((i * 7 + size) & 0xFF for i in range(size))
            envelope = seal(key, plaintext, b"module:demo", cipher=requested)
            if open_envelope(key, envelope, b"module:demo") != plaintext:
                raise AssertionError(f"{cipher} round trip failed at size {size}")
            try:
                open_envelope(key, envelope, b"module:other")
            except CryptoError:
                pass
            else:
                raise AssertionError(f"{cipher} accepted wrong aad")
            tampered = bytearray(envelope)
            tampered[-1] ^= 0x01
            try:
                open_envelope(key, bytes(tampered), b"module:demo")
            except CryptoError:
                pass
            else:
                raise AssertionError(f"{cipher} accepted tampered tag")
    return ",".join(ciphers)


if __name__ == "__main__":
    print("crypto selftest OK:", selftest())
