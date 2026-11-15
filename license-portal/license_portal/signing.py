"""Signing-key storage for the license portal.

The portal needs an Ed25519 private key to issue machine-bound entitlements
online.  The key is encrypted at rest with a passphrase-derived key.  The
corresponding public key is injected into the desktop builder at packaging
time, so rotating this key requires rebuilding clients.

For an early deployment the passphrase can live in a root-only file generated
on first start.  Production deployments should provide it through the process
environment or a systemd credential.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pyshield import crypto, ed25519, tool_license

from . import security

KEY_FORMAT = "pyshield-portal-signing-key/1"
_KEY_AAD = b"pyshield-portal-signing-key-v1"


class SigningKeyError(Exception):
    """Raised when the signing key cannot be created, read or decrypted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class SigningKey:
    key_id: str
    seed: bytes
    public_key: bytes
    created: str

    @property
    def public_b85(self) -> str:
        return crypto.b85encode(self.public_key)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.public_key).hexdigest()


class SigningKeyStore:
    def __init__(self, path: str | Path, passphrase: str) -> None:
        self.path = Path(path).expanduser()
        if not passphrase:
            raise SigningKeyError("签发密钥口令不能为空")
        self.passphrase = passphrase

    @classmethod
    def for_data_dir(
        cls,
        data_dir: str | Path,
        passphrase: str = "",
    ) -> "SigningKeyStore":
        directory = Path(data_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        pass_path = directory / "signing.pass"
        resolved = (passphrase or os.environ.get("PYSHELD_PORTAL_SIGNING_PASSPHRASE", "")).strip()
        if not resolved:
            if pass_path.is_file():
                resolved = pass_path.read_text(encoding="utf-8").strip()
            else:
                resolved = secrets.token_urlsafe(32)
                _write_private(pass_path, (resolved + "\n").encode("ascii"))
                print(
                    "[license-portal] generated signing passphrase at "
                    f"{pass_path}; move it to a systemd credential for production",
                    file=sys.stderr,
                    flush=True,
                )
            if not resolved:
                raise SigningKeyError("签发密钥口令文件为空")
        return cls(directory / "signing.key", resolved)

    def ensure(self) -> SigningKey:
        if self.path.is_file():
            return self.load()
        return self.generate()

    def load(self) -> SigningKey:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SigningKeyError(f"签发密钥不存在：{self.path}") from exc
        except (OSError, ValueError) as exc:
            raise SigningKeyError(f"无法读取签发密钥：{exc}") from exc
        if not isinstance(document, dict) or document.get("format") != KEY_FORMAT:
            raise SigningKeyError("签发密钥格式不受支持")
        try:
            salt = crypto.b85decode(document["salt"])
            encrypted = crypto.b85decode(document["seed_encrypted"])
            public_key = crypto.b85decode(document["public_key"])
        except Exception as exc:  # noqa: BLE001 - every failure means a corrupt key file
            raise SigningKeyError("签发密钥文件已损坏") from exc
        key = security.scrypt_key(self.passphrase, salt)
        try:
            seed = crypto.open_envelope(key, encrypted, _KEY_AAD)
        except crypto.CryptoError as exc:
            raise SigningKeyError("签发密钥解密失败，口令可能不正确") from exc
        if len(seed) != 32 or ed25519.public_key(seed) != public_key:
            raise SigningKeyError("签发密钥与公钥不匹配")
        return SigningKey(
            key_id=str(document.get("key_id") or hashlib.sha256(public_key).hexdigest()[:16]),
            seed=seed,
            public_key=public_key,
            created=str(document.get("created") or ""),
        )

    def generate(self) -> SigningKey:
        return self._write_seed(os.urandom(32))

    def import_seed(self, seed: bytes) -> SigningKey:
        """Store an existing Ed25519 seed (for example an offline root key)."""
        if len(seed) != 32:
            raise SigningKeyError("Ed25519 seed must be 32 bytes")
        return self._write_seed(seed)

    def _write_seed(self, seed: bytes) -> SigningKey:
        public_key = ed25519.public_key(seed)
        key_id = hashlib.sha256(public_key).hexdigest()[:16]
        salt = os.urandom(16)
        key = security.scrypt_key(self.passphrase, salt)
        encrypted = crypto.seal(key, seed, _KEY_AAD, cipher="chacha-pure")
        document = {
            "format": KEY_FORMAT,
            "key_id": key_id,
            "public_key": crypto.b85encode(public_key),
            "salt": crypto.b85encode(salt),
            "seed_encrypted": crypto.b85encode(encrypted),
            "created": _now(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_private(
            self.path,
            json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        return SigningKey(key_id=key_id, seed=seed, public_key=public_key, created=document["created"])

    def rotate(self) -> SigningKey:
        if self.path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.path.with_name(f"{self.path.name}.{stamp}.bak")
            try:
                os.replace(self.path, backup)
            except OSError as exc:
                raise SigningKeyError(f"无法备份旧签发密钥：{exc}") from exc
        return self.generate()


def sign_payload(key: SigningKey, payload: dict, random_suffix: str = "") -> dict:
    """Sign an entitlement payload after adding audit-only fields."""
    body = dict(payload)
    if random_suffix and "nonce" not in body:
        body["nonce"] = random_suffix
    body.pop("signature", None)
    return tool_license.sign_document(tool_license.TOOL_LICENSE_FORMAT, body, key.seed)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
