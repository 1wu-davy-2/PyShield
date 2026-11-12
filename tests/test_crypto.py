from __future__ import annotations

import unittest

from pyshield import crypto


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
        self.assertNotEqual(crypto.module_aad("blog.views"), crypto.module_aad("blog.models"))

    def test_envelope_rejects_wrong_key(self) -> None:
        envelope = crypto.seal(b"\x01" * 32, b"payload", b"aad", cipher="chacha-pure")
        with self.assertRaises(crypto.CryptoError):
            crypto.open_envelope(b"\x02" * 32, envelope, b"aad")

    def test_base85_round_trip(self) -> None:
        data = bytes(range(256)) * 3
        self.assertEqual(crypto.b85decode(crypto.b85encode(data)), data)

    def test_native_ciphers_round_trip(self) -> None:
        if not crypto.aesgcm_available():
            self.skipTest("cryptography is not installed")
        for name in ("aesgcm", "chacha20poly1305"):
            envelope = crypto.seal(b"\x03" * 32, b"payload", b"aad", cipher=name)
            self.assertEqual(crypto.open_envelope(b"\x03" * 32, envelope, b"aad"), b"payload")
            self.assertEqual(envelope[5], crypto.CIPHER_AESGCM if name == "aesgcm" else crypto.CIPHER_CHACHA_POLY)

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
