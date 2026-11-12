from __future__ import annotations

import unittest

from pyshield import crypto


class CryptoTests(unittest.TestCase):
    def test_selftest(self) -> None:
        ciphers = crypto.selftest().split(",")
        self.assertIn("chacha", ciphers)

    def test_module_keys_are_unique(self) -> None:
        build_key = bytes(range(32))
        first = crypto.derive_module_key(build_key, "blog.views")
        second = crypto.derive_module_key(build_key, "blog.models")
        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, second)
        self.assertNotEqual(crypto.module_aad("blog.views"), crypto.module_aad("blog.models"))

    def test_envelope_rejects_wrong_key(self) -> None:
        envelope = crypto.seal(b"\x01" * 32, b"payload", b"aad", cipher="chacha")
        with self.assertRaises(crypto.CryptoError):
            crypto.open_envelope(b"\x02" * 32, envelope, b"aad")

    def test_base85_round_trip(self) -> None:
        data = bytes(range(256)) * 3
        self.assertEqual(crypto.b85decode(crypto.b85encode(data)), data)

    def test_resolve_cipher(self) -> None:
        self.assertEqual(crypto.resolve_cipher("chacha"), "chacha")
        if crypto.aesgcm_available():
            self.assertEqual(crypto.resolve_cipher("aesgcm"), "aesgcm")
        else:
            with self.assertRaises(ValueError):
                crypto.resolve_cipher("aesgcm")


if __name__ == "__main__":
    unittest.main()
