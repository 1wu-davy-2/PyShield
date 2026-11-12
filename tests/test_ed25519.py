from __future__ import annotations

import unittest

from pyshield import ed25519

VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


class Ed25519Tests(unittest.TestCase):
    def test_rfc8032_vectors_pure(self):
        for seed_hex, public_hex, message_hex, signature_hex in VECTORS:
            seed = bytes.fromhex(seed_hex)
            message = bytes.fromhex(message_hex)
            public = ed25519._pure_public_key(seed)
            signature = ed25519._pure_sign(seed, message)
            self.assertEqual(public.hex(), public_hex)
            self.assertEqual(signature.hex(), signature_hex)
            self.assertTrue(ed25519._pure_verify(public, message, signature))

    def test_rfc8032_vectors_public_api(self):
        for seed_hex, public_hex, message_hex, signature_hex in VECTORS:
            seed = bytes.fromhex(seed_hex)
            message = bytes.fromhex(message_hex)
            self.assertEqual(ed25519.public_key(seed).hex(), public_hex)
            self.assertEqual(ed25519.sign(seed, message).hex(), signature_hex)
            self.assertTrue(ed25519.verify(bytes.fromhex(public_hex), message, bytes.fromhex(signature_hex)))

    def test_native_matches_pure(self):
        if ed25519._native_classes() is None:
            self.skipTest("cryptography is not installed")
        seeds = [bytes([index]) * 32 for index in (0, 1, 7, 255)]
        messages = [b"", b"pyshield", bytes(range(256))]
        for seed in seeds:
            self.assertEqual(ed25519.public_key(seed), ed25519._pure_public_key(seed))
            for message in messages:
                self.assertEqual(ed25519.sign(seed, message), ed25519._pure_sign(seed, message))

    def test_rejects_tampering(self):
        seed = bytes(range(32))
        public = ed25519.public_key(seed)
        signature = ed25519.sign(seed, b"license payload")
        self.assertTrue(ed25519.verify(public, b"license payload", signature))
        self.assertFalse(ed25519.verify(public, b"license payload!", signature))
        tampered = bytearray(signature)
        tampered[0] ^= 1
        self.assertFalse(ed25519.verify(public, b"license payload", bytes(tampered)))
        self.assertFalse(ed25519.verify(b"\x00" * 32, b"license payload", signature))
        self.assertFalse(ed25519.verify(public, b"license payload", b"\x00" * 63))

    def test_seed_length(self):
        with self.assertRaises(ValueError):
            ed25519.public_key(b"\x00" * 31)
        with self.assertRaises(ValueError):
            ed25519.sign(b"\x00" * 33, b"message")


if __name__ == "__main__":
    unittest.main()
