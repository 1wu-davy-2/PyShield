"""SQLite storage for the license portal.

The service is intentionally single-node: one SQLite database, WAL mode, and a
process-wide lock around writes.  It is enough for an early commercial service
and keeps deployment to a single Python process.  All timestamps are UTC ISO
strings; dates used in entitlements are UTC calendar dates.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1


class DatabaseError(Exception):
    """Raised when the database cannot be opened or migrated."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> date:
    return datetime.now(timezone.utc).date()


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    name TEXT NOT NULL,
    org_type TEXT NOT NULL CHECK (org_type IN ('individual', 'company')),
    company_name TEXT,
    consent_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial TEXT NOT NULL,
    machine TEXT NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    edition TEXT NOT NULL DEFAULT 'trial',
    status TEXT NOT NULL,
    code_hash TEXT,
    code_expires TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    ip_hash TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_applications_serial ON applications(serial);
CREATE INDEX IF NOT EXISTS idx_applications_machine ON applications(machine);
CREATE INDEX IF NOT EXISTS idx_applications_customer ON applications(customer_id);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);

CREATE TABLE IF NOT EXISTS key_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    edition TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS license_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    key_encrypted TEXT NOT NULL,
    edition TEXT NOT NULL,
    days INTEGER NOT NULL,
    max_builds_per_month INTEGER NOT NULL,
    seats INTEGER NOT NULL,
    max_activations INTEGER NOT NULL,
    grace_days INTEGER NOT NULL,
    features TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    batch_id INTEGER REFERENCES key_batches(id),
    status TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    revoked_at TEXT,
    revoked_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_license_keys_prefix ON license_keys(key_prefix);
CREATE INDEX IF NOT EXISTS idx_license_keys_status ON license_keys(status);
CREATE INDEX IF NOT EXISTS idx_license_keys_batch ON license_keys(batch_id);

CREATE TABLE IF NOT EXISTS licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial TEXT NOT NULL,
    machine TEXT NOT NULL,
    customer_id INTEGER REFERENCES customers(id),
    license_id TEXT NOT NULL UNIQUE,
    edition TEXT NOT NULL,
    features TEXT NOT NULL,
    seats INTEGER NOT NULL,
    max_activations INTEGER NOT NULL,
    max_builds_per_month INTEGER NOT NULL,
    grace_days INTEGER NOT NULL,
    bind_mode TEXT NOT NULL,
    fingerprints TEXT NOT NULL,
    activation_required INTEGER NOT NULL,
    issued TEXT NOT NULL,
    not_before TEXT NOT NULL,
    expires TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    display_code TEXT NOT NULL DEFAULT '',
    document TEXT NOT NULL,
    source TEXT NOT NULL,
    key_id INTEGER REFERENCES license_keys(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_licenses_serial ON licenses(serial);
CREATE INDEX IF NOT EXISTS idx_licenses_machine ON licenses(machine);
CREATE INDEX IF NOT EXISTS idx_licenses_customer ON licenses(customer_id);
CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses(status);
CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses(key_id);

CREATE TABLE IF NOT EXISTS key_activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id INTEGER NOT NULL REFERENCES license_keys(id),
    serial TEXT NOT NULL,
    machine TEXT NOT NULL,
    license_id TEXT NOT NULL,
    activated_at TEXT NOT NULL,
    UNIQUE(key_id, serial)
);
CREATE INDEX IF NOT EXISTS idx_key_activations_key ON key_activations(key_id);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    serial TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    edition TEXT NOT NULL,
    status TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    amount TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    handled_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_serial ON orders(serial);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    serial TEXT NOT NULL DEFAULT '',
    license_id TEXT NOT NULL DEFAULT '',
    key_prefix TEXT NOT NULL DEFAULT '',
    ip_hash TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
CREATE INDEX IF NOT EXISTS idx_events_action ON events(action);
CREATE INDEX IF NOT EXISTS idx_events_serial ON events(serial);

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES admins(id),
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip_hash TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_admin ON sessions(admin_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS email_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    purpose TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_codes_application ON email_codes(application_id);

CREATE TABLE IF NOT EXISTS rate_limits (
    bucket TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL
);
"""


class Database:
    """Thread-safe wrapper around a single SQLite connection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                timeout=15.0,
                isolation_level=None,
            )
        except sqlite3.Error as exc:
            raise DatabaseError(f"cannot open database {self.path}: {exc}") from exc
        self._connection.row_factory = sqlite3.Row
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA foreign_keys=ON",
            "PRAGMA busy_timeout=15000",
            "PRAGMA synchronous=NORMAL",
        ):
            self._connection.execute(pragma)
        self.initialize()

    def initialize(self) -> None:
        with self._lock:
            version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise DatabaseError(
                    f"database schema {version} is newer than this portal ({SCHEMA_VERSION})"
                )
            if version < SCHEMA_VERSION:
                self._connection.executescript(SCHEMA)
                self._connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._connection.commit()

    @contextlib.contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def execute(self, sql: str, params: tuple | list = (), *,
                connection: sqlite3.Connection | None = None) -> sqlite3.Cursor:
        if connection is not None:
            return connection.execute(sql, params)
        with self._lock:
            cursor = self._connection.execute(sql, params)
            return cursor

    def executemany(self, sql: str, rows, *,
                    connection: sqlite3.Connection | None = None) -> sqlite3.Cursor:
        if connection is not None:
            return connection.executemany(sql, rows)
        with self._lock:
            return self._connection.executemany(sql, rows)

    def query_one(self, sql: str, params: tuple | list = (), *,
                  connection: sqlite3.Connection | None = None) -> dict | None:
        executor = connection if connection is not None else self._connection
        if connection is None:
            with self._lock:
                row = executor.execute(sql, params).fetchone()
        else:
            row = executor.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def query_all(self, sql: str, params: tuple | list = (), *,
                  connection: sqlite3.Connection | None = None) -> list[dict]:
        executor = connection if connection is not None else self._connection
        if connection is None:
            with self._lock:
                rows = executor.execute(sql, params).fetchall()
        else:
            rows = executor.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
