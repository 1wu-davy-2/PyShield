"""Passwords, cookies, CSRF and secret encryption for the license portal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from pyshield import crypto

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_LEN = 32
_CSRF_COOKIE = "pyshield_portal_csrf"
_SESSION_COOKIE = "pyshield_portal_session"


class SecurityError(Exception):
    """Raised when a password, cookie or encrypted secret is invalid."""


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def random_hex(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)


def random_digits(length: int = 6) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def scrypt_key(passphrase: str, salt: bytes, length: int = _SCRYPT_LEN) -> bytes:
    if not passphrase:
        raise SecurityError("passphrase must not be empty")
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=length,
    )


def hash_password(password: str) -> str:
    if not password:
        raise SecurityError("password must not be empty")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_SCRYPT_LEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_text, hash_text = str(encoded).split("$", 5)
        if scheme != "scrypt":
            return False
        salt = _unb64(salt_text)
        expected = _unb64(hash_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except Exception:  # noqa: BLE001 - any parse/derivation failure means "no"
        return False
    return hmac.compare_digest(actual, expected)


def hash_token(token: str, secret: bytes) -> str:
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_key(key: str, secret: bytes) -> str:
    return hmac.new(secret, b"license-key:" + key.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_ip(ip: str, secret: bytes) -> str:
    return hmac.new(secret, b"ip:" + str(ip or "").encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def hash_code(code: str, secret: bytes) -> str:
    return hmac.new(secret, b"email-code:" + code.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_text(secret: bytes, plaintext: str, aad: bytes = b"") -> str:
    envelope = crypto.seal(secret, plaintext.encode("utf-8"), aad, cipher="chacha-pure")
    return crypto.b85encode(envelope)


def decrypt_text(secret: bytes, encoded: str, aad: bytes = b"") -> str:
    try:
        envelope = crypto.b85decode(encoded)
        return crypto.open_envelope(secret, envelope, aad).decode("utf-8")
    except (crypto.CryptoError, ValueError, UnicodeDecodeError) as exc:
        raise SecurityError("无法解密服务端密钥，请检查 portal.secret 是否被替换") from exc


def derive_scoped_key(secret: bytes, purpose: str) -> bytes:
    return crypto.hkdf_sha256(
        secret, salt=b"pyshield-portal-secret-v1", info=purpose.encode("utf-8"), length=32,
    )


@dataclass(frozen=True)
class SignedCookie:
    value: str
    signature: str

    def encode(self) -> str:
        return f"{self.value}.{self.signature}"


def sign_cookie(secret: bytes, value: str, purpose: str, ttl_seconds: int = 0) -> str:
    payload = value
    if ttl_seconds:
        payload = f"{value}|{int(time.time()) + int(ttl_seconds)}"
    signature = hmac.new(
        secret, f"{purpose}:{payload}".encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_cookie(secret: bytes, cookie: str, purpose: str) -> str | None:
    if not cookie or "." not in cookie:
        return None
    payload, _, signature = cookie.rpartition(".")
    expected = hmac.new(
        secret, f"{purpose}:{payload}".encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    value = payload
    if "|" in payload:
        value, _, expires_text = payload.rpartition("|")
        try:
            if int(expires_text) < int(time.time()):
                return None
        except ValueError:
            return None
    return value


def new_csrf_token(secret: bytes) -> tuple[str, str]:
    """Return ``(raw_token, signed_cookie_value)`` for a public form."""
    token = random_token(24)
    return token, sign_cookie(secret, token, "csrf", ttl_seconds=2 * 60 * 60)


def verify_csrf(secret: bytes, cookie: str, submitted: str) -> bool:
    raw = verify_cookie(secret, cookie, "csrf")
    return bool(raw) and hmac.compare_digest(raw, str(submitted or ""))


def csrf_cookie_name() -> str:
    return _CSRF_COOKIE


def session_cookie_name() -> str:
    return _SESSION_COOKIE


def parse_cookies(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in str(header or "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            cookies[name.strip()] = value.strip()
    return cookies


def security_headers(*, dev: bool = False, site_url: str = "") -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }
    if not dev:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    script_sources = "'self' 'unsafe-inline'"
    if "challenges.cloudflare.com" not in script_sources:
        script_sources += " https://challenges.cloudflare.com"
    headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src {script_sources}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-src https://challenges.cloudflare.com; "
        "connect-src 'self' https://challenges.cloudflare.com; "
        "base-uri 'none'; form-action 'self'"
    )
    return headers
