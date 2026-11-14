from __future__ import annotations

import struct
import unittest

from pyshield import crypto


def _reference_chacha20_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """Straightforward per-block XOR used to validate the chunked fast path."""
    key_words = struct.unpack("<8I", key)
    nonce_words = struct.unpack("<3I", nonce)
    output = bytearray()
    for index in range((len(data) + 63) // 64):
        block = crypto._chacha20_block_words(key_words, 1 + index, nonce_words)
        chunk = data[index * 64:(index + 1) * 64]
        output.extend(bytes(left ^ right for left, right in zip(chunk, block)))
    return bytes(output)


class CryptoTests(unittest.TestCase):
    def test_selftest(self) -> None:
        ciphers = crypto.selftest().split(",")
        self.assertIn("chacha", ciphers)
        if crypto.aesgcm_available():
            self.assertIn("chacha20poly1305", ciphers)
            self.assertIn("aesgcm", ciphers)

    def test_module_keys_are_unique(self) -> None:
        build_key = bytes(range(32))
        first = crypto.derive_module_key(build_key, "blog.views")
        second = crypto.derive_module_key(build_key, "blog.models")
        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, second)
        self.assertNotEqual(
            crypto.module_aad("0123456789abcdef", "blog.views"),
            crypto.module_aad("0123456789abcdef", "blog.models"),
        )

    def test_module_aad_binds_build_id(self) -> None:
        self.assertNotEqual(
            crypto.module_aad("0123456789abcdef", "blog.views"),
            crypto.module_aad("fedcba9876543210", "blog.views"),
        )
        self.assertIn(b"0123456789abcdef", crypto.module_aad("0123456789abcdef", "blog.views"))

    def test_envelope_rejects_wrong_key(self) -> None:
        envelope = crypto.seal(b"\x01" * 32, b"payload", b"aad", cipher="chacha-pure")
        with self.assertRaises(crypto.CryptoError):
            crypto.open_envelope(b"\x02" * 32, envelope, b"aad")

    def test_base85_round_trip(self) -> None:
        data = bytes(range(256)) * 3
        self.assertEqual(crypto.b85decode(crypto.b85encode(data)), data)

    def test_pure_chacha_chunking_matches_reference(self) -> None:
        original = crypto._fast_chacha
        crypto._fast_chacha = None
        try:
            key = bytes(range(32))
            nonce = bytes.fromhex("000000090000004a00000000")
            for size in (0, 1, 63, 64, 65, 65535, 65536, 65537, 200000):
                data = bytes((index * 37 + 11) & 0xFF for index in range(size))
                self.assertEqual(
                    crypto.chacha20_xor(key, nonce, data),
                    _reference_chacha20_xor(key, nonce, data),
                    f"chunk boundary mismatch at size {size}",
                )
        finally:
            crypto._fast_chacha = original

    def test_native_ciphers_round_trip(self) -> None:
        if not crypto.aesgcm_available():
            self.skipTest("cryptography is not installed")
        for name in ("aesgcm", "chacha20poly1305"):
            envelope = crypto.seal(b"\x03" * 32, b"payload", b"aad", cipher=name)
            self.assertEqual(crypto.open_envelope(b"\x03" * 32, envelope, b"aad"), b"payload")
            self.assertEqual(envelope[5], crypto.CIPHER_AESGCM if name == "aesgcm" else crypto.CIPHER_CHACHA_POLY)

    def test_signing_messages_are_domain_separated(self) -> None:
        payload = {"format": 1, "build_id": "x", "signature": "ignored"}
        self.assertNotEqual(
            crypto.license_signing_message(payload),
            crypto.manifest_signing_message(payload),
        )
        self.assertNotIn(b"signature", crypto.manifest_signing_message(payload))

    def test_resolve_cipher(self) -> None:
        self.assertEqual(crypto.resolve_cipher("chacha-pure"), "chacha")
        if crypto.aesgcm_available():
            self.assertEqual(crypto.resolve_cipher("auto"), "aesgcm")
            self.assertEqual(crypto.resolve_cipher("chacha"), "chacha20poly1305")
            self.assertEqual(crypto.resolve_cipher("aesgcm"), "aesgcm")
        else:
            self.assertEqual(crypto.resolve_cipher("auto"), "chacha")
            self.assertEqual(crypto.resolve_cipher("chacha"), "chacha")
            with self.assertRaises(ValueError):
                crypto.resolve_cipher("aesgcm")


if __name__ == "__main__":
    unittest.main()
