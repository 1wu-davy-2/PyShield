"""Configuration for the license portal.

Values come from (in increasing priority): built-in defaults, an optional JSON
configuration file, ``PYSHELD_PORTAL_*`` environment variables, and explicit
command-line overrides.  Secrets should be supplied through environment
variables or a root-readable configuration file; never commit them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

ENV_PREFIX = "PYSHELD_PORTAL_"

DEFAULT_PORT = 8091
DEFAULT_TRIAL_DAYS = 14
DEFAULT_TRIAL_BUILDS = 3
DEFAULT_SESSION_HOURS = 12
DEFAULT_RATE_LIMIT = 12
DEFAULT_MAX_BODY = 64 * 1024


class ConfigError(Exception):
    """Raised when the portal configuration is invalid."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(ENV_PREFIX + name, default)


def _as_bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value, default: int, name: str, minimum: int | None = None,
            maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if minimum is not None and number < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ConfigError(f"{name} must not exceed {maximum}")
    return number


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("license-data")
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    site_url: str = ""
    site_name: str = "PyShield 授权中心"
    contact_wechat: str = ""
    support_email: str = ""

    trial_days: int = DEFAULT_TRIAL_DAYS
    trial_builds: int = DEFAULT_TRIAL_BUILDS
    trial_grace_days: int = 0
    email_verification: bool = True
    rate_limit_per_hour: int = DEFAULT_RATE_LIMIT

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: str = "starttls"  # starttls | ssl | none

    turnstile_site_key: str = ""
    turnstile_secret: str = ""

    secure_cookies: bool = True
    trust_proxy: bool = False
    session_hours: int = DEFAULT_SESSION_HOURS
    dev: bool = False
    admin_allow_ips: tuple[str, ...] = field(default_factory=tuple)

    signing_passphrase: str = ""
    key_redemption_days: int = 30
    max_body: int = DEFAULT_MAX_BODY
    # Directory with the built React console (index.html + assets).
    # Empty means "license-portal/web/dist next to the package".
    web_dir: str = ""

    def __post_init__(self) -> None:
        # Direct construction is used by tests and embedders; keep the same
        # loopback/development cookie rules as Config.load().
        if not isinstance(self.data_dir, Path):
            object.__setattr__(self, "data_dir", Path(str(self.data_dir)).expanduser())
        if isinstance(self.admin_allow_ips, list):
            object.__setattr__(
                self, "admin_allow_ips",
                tuple(str(item).strip() for item in self.admin_allow_ips if str(item).strip()),
            )
        if self.smtp_from == "" and self.smtp_user:
            object.__setattr__(self, "smtp_from", self.smtp_user)
        local_site = (
            self.site_url.startswith("http://127.")
            or self.site_url.startswith("http://localhost")
            or self.site_url.startswith("http://[::1]")
        )
        if self.dev or local_site:
            object.__setattr__(self, "secure_cookies", False)

    # -- derived paths ----------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.data_dir / "portal.sqlite3"

    @property
    def default_web_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "web" / "dist"

    @property
    def web_dist(self) -> Path:
        """Resolved directory holding the built SPA."""
        if self.web_dir:
            return Path(self.web_dir).expanduser()
        return self.default_web_dir

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "portal.secret"

    @property
    def signing_key_path(self) -> Path:
        return self.data_dir / "signing.key"

    @property
    def signing_pass_path(self) -> Path:
        return self.data_dir / "signing.pass"

    @property
    def email_verification_enabled(self) -> bool:
        return bool(self.email_verification)

    @property
    def captcha_enabled(self) -> bool:
        return bool(self.turnstile_site_key and self.turnstile_secret)

    @property
    def mailer_is_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        overrides: dict | None = None,
    ) -> "Config":
        values: dict = {}

        if path:
            target = Path(path).expanduser()
            try:
                document = json.loads(target.read_text(encoding="utf-8"))
            except OSError as exc:
                raise ConfigError(f"无法读取配置文件 {target}: {exc}") from exc
            except ValueError as exc:
                raise ConfigError(f"配置文件不是合法 JSON: {exc}") from exc
            if not isinstance(document, dict):
                raise ConfigError("配置文件必须是 JSON 对象")
            values.update({key: value for key, value in document.items() if value is not None})

        env_map = {
            "data_dir": "DATA_DIR",
            "host": "HOST",
            "port": "PORT",
            "site_url": "SITE_URL",
            "site_name": "SITE_NAME",
            "contact_wechat": "CONTACT_WECHAT",
            "support_email": "SUPPORT_EMAIL",
            "trial_days": "TRIAL_DAYS",
            "trial_builds": "TRIAL_BUILDS",
            "trial_grace_days": "TRIAL_GRACE_DAYS",
            "email_verification": "EMAIL_VERIFICATION",
            "rate_limit_per_hour": "RATE_LIMIT_PER_HOUR",
            "smtp_host": "SMTP_HOST",
            "smtp_port": "SMTP_PORT",
            "smtp_user": "SMTP_USER",
            "smtp_password": "SMTP_PASSWORD",
            "smtp_from": "SMTP_FROM",
            "smtp_tls": "SMTP_TLS",
            "turnstile_site_key": "TURNSTILE_SITE_KEY",
            "turnstile_secret": "TURNSTILE_SECRET",
            "secure_cookies": "SECURE_COOKIES",
            "trust_proxy": "TRUST_PROXY",
            "session_hours": "SESSION_HOURS",
            "dev": "DEV",
            "admin_allow_ips": "ADMIN_ALLOW_IPS",
            "signing_passphrase": "SIGNING_PASSPHRASE",
            "key_redemption_days": "KEY_REDEMPTION_DAYS",
            "max_body": "MAX_BODY",
            "web_dir": "WEB_DIR",
        }
        for field_name, env_name in env_map.items():
            value = _env(env_name)
            if value != "":
                values[field_name] = value

        if overrides:
            values.update({key: value for key, value in overrides.items() if value is not None})

        if "dev" in values:
            values["dev"] = _as_bool(values["dev"])
        if "email_verification" in values:
            values["email_verification"] = _as_bool(values["email_verification"], True)
        if "secure_cookies" in values:
            values["secure_cookies"] = _as_bool(values["secure_cookies"], True)
        if "trust_proxy" in values:
            values["trust_proxy"] = _as_bool(values["trust_proxy"])

        for name, default, minimum, maximum in (
            ("port", DEFAULT_PORT, 0, 65535),
            ("trial_days", DEFAULT_TRIAL_DAYS, 1, 365),
            ("trial_builds", DEFAULT_TRIAL_BUILDS, 1, 10000),
            ("trial_grace_days", 0, 0, 3650),
            ("rate_limit_per_hour", DEFAULT_RATE_LIMIT, 1, 100000),
            ("smtp_port", 587, 0, 65535),
            ("session_hours", DEFAULT_SESSION_HOURS, 1, 24 * 30),
            ("key_redemption_days", 30, 1, 3650),
            ("max_body", DEFAULT_MAX_BODY, 1024, 16 * 1024 * 1024),
        ):
            if name in values:
                values[name] = _as_int(values[name], default, name, minimum, maximum)

        if "data_dir" in values:
            values["data_dir"] = Path(str(values["data_dir"])).expanduser()
        if "admin_allow_ips" in values:
            raw = values["admin_allow_ips"]
            if isinstance(raw, str):
                values["admin_allow_ips"] = tuple(
                    item.strip() for item in raw.split(",") if item.strip()
                )
            elif isinstance(raw, (list, tuple)):
                values["admin_allow_ips"] = tuple(str(item).strip() for item in raw if str(item).strip())
            else:
                raise ConfigError("admin_allow_ips 必须是逗号分隔的 IP 列表")

        values.setdefault("site_name", "PyShield 授权中心")
        values.setdefault("smtp_tls", "starttls")
        if str(values.get("smtp_tls", "")).lower() not in {"starttls", "ssl", "none"}:
            raise ConfigError("smtp_tls 必须是 starttls、ssl 或 none")

        config = cls(**values)
        if config.smtp_from == "" and config.smtp_user:
            config = replace(config, smtp_from=config.smtp_user)
        local_site = (
            config.site_url.startswith("http://127.")
            or config.site_url.startswith("http://localhost")
            or config.site_url.startswith("http://[::1]")
        )
        if config.dev or local_site:
            # Development and loopback deployments cannot send Secure cookies
            # over plain HTTP; production must use HTTPS in front of the portal.
            config = replace(config, secure_cookies=False)
        return config

    def with_overrides(self, **kwargs) -> "Config":
        return replace(self, **{key: value for key, value in kwargs.items() if value is not None})
