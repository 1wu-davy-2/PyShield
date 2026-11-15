"""Tests for license_portal.security."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # .../PyShield
PORTAL = Path(__file__).resolve().parents[1]  # .../PyShield/license-portal
for _path in (ROOT, PORTAL):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from pyshield import crypto

from license_portal import security
from license_portal.security import SecurityError

SECRET = b"x" * 32
OTHER_SECRET = b"y" * 32


class PasswordTests(unittest.TestCase):
    def test_hash_and_verify_round_trip(self) -> None:
        encoded = security.hash_password("correct horse battery staple")
        self.assertTrue(encoded.startswith("scrypt$"))
        self.assertTrue(
            security.verify_password("correct horse battery staple", encoded),
        )

    def test_rejects_wrong_password(self) -> None:
        encoded = security.hash_password("correct horse battery staple")
        self.assertFalse(security.verify_password("wrong password", encoded))
        self.assertFalse(security.verify_password("", encoded))

    def test_rejects_empty_password_and_malformed_hashes(self) -> None:
        with self.assertRaises(SecurityError):
            security.hash_password("")
        for encoded in ("", "not-a-hash", "bcrypt$1$2$3$4$5", "scrypt$bad"):
            with self.subTest(encoded=encoded):
                self.assertFalse(security.verify_password("secret", encoded))


class CookieTests(unittest.TestCase):
    def test_sign_and_verify_round_trip(self) -> None:
        cookie = security.sign_cookie(SECRET, "session-value", "session")
        self.assertEqual(
            security.verify_cookie(SECRET, cookie, "session"), "session-value",
        )
        with_ttl = security.sign_cookie(
            SECRET, "session-value", "session", ttl_seconds=300,
        )
        self.assertEqual(
            security.verify_cookie(SECRET, with_ttl, "session"), "session-value",
        )

    def test_expired_cookie_is_rejected(self) -> None:
        cookie = security.sign_cookie(
            SECRET, "session-value", "session", ttl_seconds=-60,
        )
        self.assertIsNone(security.verify_cookie(SECRET, cookie, "session"))

    def test_tampering_is_rejected(self) -> None:
        cookie = security.sign_cookie(
            SECRET, "session-value", "session", ttl_seconds=300,
        )
        tampered_value = "x" + cookie[1:]
        tampered_signature = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
        self.assertIsNone(security.verify_cookie(SECRET, tampered_value, "session"))
        self.assertIsNone(
            security.verify_cookie(SECRET, tampered_signature, "session"),
        )
        self.assertIsNone(security.verify_cookie(SECRET, cookie, "other-purpose"))

    def test_other_secret_and_malformed_cookies_are_rejected(self) -> None:
        cookie = security.sign_cookie(SECRET, "session-value", "session")
        self.assertIsNone(security.verify_cookie(OTHER_SECRET, cookie, "session"))
        self.assertIsNone(security.verify_cookie(SECRET, "", "session"))
        self.assertIsNone(security.verify_cookie(SECRET, "no-dot", "session"))

    def test_csrf_round_trip(self) -> None:
        token, cookie = security.new_csrf_token(SECRET)
        self.assertTrue(security.verify_csrf(SECRET, cookie, token))
        self.assertFalse(security.verify_csrf(SECRET, cookie, "wrong-token"))
        self.assertFalse(security.verify_csrf(SECRET, cookie, ""))


class HashingTests(unittest.TestCase):
    def test_hash_key_is_stable_and_secret_bound(self) -> None:
        key = "PSP-9ABC-DEFG-HJKM-NPQR"
        first = security.hash_key(key, SECRET)
        second = security.hash_key(key, SECRET)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        int(first, 16)  # must be a hex digest
        self.assertNotEqual(first, security.hash_key(key, OTHER_SECRET))
        self.assertNotEqual(
            first, security.hash_key("PSP-0000-0000-0000-0000", SECRET),
        )

    def test_hash_ip_is_stable_and_secret_bound(self) -> None:
        first = security.hash_ip("203.0.113.7", SECRET)
        self.assertEqual(first, security.hash_ip("203.0.113.7", SECRET))
        self.assertEqual(len(first), 32)
        int(first, 16)
        self.assertNotEqual(first, security.hash_ip("203.0.113.8", SECRET))
        self.assertNotEqual(first, security.hash_ip("203.0.113.7", OTHER_SECRET))


class EncryptionTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        plaintext = "license-key-秘密-é"
        encoded = security.encrypt_text(SECRET, plaintext, b"license-key")
        self.assertEqual(
            security.decrypt_text(SECRET, encoded, b"license-key"), plaintext,
        )

    def test_round_trip_without_aad(self) -> None:
        encoded = security.encrypt_text(SECRET, "hello")
        self.assertEqual(security.decrypt_text(SECRET, encoded), "hello")

    def test_wrong_aad_or_secret_fails(self) -> None:
        encoded = security.encrypt_text(SECRET, "hello", b"aad")
        with self.assertRaises(SecurityError):
            security.decrypt_text(SECRET, encoded, b"other")
        with self.assertRaises(SecurityError):
            security.decrypt_text(OTHER_SECRET, encoded, b"aad")

    def test_tampered_ciphertext_fails(self) -> None:
        encoded = security.encrypt_text(SECRET, "hello", b"aad")
        envelope = bytearray(crypto.b85decode(encoded))
        envelope[-1] ^= 0x01
        tampered = crypto.b85encode(bytes(envelope))
        with self.assertRaises(SecurityError):
            security.decrypt_text(SECRET, tampered, b"aad")

    def test_invalid_encoding_fails(self) -> None:
        with self.assertRaises(SecurityError):
            security.decrypt_text(SECRET, "!!not-valid-base85!!")


if __name__ == "__main__":
    unittest.main()
