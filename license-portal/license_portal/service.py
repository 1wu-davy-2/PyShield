"""Business logic for the PyShield license portal.

The service owns the transaction boundaries.  HTTP handlers only parse input,
call a method here, and render the result.  This keeps the trial flow, key
activation, renewal, revocation and the admin console on the same rules.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from pyshield import crypto, license_admin, tool_license, trial_license

from . import security, validation
from .captcha import Captcha, CaptchaError, build_captcha
from .config import Config
from .db import Database, now_iso, today
from .mailer import Mailer, MailerError, build_mailer
from .signing import SigningKey, SigningKeyStore

LOGGER = logging.getLogger("license_portal")

CODE_TTL_SECONDS = 10 * 60
CODE_RESEND_HINT_SECONDS = 60
MAX_CODE_ATTEMPTS = 5
MAX_LIST_ROWS = 500


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.status = int(status)
        self.code = code


def today_date() -> date:
    return today()


def _iso_offset(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _json_load(value: str | None, fallback):
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed


def load_or_create_secret(path: str | Path, value: bytes | None = None) -> bytes:
    target = Path(path).expanduser()
    if value is not None:
        if len(value) != 32:
            raise ServiceError("portal secret must be 32 bytes", 500)
        return value
    try:
        raw = target.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        raw = ""
    except OSError as exc:
        raise ServiceError(f"无法读取 portal.secret：{exc}", 500) from exc
    if raw:
        try:
            secret = bytes.fromhex(raw)
        except ValueError:
            try:
                secret = crypto.b85decode(raw)
            except Exception as exc:
                raise ServiceError("portal.secret 内容无效", 500) from exc
        if len(secret) != 32:
            raise ServiceError("portal.secret 长度无效", 500)
        return secret
    secret = secrets.token_bytes(32)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with open(temporary, "w", encoding="ascii", newline="\n") as handle:
        handle.write(secret.hex() + "\n")
        handle.flush()
    try:
        import os

        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(target)
    return secret


class PortalService:
    def __init__(
        self,
        *,
        db: Database,
        config: Config,
        signing_key: SigningKey,
        secret: bytes,
        mailer: Mailer | None = None,
        captcha: Captcha | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.signing_key = signing_key
        self.secret = secret
        self.mailer = mailer or build_mailer(config)
        self.captcha = captcha or build_captcha(config)
        self._key_hash_secret = security.derive_scoped_key(secret, "license-key-hash")
        self._key_encryption_key = security.derive_scoped_key(secret, "license-key-encryption")
        self._ip_secret = security.derive_scoped_key(secret, "ip-hash")
        self._code_secret = security.derive_scoped_key(secret, "email-code")
        self._session_secret = security.derive_scoped_key(secret, "session")
        self._csrf_secret = security.derive_scoped_key(secret, "csrf")

    # ------------------------------------------------------------------
    # shared helpers
    # ------------------------------------------------------------------

    def hash_ip(self, ip: str) -> str:
        return security.hash_ip(ip, self._ip_secret)

    def _subject_hash(self, value: str) -> str:
        return security.hash_token(str(value or "").lower(), self._ip_secret)[:32]

    def csrf_secret(self) -> bytes:
        return self._csrf_secret

    def session_secret(self) -> bytes:
        return self._session_secret

    def _hash_code(self, code: str) -> str:
        return security.hash_code(code, self._code_secret)

    def _hash_key(self, key: str) -> str:
        return security.hash_key(key, self._key_hash_secret)

    def _encrypt_key(self, key: str) -> str:
        return security.encrypt_text(self._key_encryption_key, key, b"license-key")

    def _decrypt_key(self, value: str) -> str:
        return security.decrypt_text(self._key_encryption_key, value, b"license-key")

    def _log(
        self,
        connection: sqlite3.Connection,
        actor_type: str,
        actor: str,
        action: str,
        *,
        serial: str = "",
        license_id: str = "",
        key_prefix: str = "",
        ip_hash: str = "",
        detail="",
    ) -> None:
        if not isinstance(detail, str):
            detail = _json(detail)
        connection.execute(
            "INSERT INTO events (at, actor_type, actor, action, serial, license_id,"
            " key_prefix, ip_hash, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), actor_type, actor, action, serial, license_id,
             key_prefix, ip_hash, detail[:4000]),
        )

    def _consume_limits(self, limits: list[tuple[str, int, int]]) -> None:
        """Count an attempt even when the business action later fails.

        Rate-limit buckets are committed in their own transaction.  Counting
        them inside the caller's transaction would let an attacker reset the
        counter by triggering a validation failure that rolls the transaction
        back.
        """
        exceeded = False
        with self.db.transaction() as connection:
            for bucket, limit, window_seconds in limits:
                now = int(time.time())
                window_start = now // max(1, window_seconds)
                key = f"{bucket}:{window_start}"
                row = connection.execute(
                    "SELECT count FROM rate_limits WHERE bucket = ?", (key,),
                ).fetchone()
                count = int(row["count"]) if row else 0
                if count >= limit:
                    exceeded = True
                if row:
                    connection.execute(
                        "UPDATE rate_limits SET count = count + 1 WHERE bucket = ?", (key,),
                    )
                else:
                    connection.execute(
                        "INSERT INTO rate_limits (bucket, window_start, count) VALUES (?, ?, 1)",
                        (key, window_start),
                    )
                connection.execute(
                    "DELETE FROM rate_limits WHERE window_start < ?", (window_start - 2,),
                )
        if exceeded:
            raise ServiceError("操作过于频繁，请稍后再试", 429, "rate_limited")

    def _find_license(self, connection: sqlite3.Connection, serial: str) -> dict | None:
        return self.db.query_one(
            "SELECT * FROM licenses WHERE serial = ?", (serial,), connection=connection,
        )

    def _find_application(self, connection: sqlite3.Connection, application_id: int) -> dict | None:
        return self.db.query_one(
            "SELECT * FROM applications WHERE id = ?", (application_id,), connection=connection,
        )

    def get_application(self, application_id: int) -> dict | None:
        return self.db.query_one(
            "SELECT a.*, c.email AS customer_email, c.name AS customer_name"
            " FROM applications a JOIN customers c ON c.id = a.customer_id"
            " WHERE a.id = ?",
            (int(application_id),),
        )

    def _customer_label(self, customer: dict | None) -> str:
        if not customer:
            return ""
        if customer.get("company_name"):
            return str(customer["company_name"])
        return str(customer.get("name") or "")

    def _license_status(self, row: dict) -> str:
        if row.get("status") == "revoked":
            return "revoked"
        if validation.is_expired(row.get("expires"), grace_days=int(row.get("grace_days") or 0)):
            return "expired"
        return "active"

    def _key_status(self, row: dict) -> str:
        if row.get("status") == "revoked":
            return "revoked"
        if row.get("expires_at"):
            try:
                if date.fromisoformat(str(row["expires_at"])) < today():
                    return "expired"
            except ValueError:
                return "expired"
        return str(row.get("status") or "unused")

    def _row_to_license(self, row: dict | None) -> dict | None:
        if row is None:
            return None
        data = dict(row)
        data["features"] = _json_load(data.get("features"), [])
        data["fingerprints"] = _json_load(data.get("fingerprints"), [])
        data["document"] = _json_load(data.get("document"), {})
        data["activation_required"] = bool(data.get("activation_required"))
        data["revision"] = int(data.get("revision") or 1)
        data["effective_status"] = self._license_status(data)
        return data

    def _row_to_key(self, row: dict | None) -> dict | None:
        if row is None:
            return None
        data = dict(row)
        data.pop("key_hash", None)
        data.pop("key_encrypted", None)
        data["features"] = _json_load(data.get("features"), [])
        data["effective_status"] = self._key_status(data)
        data["activated_count"] = int(data.get("activated_count") or 0)
        return data

    def _deep_link(self, serial: str, code: str = "") -> str:
        link = trial_license.deep_link(serial)
        if code:
            link = f"{link}&{urlencode({'code': code})}"
        return link

    def _issued_response(self, row: dict) -> dict:
        data = self._row_to_license(row)
        assert data is not None
        return {
            "ok": True,
            "status": "granted",
            "serial": data["serial"],
            "machine": data["machine"],
            "license_id": data["license_id"],
            "edition": data["edition"],
            "expires": data.get("expires"),
            "max_builds": data.get("max_builds_per_month"),
            "revision": data["revision"],
            "trial_code": data.get("display_code") or "",
            "document": data["document"],
            "deep_link": self._deep_link(data["serial"], data.get("display_code") or ""),
            "verification_required": False,
        }

    def _build_document(
        self,
        *,
        serial: str,
        machine: str,
        license_id: str,
        customer: str,
        edition: str,
        features: list[str],
        seats: int,
        max_activations: int,
        max_builds: int,
        grace_days: int,
        expires: date,
        revision: int,
        source: str,
        notes: str = "",
    ) -> dict:
        issued = today()
        payload = {
            "license_id": license_id,
            "customer": customer,
            "edition": edition,
            "features": list(features),
            "seats": int(seats),
            "max_activations": int(max_activations),
            "max_builds_per_month": int(max_builds),
            "bind_mode": tool_license.BIND_FIXED,
            "fingerprints": [machine],
            "activation_required": False,
            "issued": issued.isoformat(),
            "not_before": issued.isoformat(),
            "expires": expires.isoformat() if expires else None,
            "grace_days": int(grace_days),
            "revocation_epoch": 0,
            "revision": int(revision),
            "source": source,
            "serial": serial,
            "signing_key_id": self.signing_key.key_id,
            "notes": notes or "",
        }
        tool_license.parse_entitlement(payload)
        return tool_license.sign_document(
            tool_license.TOOL_LICENSE_FORMAT, payload, self.signing_key.seed,
        )

    def _write_license(
        self,
        connection: sqlite3.Connection,
        existing: dict | None,
        *,
        serial: str,
        machine: str,
        customer_id: int | None,
        license_id: str,
        edition: str,
        features: list[str],
        seats: int,
        max_activations: int,
        max_builds: int,
        grace_days: int,
        expires: date,
        revision: int,
        document: dict,
        source: str,
        key_id: int | None,
        display_code: str = "",
    ) -> int:
        now = now_iso()
        document_text = _json(document)
        if existing is None:
            cursor = connection.execute(
                "INSERT INTO licenses (serial, machine, customer_id, license_id, edition,"
                " features, seats, max_activations, max_builds_per_month, grace_days,"
                " bind_mode, fingerprints, activation_required, issued, not_before, expires,"
                " revision, status, display_code, document, source, key_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                (
                    serial, machine, customer_id, license_id, edition, _json(features), seats,
                    max_activations, max_builds, grace_days, tool_license.BIND_FIXED,
                    _json([machine]), 0, today().isoformat(), today().isoformat(),
                    expires.isoformat() if expires else None, revision, display_code,
                    document_text, source, key_id, now, now,
                ),
            )
            return int(cursor.lastrowid)
        connection.execute(
            "UPDATE licenses SET machine = ?, customer_id = ?, license_id = ?, edition = ?,"
            " features = ?, seats = ?, max_activations = ?, max_builds_per_month = ?,"
            " grace_days = ?, bind_mode = ?, fingerprints = ?, activation_required = 0,"
            " issued = ?, not_before = ?, expires = ?, revision = ?, status = 'active',"
            " display_code = ?, document = ?, source = ?, key_id = ?, updated_at = ?,"
            " revoked_at = NULL, revoked_reason = '' WHERE id = ?",
            (
                machine, customer_id, license_id, edition, _json(features), seats,
                max_activations, max_builds, grace_days, tool_license.BIND_FIXED,
                _json([machine]), today().isoformat(), today().isoformat(),
                expires.isoformat() if expires else None, revision, display_code,
                document_text, source, key_id, now, int(existing["id"]),
            ),
        )
        return int(existing["id"])

    def _license_id_for(self, edition: str, serial: str, existing: dict | None) -> str:
        if existing is not None and existing.get("edition") == edition:
            return str(existing["license_id"])
        prefix = {"trial": "TRIAL", "pro": "PRO", "enterprise": "ENT"}.get(edition, "LIC")
        return f"{prefix}-{serial}"

    def _customer_has_trial(
        self,
        connection: sqlite3.Connection,
        customer_id: int,
        *,
        exclude_application_id: int | None = None,
    ) -> bool:
        if exclude_application_id:
            row = connection.execute(
                "SELECT 1 FROM applications WHERE customer_id = ? AND status = 'verified'"
                " AND id <> ? LIMIT 1",
                (customer_id, int(exclude_application_id)),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT 1 FROM applications WHERE customer_id = ? AND status = 'verified' LIMIT 1",
                (customer_id,),
            ).fetchone()
        return row is not None

    def _machine_has_trial(self, connection: sqlite3.Connection, machine: str,
                           serial: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM applications WHERE machine = ? AND status = 'verified'"
            " AND serial <> ? LIMIT 1",
            (machine, serial),
        ).fetchone()
        return row is not None

    def _upsert_customer(
        self,
        connection: sqlite3.Connection,
        data: dict,
        *,
        ip_hash: str = "",
    ) -> dict:
        by_email = connection.execute(
            "SELECT * FROM customers WHERE email = ?", (data["email"],),
        ).fetchone()
        by_phone = connection.execute(
            "SELECT * FROM customers WHERE phone = ?", (data["phone"],),
        ).fetchone()
        by_email = dict(by_email) if by_email else None
        by_phone = dict(by_phone) if by_phone else None
        if by_email and by_phone and by_email["id"] != by_phone["id"]:
            raise ServiceError("该邮箱和手机号分别属于不同账户", 409, "customer_conflict")
        existing = by_email or by_phone
        now = now_iso()
        if existing:
            try:
                connection.execute(
                    "UPDATE customers SET email = ?, phone = ?, name = ?, org_type = ?,"
                    " company_name = ?, consent_at = ?, updated_at = ? WHERE id = ?",
                    (
                        data["email"], data["phone"], data["name"], data["org_type"],
                        data.get("company_name"), now, now, existing["id"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ServiceError("邮箱或手机号已被其他账户使用", 409, "customer_conflict") from exc
            return self.db.query_one(
                "SELECT * FROM customers WHERE id = ?", (existing["id"],), connection=connection,
            ) or existing
        try:
            cursor = connection.execute(
                "INSERT INTO customers (email, phone, name, org_type, company_name,"
                " consent_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["email"], data["phone"], data["name"], data["org_type"],
                    data.get("company_name"), now, now, now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ServiceError("邮箱或手机号已被其他账户使用", 409, "customer_conflict") from exc
        return self.db.query_one(
            "SELECT * FROM customers WHERE id = ?", (cursor.lastrowid,), connection=connection,
        ) or {}

    # ------------------------------------------------------------------
    # public trial flow
    # ------------------------------------------------------------------

    def start_trial(self, payload: dict, remote_ip: str, user_agent: str = "") -> dict:
        try:
            self.captcha.verify(str(payload.get("captcha_token") or ""), remote_ip)
        except CaptchaError as exc:
            raise ServiceError(str(exc), 400, "captcha_failed") from exc
        data = validation.validate_trial_application(payload)
        ip_hash = self.hash_ip(remote_ip)
        verification_required = bool(self.config.email_verification)
        application_id = 0
        code = ""

        self._consume_limits([
            (f"trial-ip:{ip_hash}", self.config.rate_limit_per_hour, 3600),
            (f"trial-email:{self._subject_hash(data['email'])}", 3, 3600),
            (f"trial-phone:{self._subject_hash(data['phone'])}", 3, 3600),
        ])

        with self.db.transaction() as connection:
            existing = self._find_license(connection, data["serial"])
            if existing is not None:
                if existing["machine"] != data["machine"]:
                    raise ServiceError("该序列号已绑定其他设备", 409, "serial_bound")
                current = self._row_to_license(existing)
                assert current is not None
                if existing["source"] == "trial":
                    if current["effective_status"] == "active":
                        return {**self._issued_response(existing), "already": True,
                                "verification_required": False}
                    if current["effective_status"] == "revoked":
                        raise ServiceError("该设备的试用已被吊销，请联系管理员", 403, "trial_revoked")
                    raise ServiceError("试用已结束，请购买专业版或企业版", 409, "trial_expired")
                raise ServiceError("本机已有正式授权，无需申请试用", 409, "already_licensed")

            if self._machine_has_trial(connection, data["machine"], data["serial"]):
                raise ServiceError("本机已申请过试用，请勿重复申请", 409, "machine_trial_exists")

            customer = self._upsert_customer(connection, data, ip_hash=ip_hash)
            if customer.get("id") and self._customer_has_trial(connection, int(customer["id"])):
                raise ServiceError("该邮箱或手机号已申请过试用", 409, "customer_trial_exists")

            connection.execute(
                "UPDATE applications SET status = 'superseded', updated_at = ?"
                " WHERE serial = ? AND status = 'pending_email'",
                (now_iso(), data["serial"]),
            )
            status = "pending_email" if verification_required else "verified"
            cursor = connection.execute(
                "INSERT INTO applications (serial, machine, customer_id, edition, status,"
                " ip_hash, user_agent, created_at, updated_at) VALUES (?, ?, ?, 'trial', ?, ?, ?, ?, ?)",
                (
                    data["serial"], data["machine"], customer["id"], status, ip_hash,
                    str(user_agent or "")[:300], now_iso(), now_iso(),
                ),
            )
            application_id = int(cursor.lastrowid)
            self._log(
                connection, "public", data["email"], "trial_apply",
                serial=data["serial"], ip_hash=ip_hash,
                detail={"edition": "trial", "org_type": data["org_type"]},
            )

            if not verification_required:
                application = self._find_application(connection, application_id)
                assert application is not None
                return self._issue_trial_locked(connection, application)

            code = security.random_digits(6)
            code_expires = _iso_offset(CODE_TTL_SECONDS)
            connection.execute(
                "UPDATE applications SET code_hash = ?, code_expires = ?, attempts = 0,"
                " updated_at = ? WHERE id = ?",
                (self._hash_code(code), code_expires, now_iso(), application_id),
            )
            connection.execute(
                "INSERT INTO email_codes (application_id, email, code_hash, purpose,"
                " expires_at, created_at) VALUES (?, ?, ?, 'trial', ?, ?)",
                (application_id, data["email"], self._hash_code(code), code_expires, now_iso()),
            )

        try:
            self.mailer.send_code(data["email"], code, "trial")
        except MailerError as exc:
            self._log_failure("trial_mail_failed", data["serial"], ip_hash, str(exc))
            raise ServiceError("验证码发送失败，请稍后重试", 502, "mail_failed") from exc

        return {
            "ok": True,
            "application_id": application_id,
            "email": validation.mask_email(data["email"]),
            "phone": validation.mask_phone(data["phone"]),
            "verification_required": True,
            "expires_in": CODE_TTL_SECONDS,
        }

    def _log_failure(self, action: str, serial: str, ip_hash: str, detail: str) -> None:
        try:
            with self.db.transaction() as connection:
                self._log(
                    connection, "system", "", action, serial=serial,
                    ip_hash=ip_hash, detail={"error": detail[:500]},
                )
        except Exception:  # noqa: BLE001 - logging must never mask the original error
            LOGGER.exception("failed to write audit event %s", action)

    def verify_trial(self, application_id: int, code: str, remote_ip: str) -> dict:
        ip_hash = self.hash_ip(remote_ip)
        self._consume_limits([(f"verify-ip:{ip_hash}", 30, 3600)])
        failure: ServiceError | None = None
        result: dict | None = None
        with self.db.transaction() as connection:
            application = self._find_application(connection, application_id)
            if application is None:
                raise ServiceError("申请记录不存在或已失效", 404, "application_not_found")
            if application["status"] == "verified":
                existing = self._find_license(connection, application["serial"])
                if existing is not None:
                    return {**self._issued_response(existing), "already": True}
                raise ServiceError("该申请已完成，但授权记录缺失，请联系管理员", 500)
            if application["status"] != "pending_email":
                raise ServiceError("该申请已失效，请重新提交申请表", 409, "application_not_pending")
            expires = application.get("code_expires")
            if not expires or str(expires) < now_iso():
                raise ServiceError("验证码已过期，请重新提交申请表", 409, "code_expired")
            attempts = int(application.get("attempts") or 0)
            if attempts >= MAX_CODE_ATTEMPTS:
                raise ServiceError("验证码错误次数过多，请重新提交申请表", 429, "too_many_attempts")
            expected = str(application.get("code_hash") or "")
            actual = self._hash_code(str(code))
            if not hmac.compare_digest(expected, actual):
                # Commit the failed attempt instead of raising inside the
                # transaction: otherwise the counter and the audit event are
                # rolled back and the attempt limit never triggers.
                connection.execute(
                    "UPDATE applications SET attempts = attempts + 1, updated_at = ? WHERE id = ?",
                    (now_iso(), application_id),
                )
                self._log(
                    connection, "public", application["serial"], "trial_code_failed",
                    serial=application["serial"], ip_hash=ip_hash,
                    detail={"attempts": attempts + 1},
                )
                failure = ServiceError("验证码不正确", 400, "code_invalid")
            else:
                connection.execute(
                    "UPDATE applications SET status = 'verified', updated_at = ? WHERE id = ?",
                    (now_iso(), application_id),
                )
                connection.execute(
                    "UPDATE email_codes SET consumed_at = ?"
                    " WHERE application_id = ? AND consumed_at IS NULL",
                    (now_iso(), application_id),
                )
                result = self._issue_trial_locked(connection, application)
        if failure is not None:
            raise failure
        assert result is not None
        return result

    def resend_trial_code(self, application_id: int, remote_ip: str) -> dict:
        ip_hash = self.hash_ip(remote_ip)
        self._consume_limits([
            (f"resend-ip:{ip_hash}", 10, 3600),
            (f"resend-app:{int(application_id)}", 5, 3600),
        ])
        code = security.random_digits(6)
        code_expires = _iso_offset(CODE_TTL_SECONDS)
        serial = ""
        email = ""
        with self.db.transaction() as connection:
            application = self._find_application(connection, application_id)
            if application is None:
                raise ServiceError("申请记录不存在或已失效", 404, "application_not_found")
            if application["status"] == "verified":
                raise ServiceError(
                    "该申请已完成验证，无需重新发送验证码", 409, "application_verified",
                )
            if application["status"] != "pending_email":
                raise ServiceError(
                    "该申请已失效，请重新提交申请表", 409, "application_not_pending",
                )
            customer = connection.execute(
                "SELECT email FROM customers WHERE id = ?", (application["customer_id"],),
            ).fetchone()
            if customer is None:
                raise ServiceError("客户记录不存在，请重新提交申请表", 409)
            serial = str(application["serial"])
            email = str(customer["email"])
            connection.execute(
                "UPDATE applications SET code_hash = ?, code_expires = ?, attempts = 0,"
                " updated_at = ? WHERE id = ?",
                (self._hash_code(code), code_expires, now_iso(), int(application_id)),
            )
            connection.execute(
                "INSERT INTO email_codes (application_id, email, code_hash, purpose,"
                " expires_at, created_at) VALUES (?, ?, ?, 'trial', ?, ?)",
                (int(application_id), email, self._hash_code(code), code_expires, now_iso()),
            )
            self._log(
                connection, "public", email, "trial_resend",
                serial=serial, ip_hash=ip_hash,
                detail={"application_id": int(application_id)},
            )
        try:
            self.mailer.send_code(email, code, "trial")
        except MailerError as exc:
            self._log_failure("trial_mail_failed", serial, ip_hash, str(exc))
            raise ServiceError("验证码发送失败，请稍后重试", 502, "mail_failed") from exc
        return {
            "ok": True,
            "application_id": int(application_id),
            "email": validation.mask_email(email),
            "verification_required": True,
            "expires_in": CODE_TTL_SECONDS,
            "resend_after": CODE_RESEND_HINT_SECONDS,
        }

    def _issue_trial_locked(self, connection: sqlite3.Connection, application: dict) -> dict:
        serial = application["serial"]
        machine = application["machine"]
        existing = self._find_license(connection, serial)
        if existing is not None:
            if existing["machine"] != machine:
                raise ServiceError("该序列号已绑定其他设备", 409, "serial_bound")
            current = self._row_to_license(existing)
            assert current is not None
            if existing["source"] == "trial":
                if current["effective_status"] == "active":
                    return {**self._issued_response(existing), "already": True}
                if current["effective_status"] == "revoked":
                    raise ServiceError("该设备的试用已被吊销，请联系管理员", 403, "trial_revoked")
                raise ServiceError("试用已结束，请购买专业版或企业版", 409, "trial_expired")
            raise ServiceError("本机已有正式授权，无需申请试用", 409, "already_licensed")
        if self._machine_has_trial(connection, machine, serial):
            raise ServiceError("本机已申请过试用", 409, "machine_trial_exists")
        customer = self.db.query_one(
            "SELECT * FROM customers WHERE id = ?",
            (application["customer_id"],), connection=connection,
        )
        if customer is None:
            raise ServiceError("客户记录不存在，请重新提交申请表", 409)
        if self._customer_has_trial(
            connection, int(customer["id"]),
            exclude_application_id=int(application["id"]),
        ):
            raise ServiceError("该邮箱或手机号已申请过试用", 409, "customer_trial_exists")

        trial_code = validation.generate_trial_code()
        license_id = self._license_id_for("trial", serial, None)
        expires = today() + timedelta(days=self.config.trial_days)
        document = self._build_document(
            serial=serial,
            machine=machine,
            license_id=license_id,
            customer=self._customer_label(customer),
            edition=tool_license.EDITION_TRIAL,
            features=[tool_license.FEATURE_GUI],
            seats=1,
            max_activations=1,
            max_builds=self.config.trial_builds,
            grace_days=self.config.trial_grace_days,
            expires=expires,
            revision=1,
            source="trial",
            notes=f"trial application {application['id']}",
        )
        self._write_license(
            connection, None,
            serial=serial, machine=machine, customer_id=int(customer["id"]),
            license_id=license_id, edition=tool_license.EDITION_TRIAL,
            features=[tool_license.FEATURE_GUI], seats=1, max_activations=1,
            max_builds=self.config.trial_builds, grace_days=self.config.trial_grace_days,
            expires=expires, revision=1, document=document, source="trial",
            key_id=None, display_code=trial_code,
        )
        self._log(
            connection, "system", "", "trial_grant",
            serial=serial, license_id=license_id,
            detail={"expires": expires.isoformat(), "max_builds": self.config.trial_builds},
        )
        row = self._find_license(connection, serial)
        assert row is not None
        return self._issued_response(row)

    def trial_status(self, serial: str, machine: str) -> dict:
        serial = validation.normalize_serial(serial)
        machine = validation.normalize_machine(machine)
        row = self.db.query_one("SELECT * FROM licenses WHERE serial = ?", (serial,))
        if row is None:
            return {"ok": True, "serial": serial, "status": "unknown"}
        if row["machine"] != machine:
            raise ServiceError("机器码与序列号不匹配", 403, "machine_mismatch")
        data = self._row_to_license(row)
        assert data is not None
        status = data["effective_status"]
        mapped = {"active": "granted", "expired": "expired", "revoked": "rejected"}.get(status, status)
        result = {
            "ok": True,
            "serial": serial,
            "status": mapped,
            "edition": data["edition"],
            "expires": data.get("expires"),
            "max_builds": data.get("max_builds_per_month"),
            "revision": data["revision"],
            "license_id": data["license_id"],
            "trial_code": data.get("display_code") or "",
        }
        if status == "revoked":
            result["reason"] = data.get("revoked_reason") or "授权已被吊销"
        return result

    def claim_trial(self, serial: str, machine: str) -> dict:
        serial = validation.normalize_serial(serial)
        machine = validation.normalize_machine(machine)
        row = self.db.query_one("SELECT * FROM licenses WHERE serial = ?", (serial,))
        if row is None:
            raise ServiceError("没有找到该序列号的授权记录", 404, "license_not_found")
        if row["machine"] != machine:
            raise ServiceError("机器码与授权记录不一致", 403, "machine_mismatch")
        data = self._row_to_license(row)
        assert data is not None
        if data["effective_status"] == "revoked":
            raise ServiceError("授权已被吊销", 403, "license_revoked")
        if data["effective_status"] == "expired":
            raise ServiceError("授权已过期", 403, "license_expired")
        return {
            "ok": True,
            "serial": serial,
            "license_id": data["license_id"],
            "edition": data["edition"],
            "revision": data["revision"],
            "expires": data.get("expires"),
            "max_builds": data.get("max_builds_per_month"),
            "trial_code": data.get("display_code") or "",
            "deep_link": self._deep_link(serial, data.get("display_code") or ""),
            "document": data["document"],
        }

    # ------------------------------------------------------------------
    # activation keys
    # ------------------------------------------------------------------

    def activate_key(self, key: str, serial: str, machine: str, remote_ip: str) -> dict:
        normalized_key = validation.normalize_license_key(key)
        serial = validation.normalize_serial(serial)
        machine = validation.normalize_machine(machine)
        ip_hash = self.hash_ip(remote_ip)
        key_hash = self._hash_key(normalized_key)

        self._consume_limits([
            (f"key-ip:{ip_hash}", self.config.rate_limit_per_hour, 3600),
            (f"key-prefix:{normalized_key[:3]}", 100, 3600),
        ])
        if self.db.query_one(
            "SELECT id FROM license_keys WHERE key_hash = ?", (key_hash,),
        ) is None:
            with self.db.transaction() as connection:
                self._log(
                    connection, "client", "desktop", "key_activate_failed",
                    serial=serial, ip_hash=ip_hash,
                    detail={"reason": "unknown key", "prefix": normalized_key[:3]},
                )
            raise ServiceError("激活码无效", 400, "invalid_key")

        with self.db.transaction() as connection:
            key_row = connection.execute(
                "SELECT * FROM license_keys WHERE key_hash = ?", (key_hash,),
            ).fetchone()
            if key_row is None:
                raise ServiceError("激活码无效", 400, "invalid_key")
            key_row = dict(key_row)
            status = self._key_status(key_row)
            if status == "revoked":
                raise ServiceError("激活码已被吊销，请联系管理员", 403, "key_revoked")
            if status == "expired":
                raise ServiceError("激活码已过期，请联系管理员", 403, "key_expired")
            if key_row["edition"] == tool_license.EDITION_TRIAL:
                raise ServiceError("该激活码不能用于本流程", 400, "invalid_edition")

            existing = self._find_license(connection, serial)
            if existing is not None:
                if existing["machine"] != machine:
                    raise ServiceError("该序列号已绑定其他设备", 409, "serial_bound")
                if existing["source"] == "key" and int(existing.get("key_id") or 0) == int(key_row["id"]):
                    current = self._row_to_license(existing)
                    assert current is not None
                    if current["effective_status"] == "revoked":
                        raise ServiceError("该授权已被吊销", 403, "license_revoked")
                    if current["effective_status"] == "expired":
                        raise ServiceError("该授权已过期，请联系管理员续费", 403, "license_expired")
                    return self._issued_response(existing)
                if existing["source"] == "key":
                    current = self._row_to_license(existing)
                    assert current is not None
                    if current["effective_status"] == "active":
                        raise ServiceError("本机已有其他激活码，不能重复激活", 409, "other_key")
                    # An expired or revoked key license may be replaced by a
                    # newly purchased key; the machine stays the same.
                if existing["source"] == "admin":
                    raise ServiceError("本机已有管理员签发的授权", 409, "admin_license")

            activation = connection.execute(
                "SELECT 1 FROM key_activations WHERE key_id = ? AND serial = ?",
                (key_row["id"], serial),
            ).fetchone()
            count_row = connection.execute(
                "SELECT COUNT(*) AS total FROM key_activations WHERE key_id = ?",
                (key_row["id"],),
            ).fetchone()
            activated = int(count_row["total"]) if count_row else 0
            if activation is None and activated >= int(key_row["max_activations"]):
                raise ServiceError("激活码已达到最大激活设备数", 409, "activation_limit")

            edition = str(key_row["edition"])
            features = _json_load(key_row["features"], license_admin.EDITION_FEATURES.get(edition, []))
            license_id = self._license_id_for(edition, serial, existing)
            revision = int(existing["revision"]) + 1 if existing else 1
            expires = today() + timedelta(days=int(key_row["days"]))
            customer_id = existing.get("customer_id") if existing else None
            customer = None
            if customer_id:
                customer = self.db.query_one(
                    "SELECT * FROM customers WHERE id = ?", (customer_id,), connection=connection,
                )
            document = self._build_document(
                serial=serial,
                machine=machine,
                license_id=license_id,
                customer=self._customer_label(customer),
                edition=edition,
                features=list(features),
                seats=int(key_row["seats"]),
                max_activations=int(key_row["max_activations"]),
                max_builds=int(key_row["max_builds_per_month"]),
                grace_days=int(key_row["grace_days"]),
                expires=expires,
                revision=revision,
                source="key",
                notes=str(key_row.get("notes") or ""),
            )
            self._write_license(
                connection, existing,
                serial=serial, machine=machine, customer_id=customer_id,
                license_id=license_id, edition=edition, features=list(features),
                seats=int(key_row["seats"]), max_activations=int(key_row["max_activations"]),
                max_builds=int(key_row["max_builds_per_month"]),
                grace_days=int(key_row["grace_days"]), expires=expires,
                revision=revision, document=document, source="key",
                key_id=int(key_row["id"]), display_code=str(key_row["key_prefix"]),
            )
            if activation is None:
                connection.execute(
                    "INSERT INTO key_activations (key_id, serial, machine, license_id, activated_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (key_row["id"], serial, machine, license_id, now_iso()),
                )
            connection.execute(
                "UPDATE license_keys SET status = 'active' WHERE id = ? AND status = 'unused'",
                (key_row["id"],),
            )
            self._log(
                connection, "client", "desktop", "key_activate",
                serial=serial, license_id=license_id, key_prefix=str(key_row["key_prefix"]),
                ip_hash=ip_hash, detail={"edition": edition, "revision": revision},
            )
            row = self._find_license(connection, serial)
            assert row is not None
            return self._issued_response(row)

    def license_status(
        self,
        serial: str,
        machine: str,
        license_id: str = "",
        revision: int = 0,
        remote_ip: str = "",
    ) -> dict:
        serial = validation.normalize_serial(serial)
        machine = validation.normalize_machine(machine)
        row = self.db.query_one("SELECT * FROM licenses WHERE serial = ?", (serial,))
        if row is None:
            return {"ok": True, "status": "unknown", "serial": serial}
        if row["machine"] != machine:
            raise ServiceError("机器码与授权记录不一致", 403, "machine_mismatch")
        data = self._row_to_license(row)
        assert data is not None
        status = data["effective_status"]
        result = {
            "ok": True,
            "status": status,
            "serial": serial,
            "license_id": data["license_id"],
            "edition": data["edition"],
            "expires": data.get("expires"),
            "revision": data["revision"],
            "max_builds": data.get("max_builds_per_month"),
            "trial_code": data.get("display_code") or "",
            "deep_link": self._deep_link(serial, data.get("display_code") or ""),
        }
        if status == "revoked":
            result["reason"] = data.get("revoked_reason") or "授权已被吊销"
        if status == "expired":
            result["reason"] = "授权已过期"
        try:
            current_revision = int(revision or 0)
        except (TypeError, ValueError):
            current_revision = 0
        if status == "active" and (
            str(license_id or "") != str(data["license_id"]) or current_revision < int(data["revision"])
        ):
            result["document"] = data["document"]
        if remote_ip:
            result["checked"] = now_iso()
        return result

    def revocations_document(self) -> dict:
        rows = self.db.query_all(
            "SELECT license_id FROM licenses WHERE status = 'revoked' ORDER BY license_id",
        )
        payload = {
            "issued": now_iso(),
            "epoch": 1,
            "revoked": [str(row["license_id"]) for row in rows if row.get("license_id")],
            "reason": "license portal revocation list",
        }
        return tool_license.sign_document(
            tool_license.REVOCATION_FORMAT, payload, self.signing_key.seed,
            tool_license.REVOCATION_SIGNATURE_PREFIX,
        )

    # ------------------------------------------------------------------
    # cloud guard: encrypted client verify / preflight / consume
    # ------------------------------------------------------------------

    GUARD_NONCE_TTL_SECONDS = 600
    GUARD_CHECK_KEEP = 5000

    def guard_key_material(self, serial: str) -> dict | None:
        """Row fields needed to derive the shared transport key."""
        serial = validation.normalize_serial(serial)
        return self.db.query_one(
            "SELECT serial, machine, display_code FROM licenses WHERE serial = ?",
            (serial,),
        )

    def _quota_state(self, row: dict) -> tuple[int, int, str]:
        """Return ``(limit, used, period)``; limit 0 means unlimited.

        Trial quota is a total allowance; paid editions reset every calendar
        month. An admin ``builds_limit`` override wins over the edition default.
        """
        override = row.get("builds_limit")
        limit = int(override) if override is not None else int(row.get("max_builds_per_month") or 0)
        period = "total" if row.get("edition") == tool_license.EDITION_TRIAL else today().strftime("%Y-%m")
        used = int(row.get("builds_used") or 0)
        if str(row.get("builds_period") or "") != period:
            used = 0
        return limit, used, period

    @staticmethod
    def _remaining(limit: int, used: int) -> int | None:
        return None if limit <= 0 else max(0, limit - used)

    def _log_check(
        self,
        connection: sqlite3.Connection,
        *,
        serial: str,
        license_id: str,
        kind: str,
        ok: bool,
        code: str = "",
        ip_hash: str = "",
        detail: dict | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO client_checks (at, serial, license_id, kind, ok, code, ip_hash, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now_iso(), serial, license_id, kind, 1 if ok else 0, code, ip_hash,
                _json(detail or {}),
            ),
        )
        connection.execute(
            "DELETE FROM client_checks WHERE id <= (SELECT MAX(id) - ? FROM client_checks)",
            (self.GUARD_CHECK_KEEP,),
        )

    def log_guard_failure(
        self, serial: str, kind: str, code: str, remote_ip: str,
        license_id: str = "", detail: dict | None = None,
    ) -> None:
        with self.db.transaction() as connection:
            self._log_check(
                connection, serial=serial, license_id=license_id, kind=kind,
                ok=False, code=code, ip_hash=self.hash_ip(remote_ip), detail=detail,
            )

    def _guard_common(
        self,
        connection: sqlite3.Connection,
        row: dict,
        data: dict,
        kind: str,
        ip_hash: str,
    ) -> dict:
        """Validate identity, clock and nonce; raise ServiceError on any mismatch."""
        serial = str(row["serial"])
        license_id = str(data.get("license_id") or "")
        machine = validation.normalize_machine(str(data.get("machine") or ""))

        def fail(message: str, status: int, code: str) -> ServiceError:
            # The caller's transaction rolls back on raise; the failure is
            # logged afterwards by log_guard_failure so the audit survives.
            return ServiceError(message, status, code)

        if machine != str(row["machine"]):
            raise fail("机器码与授权记录不一致", 403, "machine_mismatch")
        if str(data.get("serial") or "") != serial:
            raise fail("序列号与请求不一致", 400, "serial_mismatch")
        edition = str(data.get("edition") or "")
        if edition and edition != str(row["edition"]):
            raise fail("版本与授权记录不一致", 409, "edition_mismatch")
        status = self._license_status(row)
        if status == "revoked":
            raise fail(str(row.get("revoked_reason") or "授权已被吊销"), 403, "license_revoked")
        if status == "expired":
            raise fail("授权已过期", 403, "license_expired")

        try:
            client_time = int(data.get("client_time") or 0)
        except (TypeError, ValueError):
            client_time = 0
        if client_time <= 0 or abs(int(time.time()) - client_time) > int(self.config.guard_clock_skew):
            raise fail("客户端时间与服务器偏差过大", 400, "clock_skew")

        nonce = str(data.get("nonce") or "")
        if not (8 <= len(nonce) <= 64) or any(ch not in "0123456789abcdefABCDEF" for ch in nonce):
            raise fail("nonce 无效", 400, "invalid_nonce")
        connection.execute("DELETE FROM guard_nonces WHERE expires_at < ?", (now_iso(),))
        try:
            connection.execute(
                "INSERT INTO guard_nonces (nonce, serial, expires_at) VALUES (?, ?, ?)",
                (nonce.lower(), serial, _iso_offset(self.GUARD_NONCE_TTL_SECONDS)),
            )
        except sqlite3.IntegrityError:
            raise fail("请求已被使用（重放）", 409, "replay_detected") from None

        try:
            revision = int(data.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        return {"license_id": license_id, "revision": revision}

    def _guard_result_base(self, row: dict, nonce: str) -> dict:
        limit, used, period = self._quota_state(row)
        return {
            "ok": True,
            "serial": str(row["serial"]),
            "license_id": str(row["license_id"]),
            "edition": str(row["edition"]),
            "revision": int(row["revision"]),
            "expires": row.get("expires"),
            "quota_limit": limit,
            "quota_used": used,
            "quota_period": period,
            "remaining_builds": self._remaining(limit, used),
            "server_time": now_iso(),
            "client_time": int(time.time()),
            "nonce": nonce,
        }

    def _stale_document(self, row: dict, identity: dict) -> dict | None:
        """Offer a fresh entitlement when the client is behind or renamed."""
        if (
            identity["license_id"] != str(row["license_id"])
            or identity["revision"] < int(row["revision"])
        ):
            return _json_load(row.get("document"), {})
        return None

    def client_verify(self, serial: str, data: dict, remote_ip: str) -> dict:
        serial = validation.normalize_serial(serial)
        ip_hash = self.hash_ip(remote_ip)
        try:
            with self.db.transaction() as connection:
                row = self._find_license(connection, serial)
                if row is None:
                    raise ServiceError("没有找到该序列号的授权记录", 404, "license_not_found")
                identity = self._guard_common(connection, row, data, "verify", ip_hash)
                connection.execute(
                    "UPDATE licenses SET last_seen_at = ?, last_seen_ip = ? WHERE serial = ?",
                    (now_iso(), ip_hash, serial),
                )
                self._log_check(
                    connection, serial=serial, license_id=str(row["license_id"]),
                    kind="verify", ok=True, ip_hash=ip_hash,
                    detail={"revision": identity["revision"]},
                )
                result = self._guard_result_base(row, str(data.get("nonce") or ""))
                result["status"] = "active"
                result["next_check_after"] = 60 + secrets.randbelow(241)
                document = self._stale_document(row, identity)
                if document is not None:
                    result["status"] = "update_required"
                    result["document"] = document
                return result
        except ServiceError as exc:
            self.log_guard_failure(
                serial, "verify", exc.code, remote_ip,
                license_id=str(data.get("license_id") or ""),
            )
            raise

    def client_preflight(self, serial: str, data: dict, remote_ip: str) -> dict:
        serial = validation.normalize_serial(serial)
        ip_hash = self.hash_ip(remote_ip)
        try:
            with self.db.transaction() as connection:
                row = self._find_license(connection, serial)
                if row is None:
                    raise ServiceError("没有找到该序列号的授权记录", 404, "license_not_found")
                identity = self._guard_common(connection, row, data, "preflight", ip_hash)
                self._log_check(
                    connection, serial=serial, license_id=str(row["license_id"]),
                    kind="preflight", ok=True, ip_hash=ip_hash,
                )
                result = self._guard_result_base(row, str(data.get("nonce") or ""))
                result["allowed"] = True
                result["reason"] = ""
                document = self._stale_document(row, identity)
                if document is not None:
                    result["document"] = document
                return result
        except ServiceError as exc:
            self.log_guard_failure(
                serial, "preflight", exc.code, remote_ip,
                license_id=str(data.get("license_id") or ""),
            )
            raise

    def client_consume(self, serial: str, data: dict, remote_ip: str) -> dict:
        serial = validation.normalize_serial(serial)
        ip_hash = self.hash_ip(remote_ip)
        idempotency_key = str(data.get("idempotency_key") or "").strip()
        if not idempotency_key or len(idempotency_key) > 128:
            self.log_guard_failure(serial, "consume", "invalid_idempotency_key", remote_ip)
            raise ServiceError("idempotency_key 无效", 400, "invalid_idempotency_key")
        try:
            with self.db.transaction() as connection:
                row = self._find_license(connection, serial)
                if row is None:
                    raise ServiceError("没有找到该序列号的授权记录", 404, "license_not_found")
                identity = self._guard_common(connection, row, data, "consume", ip_hash)

                previous = connection.execute(
                    "SELECT * FROM build_charges WHERE serial = ? AND idempotency_key = ?",
                    (serial, idempotency_key),
                ).fetchone()
                if previous is not None:
                    result = self._guard_result_base(row, str(data.get("nonce") or ""))
                    result.update({
                        "charged": False,
                        "duplicate": True,
                        "quota_used": int(previous["used_after"]),
                        "remaining_builds": self._remaining(
                            int(previous["quota_limit"]), int(previous["used_after"]),
                        ),
                    })
                    return result

                limit, used, period = self._quota_state(row)
                if limit > 0 and used >= limit:
                    raise ServiceError("构建次数已用完，请联系管理员续费或调整配额", 409, "quota_exhausted")

                used += 1
                connection.execute(
                    "UPDATE licenses SET builds_used = ?, builds_period = ?, last_seen_at = ?,"
                    " last_seen_ip = ? WHERE serial = ?",
                    (used, period, now_iso(), ip_hash, serial),
                )
                connection.execute(
                    "INSERT INTO build_charges (serial, idempotency_key, license_id, used_after,"
                    " quota_limit, quota_period, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        serial, idempotency_key, str(row["license_id"]), used, limit, period,
                        now_iso(),
                    ),
                )
                self._log(
                    connection, "client", "desktop", "build_charge",
                    serial=serial, license_id=str(row["license_id"]), ip_hash=ip_hash,
                    detail={
                        "idempotency_key": idempotency_key, "used": used,
                        "limit": limit, "period": period,
                        "project_hash": str(data.get("project_hash") or "")[:64],
                    },
                )
                self._log_check(
                    connection, serial=serial, license_id=str(row["license_id"]),
                    kind="consume", ok=True, ip_hash=ip_hash,
                    detail={"used": used, "limit": limit, "idempotency_key": idempotency_key},
                )
                result = self._guard_result_base(row, str(data.get("nonce") or ""))
                result.update({
                    "charged": True,
                    "duplicate": False,
                    "quota_used": used,
                    "remaining_builds": self._remaining(limit, used),
                })
                document = self._stale_document(row, identity)
                if document is not None:
                    result["document"] = document
                return result
        except ServiceError as exc:
            self.log_guard_failure(
                serial, "consume", exc.code, remote_ip,
                license_id=str(data.get("license_id") or ""),
            )
            raise

    def list_client_checks(
        self,
        serial: str = "",
        kind: str = "",
        ok: str = "",
        limit: int = 200,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if serial:
            clauses.append("serial LIKE ?")
            params.append(f"%{serial}%")
        if kind in {"verify", "preflight", "consume"}:
            clauses.append("kind = ?")
            params.append(kind)
        if ok in {"0", "1"}:
            clauses.append("ok = ?")
            params.append(int(ok))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(min(max(1, int(limit)), 1000))
        rows = self.db.query_all(
            f"SELECT * FROM client_checks{where} ORDER BY id DESC LIMIT ?", params,
        )
        for row in rows:
            row["detail"] = _json_load(row.get("detail"), {})
        return rows

    def set_build_quota(self, actor: str, serial: str, payload: dict) -> dict:
        serial = validation.normalize_serial(serial)
        reset = bool(payload.get("reset"))
        limit_value = payload.get("limit")
        if limit_value is not None and limit_value != "":
            try:
                limit_value = int(limit_value)
            except (TypeError, ValueError):
                raise ServiceError("limit 必须是整数或 null", 400, "validation_failed") from None
            if limit_value < 0 or limit_value > 1000000:
                raise ServiceError("limit 超出范围", 400, "validation_failed")
        else:
            limit_value = None
        with self.db.transaction() as connection:
            row = self._find_license(connection, serial)
            if row is None:
                raise ServiceError("授权不存在", 404, "license_not_found")
            if reset:
                _, _, period = self._quota_state(row)
                connection.execute(
                    "UPDATE licenses SET builds_used = 0, builds_period = ?, updated_at = ?"
                    " WHERE serial = ?",
                    (period, now_iso(), serial),
                )
            if limit_value is not None or "limit" in payload:
                connection.execute(
                    "UPDATE licenses SET builds_limit = ?, updated_at = ? WHERE serial = ?",
                    (limit_value, now_iso(), serial),
                )
            self._log(
                connection, "admin", actor, "build_quota_adjust",
                serial=serial, license_id=str(row["license_id"]),
                detail={"limit": limit_value, "reset": reset},
            )
            updated = self._find_license(connection, serial)
            assert updated is not None
            return self._row_to_license(updated) or {}

    # ------------------------------------------------------------------
    # upgrade / orders
    # ------------------------------------------------------------------

    def create_upgrade_request(self, payload: dict, remote_ip: str) -> dict:
        data = validation.validate_upgrade_request(payload)
        ip_hash = self.hash_ip(remote_ip)
        self._consume_limits([(f"upgrade-ip:{ip_hash}", 5, 3600)])
        with self.db.transaction() as connection:
            customer_id = None
            if data.get("serial"):
                row = self._find_license(connection, data["serial"])
                if row is not None:
                    customer_id = row.get("customer_id")
            cursor = connection.execute(
                "INSERT INTO orders (customer_id, serial, kind, edition, status, contact,"
                " notes, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    customer_id, data.get("serial") or "", "upgrade", data["edition"],
                    data["contact"], data.get("notes") or "", now_iso(), now_iso(),
                ),
            )
            self._log(
                connection, "public", data["contact"], "upgrade_request",
                serial=data.get("serial") or "", ip_hash=ip_hash,
                detail={"edition": data["edition"], "order_id": cursor.lastrowid},
            )
            return {
                "ok": True,
                "order_id": int(cursor.lastrowid),
                "edition": data["edition"],
                "contact_wechat": self.config.contact_wechat,
            }

    # ------------------------------------------------------------------
    # admin authentication
    # ------------------------------------------------------------------

    def has_admin(self) -> bool:
        row = self.db.query_one("SELECT 1 AS present FROM admins LIMIT 1")
        return row is not None

    def create_admin(
        self,
        username: str,
        password: str,
        role: str = "admin",
        *,
        allow_weak_password: bool = False,
    ) -> int:
        username = str(username or "").strip()
        if not username or len(username) > 64:
            raise ServiceError("用户名无效", 400)
        # Only the deploy-time bootstrap may opt into a legacy short password;
        # admin create/reset keep the normal 10-character policy.
        if not password or (not allow_weak_password and len(password) < 10):
            raise ServiceError("管理员密码至少需要 10 个字符", 400)
        password_hash = security.hash_password(password)
        try:
            with self.db.transaction() as connection:
                cursor = connection.execute(
                    "INSERT INTO admins (username, password_hash, role, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (username, password_hash, role, now_iso()),
                )
                self._log(connection, "system", username, "admin_create",
                          detail={"role": role})
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ServiceError("管理员用户名已存在", 409, "admin_exists") from exc

    def set_admin_password(self, username: str, password: str) -> None:
        if not password or len(password) < 10:
            raise ServiceError("管理员密码至少需要 10 个字符", 400)
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE admins SET password_hash = ?, disabled = 0 WHERE username = ?",
                (security.hash_password(password), str(username or "").strip()),
            )
            if cursor.rowcount == 0:
                raise ServiceError("管理员不存在", 404, "admin_not_found")
            connection.execute("DELETE FROM sessions WHERE admin_id IN"
                               " (SELECT id FROM admins WHERE username = ?)",
                               (str(username or "").strip(),))
            self._log(connection, "system", username, "admin_password_reset")

    def authenticate(self, username: str, password: str, remote_ip: str,
                     user_agent: str = "") -> dict:
        ip_hash = self.hash_ip(remote_ip)
        username = str(username or "").strip()
        self._consume_limits([
            (f"login-ip:{ip_hash}", 20, 900),
            (f"login-user:{self._subject_hash(username)}", 10, 900),
        ])
        row = self.db.query_one("SELECT * FROM admins WHERE username = ?", (username,))
        valid = bool(row) and not row["disabled"] and security.verify_password(
            password, row["password_hash"],
        )
        if not valid:
            # Commit the failure audit; raising inside a transaction would
            # roll the event back along with the attempt.
            with self.db.transaction() as connection:
                self._log(connection, "public", username, "admin_login_failed", ip_hash=ip_hash)
            raise ServiceError("用户名或密码错误", 401, "login_failed")
        token = security.random_token(32)
        csrf = security.random_token(24)
        expires = _iso_offset(self.config.session_hours * 3600)
        failure: ServiceError | None = None
        with self.db.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM admins WHERE id = ?", (row["id"],),
            ).fetchone()
            if current is None or current["disabled"]:
                self._log(connection, "public", username, "admin_login_failed", ip_hash=ip_hash)
                failure = ServiceError("用户名或密码错误", 401, "login_failed")
            else:
                connection.execute(
                    "INSERT INTO sessions (token_hash, admin_id, csrf_token, created_at,"
                    " expires_at, ip_hash, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        security.hash_token(token, self._session_secret), current["id"], csrf,
                        now_iso(), expires, ip_hash, str(user_agent or "")[:300],
                    ),
                )
                connection.execute(
                    "UPDATE admins SET last_login_at = ? WHERE id = ?",
                    (now_iso(), current["id"]),
                )
                self._log(connection, "admin", username, "admin_login", ip_hash=ip_hash)
        if failure is not None:
            raise failure
        return {
            "token": token,
            "csrf": csrf,
            "expires": expires,
            "admin": {"id": row["id"], "username": row["username"], "role": row["role"]},
        }

    def resolve_session(self, token: str) -> dict | None:
        if not token:
            return None
        token_hash = security.hash_token(token, self._session_secret)
        row = self.db.query_one(
            "SELECT s.token_hash, s.csrf_token, s.expires_at, s.admin_id,"
            " a.username, a.role, a.disabled FROM sessions s"
            " JOIN admins a ON a.id = s.admin_id WHERE s.token_hash = ?",
            (token_hash,),
        )
        if row is None or row["disabled"]:
            return None
        if str(row["expires_at"]) < now_iso():
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        return {
            "admin_id": row["admin_id"],
            "username": row["username"],
            "role": row["role"],
            "csrf": row["csrf_token"],
            "expires": row["expires_at"],
            "token_hash": token_hash,
        }

    def delete_session(self, token: str) -> None:
        if token:
            self.db.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (security.hash_token(token, self._session_secret),),
            )

    # ------------------------------------------------------------------
    # admin license keys
    # ------------------------------------------------------------------

    def create_license_keys(self, actor: str, payload: dict) -> dict:
        data = validation.validate_key_issue(
            payload, default_valid_days=self.config.key_redemption_days,
        )
        quantity = int(data["quantity"])
        batch_id = None
        created: list[dict] = []
        with self.db.transaction() as connection:
            if data["batch_name"] or quantity > 1:
                cursor = connection.execute(
                    "INSERT INTO key_batches (name, edition, quantity, created_at,"
                    " created_by, notes) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        data["batch_name"] or f"{data['edition']} x{quantity}",
                        data["edition"], quantity, now_iso(), actor, data["notes"],
                    ),
                )
                batch_id = int(cursor.lastrowid)
            expires_at = (today() + timedelta(days=int(data["redemption_days"]))).isoformat()
            features = _json(data["features"])
            for _ in range(quantity):
                key = validation.generate_license_key(data["edition"])
                prefix = key[:8]
                cursor = connection.execute(
                    "INSERT INTO license_keys (key_prefix, key_hash, key_encrypted, edition,"
                    " days, max_builds_per_month, seats, max_activations, grace_days,"
                    " features, notes, batch_id, status, expires_at, created_at, created_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unused', ?, ?, ?)",
                    (
                        prefix, self._hash_key(key), self._encrypt_key(key), data["edition"],
                        int(data["days"]), int(data["max_builds"]), int(data["seats"]),
                        int(data["max_activations"]), int(data["grace_days"]), features,
                        data["notes"], batch_id, expires_at, now_iso(), actor,
                    ),
                )
                created.append({
                    "id": int(cursor.lastrowid),
                    "key": key,
                    "prefix": prefix,
                    "edition": data["edition"],
                    "days": int(data["days"]),
                    "max_builds": int(data["max_builds"]),
                    "seats": int(data["seats"]),
                    "max_activations": int(data["max_activations"]),
                    "expires_at": expires_at,
                })
            self._log(
                connection, "admin", actor, "key_create",
                key_prefix=created[0]["prefix"] if created else "",
                detail={"quantity": quantity, "edition": data["edition"], "batch_id": batch_id},
            )
        return {"ok": True, "batch_id": batch_id, "keys": created}

    def list_license_keys(self, search: str = "", status: str = "",
                          edition: str = "") -> list[dict]:
        clauses = []
        params: list = []
        if search:
            clauses.append("(k.key_prefix LIKE ? OR k.notes LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])
        if edition:
            clauses.append("k.edition = ?")
            params.append(edition)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.query_all(
            "SELECT k.*, (SELECT COUNT(*) FROM key_activations a WHERE a.key_id = k.id)"
            " AS activated_count FROM license_keys k" + where +
            " ORDER BY k.id DESC LIMIT ?",
            tuple(params) + (MAX_LIST_ROWS,),
        )
        result = [self._row_to_key(row) for row in rows]
        result = [row for row in result if row is not None]
        if status:
            result = [row for row in result if row["effective_status"] == status]
        return result

    def get_license_key(self, key_id: int) -> dict | None:
        row = self.db.query_one(
            "SELECT k.*, (SELECT COUNT(*) FROM key_activations a WHERE a.key_id = k.id)"
            " AS activated_count FROM license_keys k WHERE k.id = ?",
            (int(key_id),),
        )
        key = self._row_to_key(row)
        if key is None:
            return None
        key["activations"] = self.db.query_all(
            "SELECT * FROM key_activations WHERE key_id = ? ORDER BY activated_at",
            (int(key_id),),
        )
        return key

    def reveal_license_key(self, actor: str, key_id: int) -> str:
        row = self.db.query_one(
            "SELECT id, key_prefix, key_encrypted FROM license_keys WHERE id = ?",
            (int(key_id),),
        )
        if row is None:
            raise ServiceError("激活码不存在", 404, "key_not_found")
        try:
            key = self._decrypt_key(str(row["key_encrypted"]))
        except security.SecurityError as exc:
            raise ServiceError(str(exc), 500, "key_decrypt_failed") from exc
        with self.db.transaction() as connection:
            self._log(connection, "admin", actor, "key_reveal",
                      key_prefix=str(row["key_prefix"]), detail={"key_id": int(key_id)})
        return key

    def revoke_license_key(self, actor: str, key_id: int, reason: str = "") -> dict:
        reason = str(reason or "").strip()[:500]
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM license_keys WHERE id = ?", (int(key_id),),
            ).fetchone()
            if row is None:
                raise ServiceError("激活码不存在", 404, "key_not_found")
            connection.execute(
                "UPDATE license_keys SET status = 'revoked', revoked_at = ?,"
                " revoked_reason = ? WHERE id = ?",
                (now_iso(), reason, int(key_id)),
            )
            connection.execute(
                "UPDATE licenses SET status = 'revoked', revoked_at = ?, revoked_reason = ?,"
                " updated_at = ? WHERE key_id = ?",
                (now_iso(), reason or "激活码已吊销", now_iso(), int(key_id)),
            )
            self._log(
                connection, "admin", actor, "key_revoke",
                key_prefix=str(row["key_prefix"]), detail={"reason": reason},
            )
        return {"ok": True, "key_id": int(key_id)}

    # ------------------------------------------------------------------
    # admin licenses
    # ------------------------------------------------------------------

    def list_licenses(self, search: str = "", edition: str = "",
                      status: str = "") -> list[dict]:
        clauses = []
        params: list = []
        if search:
            like = f"%{search}%"
            clauses.append(
                "(l.serial LIKE ? OR l.license_id LIKE ? OR c.email LIKE ?"
                " OR c.phone LIKE ? OR c.name LIKE ? OR c.company_name LIKE ?)"
            )
            params.extend([like] * 6)
        if edition:
            clauses.append("l.edition = ?")
            params.append(edition)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.query_all(
            "SELECT l.*, c.email AS customer_email, c.phone AS customer_phone,"
            " c.name AS customer_name, c.company_name AS customer_company"
            " FROM licenses l LEFT JOIN customers c ON c.id = l.customer_id" + where +
            " ORDER BY l.id DESC LIMIT ?",
            tuple(params) + (MAX_LIST_ROWS,),
        )
        result = []
        for row in rows:
            data = self._row_to_license(row)
            if data is None:
                continue
            if status and data["effective_status"] != status:
                continue
            data["customer_email"] = row.get("customer_email")
            data["customer_phone"] = row.get("customer_phone")
            data["customer_name"] = row.get("customer_name")
            data["customer_company"] = row.get("customer_company")
            result.append(data)
        return result

    def get_license(self, serial: str) -> dict | None:
        serial = validation.normalize_serial(serial)
        row = self.db.query_one(
            "SELECT l.*, c.email AS customer_email, c.phone AS customer_phone,"
            " c.name AS customer_name, c.org_type AS customer_org_type,"
            " c.company_name AS customer_company FROM licenses l"
            " LEFT JOIN customers c ON c.id = l.customer_id WHERE l.serial = ?",
            (serial,),
        )
        data = self._row_to_license(row)
        if data is None:
            return None
        data["customer_email"] = row.get("customer_email")
        data["customer_phone"] = row.get("customer_phone")
        data["customer_name"] = row.get("customer_name")
        data["customer_org_type"] = row.get("customer_org_type")
        data["customer_company"] = row.get("customer_company")
        data["activations"] = self.db.query_all(
            "SELECT * FROM key_activations WHERE serial = ? ORDER BY activated_at", (serial,),
        )
        data["events"] = self.db.query_all(
            "SELECT * FROM events WHERE serial = ? ORDER BY id DESC LIMIT 50", (serial,),
        )
        data["client_checks"] = self.list_client_checks(serial=serial, limit=20)
        return data

    def _renew_row(
        self,
        connection: sqlite3.Connection,
        row: dict,
        *,
        edition: str | None = None,
        expires: date | None = None,
        max_builds: int | None = None,
        grace_days: int | None = None,
        seats: int | None = None,
        notes: str = "",
    ) -> dict:
        data = self._row_to_license(row)
        assert data is not None
        if data["effective_status"] == "revoked":
            raise ServiceError("已吊销的授权不能续费或升级", 409, "license_revoked")
        target_edition = edition or data["edition"]
        features = list(data["features"])
        if edition and edition != data["edition"]:
            features = list(license_admin.EDITION_FEATURES.get(edition, features))
        target_expires = expires
        if target_expires is None:
            current_expiry = _parse_date(data.get("expires"))
            base = current_expiry if current_expiry and current_expiry >= today() else today()
            target_expires = base + timedelta(days=365)
        license_id = self._license_id_for(target_edition, data["serial"], data)
        revision = int(data["revision"]) + 1
        customer = None
        if data.get("customer_id"):
            customer = self.db.query_one(
                "SELECT * FROM customers WHERE id = ?",
                (data["customer_id"],), connection=connection,
            )
        source = data["source"]
        if data["edition"] == tool_license.EDITION_TRIAL and target_edition != tool_license.EDITION_TRIAL:
            source = "admin"
        document_notes = str(
            ((data.get("document") or {}).get("payload") or {}).get("notes") or ""
        )
        target_seats = int(seats if seats is not None else data["seats"])
        target_activations = int(data["max_activations"])
        if seats is not None:
            target_activations = max(target_activations, target_seats)
        document = self._build_document(
            serial=data["serial"],
            machine=data["machine"],
            license_id=license_id,
            customer=self._customer_label(customer),
            edition=target_edition,
            features=features,
            seats=target_seats,
            max_activations=target_activations,
            max_builds=int(max_builds if max_builds is not None else data["max_builds_per_month"]),
            grace_days=int(grace_days if grace_days is not None else data["grace_days"]),
            expires=target_expires,
            revision=revision,
            source=source,
            notes=notes or document_notes,
        )
        self._write_license(
            connection, row,
            serial=data["serial"], machine=data["machine"], customer_id=data.get("customer_id"),
            license_id=license_id, edition=target_edition, features=features,
            seats=target_seats,
            max_activations=target_activations,
            max_builds=int(max_builds if max_builds is not None else data["max_builds_per_month"]),
            grace_days=int(grace_days if grace_days is not None else data["grace_days"]),
            expires=target_expires, revision=revision, document=document,
            source=source, key_id=data.get("key_id"),
            display_code=data.get("display_code") or "",
        )
        updated = self._find_license(connection, data["serial"])
        assert updated is not None
        return self._row_to_license(updated) or {}

    def renew_license(self, actor: str, serial: str, payload: dict) -> dict:
        data = validation.validate_renewal(payload)
        serial = validation.normalize_serial(serial)
        with self.db.transaction() as connection:
            row = self._find_license(connection, serial)
            if row is None:
                raise ServiceError("授权不存在", 404, "license_not_found")
            current = self._row_to_license(row)
            assert current is not None
            expires = data.get("expires")
            if expires is None:
                current_expiry = _parse_date(current.get("expires"))
                base = current_expiry if current_expiry and current_expiry >= today() else today()
                expires = base + timedelta(days=int(data["days"]))
            updated = self._renew_row(
                connection, row,
                expires=expires,
                max_builds=data.get("max_builds"),
                grace_days=data.get("grace_days"),
                notes=data.get("notes") or "",
            )
            self._log(
                connection, "admin", actor, "license_renew",
                serial=serial, license_id=updated["license_id"],
                detail={"expires": updated.get("expires"),
                        "revision": updated["revision"]},
            )
            return {"ok": True, "license": updated}

    def upgrade_license(self, actor: str, serial: str, payload: dict) -> dict:
        data = validation.validate_edition_change(payload)
        serial = validation.normalize_serial(serial)
        with self.db.transaction() as connection:
            row = self._find_license(connection, serial)
            if row is None:
                raise ServiceError("授权不存在", 404, "license_not_found")
            current = self._row_to_license(row)
            assert current is not None
            edition = data["edition"]
            expires = data.get("expires")
            if expires is None:
                current_expiry = _parse_date(current.get("expires"))
                if edition != current["edition"]:
                    base = today()
                else:
                    base = current_expiry if current_expiry and current_expiry >= today() else today()
                expires = base + timedelta(days=int(data["days"]))
            updated = self._renew_row(
                connection, row,
                edition=edition,
                expires=expires,
                max_builds=data.get("max_builds"),
                grace_days=data.get("grace_days"),
                seats=data.get("seats"),
                notes=data.get("notes") or "",
            )
            self._log(
                connection, "admin", actor, "license_upgrade",
                serial=serial, license_id=updated["license_id"],
                detail={"edition": edition, "expires": updated.get("expires"),
                        "revision": updated["revision"]},
            )
            return {"ok": True, "license": updated}

    def revoke_license(self, actor: str, serial: str, reason: str = "") -> dict:
        serial = validation.normalize_serial(serial)
        reason = str(reason or "").strip()[:500]
        with self.db.transaction() as connection:
            row = self._find_license(connection, serial)
            if row is None:
                raise ServiceError("授权不存在", 404, "license_not_found")
            connection.execute(
                "UPDATE licenses SET status = 'revoked', revoked_at = ?, revoked_reason = ?,"
                " updated_at = ? WHERE id = ?",
                (now_iso(), reason or "管理员吊销", now_iso(), int(row["id"])),
            )
            self._log(
                connection, "admin", actor, "license_revoke",
                serial=serial, license_id=row["license_id"], detail={"reason": reason},
            )
        return {"ok": True, "serial": serial}

    # ------------------------------------------------------------------
    # admin dashboards
    # ------------------------------------------------------------------

    def dashboard(self) -> dict:
        counts = {
            "licenses": 0,
            "trials": 0,
            "pro": 0,
            "enterprise": 0,
            "revoked": 0,
            "expired": 0,
            "customers": 0,
            "keys_unused": 0,
            "keys_active": 0,
            "orders_pending": 0,
        }
        row = self.db.query_one("SELECT COUNT(*) AS total FROM licenses")
        counts["licenses"] = int(row["total"]) if row else 0
        for edition in ("trial", "pro", "enterprise"):
            row = self.db.query_one(
                "SELECT COUNT(*) AS total FROM licenses WHERE edition = ? AND status <> 'revoked'",
                (edition,),
            )
            counts["trials" if edition == "trial" else edition] = int(row["total"]) if row else 0
        row = self.db.query_one("SELECT COUNT(*) AS total FROM licenses WHERE status = 'revoked'")
        counts["revoked"] = int(row["total"]) if row else 0
        rows = self.db.query_all("SELECT expires, grace_days, status FROM licenses")
        counts["expired"] = sum(
            1 for item in rows
            if item["status"] != "revoked" and validation.is_expired(
                item["expires"], grace_days=int(item["grace_days"] or 0),
            )
        )
        row = self.db.query_one("SELECT COUNT(*) AS total FROM customers")
        counts["customers"] = int(row["total"]) if row else 0
        for status, key in (("unused", "keys_unused"), ("active", "keys_active")):
            row = self.db.query_one(
                "SELECT COUNT(*) AS total FROM license_keys WHERE status = ?", (status,),
            )
            counts[key] = int(row["total"]) if row else 0
        row = self.db.query_one(
            "SELECT COUNT(*) AS total FROM orders WHERE status = 'pending'",
        )
        counts["orders_pending"] = int(row["total"]) if row else 0

        horizon = (today() + timedelta(days=30)).isoformat()
        expiring_clause = (
            " WHERE l.status <> 'revoked' AND l.expires IS NOT NULL"
            " AND l.expires >= ? AND l.expires <= ?"
        )
        row = self.db.query_one(
            "SELECT COUNT(*) AS total FROM licenses l" + expiring_clause,
            (today().isoformat(), horizon),
        )
        counts["expiring_30d"] = int(row["total"]) if row else 0
        expiring = self.db.query_all(
            "SELECT l.*, c.email AS customer_email, c.company_name AS customer_company"
            " FROM licenses l LEFT JOIN customers c ON c.id = l.customer_id"
            + expiring_clause +
            " ORDER BY l.expires LIMIT 100",
            (today().isoformat(), horizon),
        )
        return {
            "counts": counts,
            "expiring": [self._row_to_license(row) for row in expiring],
            "signing_key": {
                "key_id": self.signing_key.key_id,
                "public_key": self.signing_key.public_b85,
                "fingerprint": self.signing_key.fingerprint,
                "created": self.signing_key.created,
            },
            "email": {
                "verification": self.config.email_verification_enabled,
                "configured": self.config.mailer_is_configured,
                "sender": self.config.smtp_from,
                "from_name": self.config.smtp_from_name,
                "reply_to": self.config.smtp_reply_to,
                "host": self.config.smtp_host,
            },
        }

    def list_customers(self, search: str = "") -> list[dict]:
        clauses = []
        params: list = []
        if search:
            like = f"%{search}%"
            clauses.append("(c.email LIKE ? OR c.phone LIKE ? OR c.name LIKE ? OR c.company_name LIKE ?)")
            params.extend([like] * 4)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.query_all(
            "SELECT c.*, (SELECT COUNT(*) FROM licenses l WHERE l.customer_id = c.id)"
            " AS license_count, (SELECT MAX(l.updated_at) FROM licenses l WHERE l.customer_id = c.id)"
            " AS last_license_at FROM customers c" + where +
            " ORDER BY c.id DESC LIMIT ?",
            tuple(params) + (MAX_LIST_ROWS,),
        )
        return rows

    def get_customer(self, customer_id: int) -> dict | None:
        customer = self.db.query_one(
            "SELECT c.*, (SELECT COUNT(*) FROM licenses l WHERE l.customer_id = c.id)"
            " AS license_count, (SELECT MAX(l.updated_at) FROM licenses l WHERE l.customer_id = c.id)"
            " AS last_license_at FROM customers c WHERE c.id = ?",
            (int(customer_id),),
        )
        if customer is None:
            return None
        licenses = [
            data for row in self.db.query_all(
                "SELECT * FROM licenses WHERE customer_id = ? ORDER BY id DESC LIMIT ?",
                (int(customer_id), MAX_LIST_ROWS),
            )
            if (data := self._row_to_license(row)) is not None
        ]
        orders = self.db.query_all(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY id DESC LIMIT ?",
            (int(customer_id), MAX_LIST_ROWS),
        )
        applications = self.db.query_all(
            "SELECT id, serial, machine, edition, status, code_expires, created_at, updated_at"
            " FROM applications WHERE customer_id = ? ORDER BY id DESC LIMIT ?",
            (int(customer_id), MAX_LIST_ROWS),
        )
        return {
            "customer": customer,
            "licenses": licenses,
            "orders": orders,
            "applications": applications,
        }

    def list_orders(self, status: str = "") -> list[dict]:
        if status:
            rows = self.db.query_all(
                "SELECT o.*, c.email AS customer_email, c.phone AS customer_phone,"
                " c.name AS customer_name, c.company_name AS customer_company"
                " FROM orders o LEFT JOIN customers c ON c.id = o.customer_id"
                " WHERE o.status = ? ORDER BY o.id DESC LIMIT ?",
                (status, MAX_LIST_ROWS),
            )
        else:
            rows = self.db.query_all(
                "SELECT o.*, c.email AS customer_email, c.phone AS customer_phone,"
                " c.name AS customer_name, c.company_name AS customer_company"
                " FROM orders o LEFT JOIN customers c ON c.id = o.customer_id"
                " ORDER BY o.id DESC LIMIT ?",
                (MAX_LIST_ROWS,),
            )
        return rows

    def mark_order(self, actor: str, order_id: int, status: str, amount: str = "") -> dict:
        if status not in {"pending", "paid", "fulfilled", "cancelled"}:
            raise ServiceError("订单状态无效", 400, "invalid_order_status")
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM orders WHERE id = ?", (int(order_id),)).fetchone()
            if row is None:
                raise ServiceError("订单不存在", 404, "order_not_found")
            connection.execute(
                "UPDATE orders SET status = ?, amount = COALESCE(NULLIF(?, ''), amount),"
                " updated_at = ?, handled_by = ? WHERE id = ?",
                (status, str(amount or "")[:80], now_iso(), actor, int(order_id)),
            )
            self._log(
                connection, "admin", actor, "order_update",
                serial=str(row["serial"] or ""), detail={"order_id": int(order_id), "status": status},
            )
        return {"ok": True, "order_id": int(order_id), "status": status}

    def list_events(self, action: str = "", search: str = "", limit: int = 200) -> list[dict]:
        clauses = []
        params: list = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if search:
            like = f"%{search}%"
            clauses.append("(serial LIKE ? OR license_id LIKE ? OR key_prefix LIKE ? OR actor LIKE ?)")
            params.extend([like] * 4)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit = max(1, min(int(limit or 200), 1000))
        return self.db.query_all(
            "SELECT * FROM events" + where + " ORDER BY id DESC LIMIT ?",
            tuple(params) + (limit,),
        )


def create_service(
    config: Config,
    *,
    secret: bytes | None = None,
    mailer: Mailer | None = None,
    captcha: Captcha | None = None,
    signing_key: SigningKey | None = None,
) -> PortalService:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    secret = load_or_create_secret(config.secret_path, secret)
    if signing_key is None:
        store = SigningKeyStore.for_data_dir(config.data_dir, config.signing_passphrase)
        signing_key = store.ensure()
    db = Database(config.db_path)
    service = PortalService(
        db=db,
        config=config,
        signing_key=signing_key,
        secret=secret,
        mailer=mailer,
        captcha=captcha,
    )
    if config.email_verification and not config.mailer_is_configured and not config.dev:
        LOGGER.warning(
            "email verification is enabled but SMTP is not configured; "
            "trial applications will fail until PYSHELD_PORTAL_SMTP_* is set",
        )
    return service
