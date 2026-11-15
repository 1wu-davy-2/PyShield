"""Command-line entry point for the PyShield license portal."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
import tempfile
from pathlib import Path

from pyshield import license_admin

from . import __version__
from .config import Config, ConfigError
from .db import Database
from .security import SecurityError
from .service import PortalService, ServiceError, create_service, load_or_create_secret
from .signing import SigningKeyError, SigningKeyStore
from .server import run as run_server

LOGGER = logging.getLogger("license_portal")


def _password(value: str | None, *, confirm: bool = False) -> str:
    candidate = (value or os.environ.get("PYSHELD_PORTAL_ADMIN_PASSWORD", "")).strip()
    if candidate:
        return candidate
    if not sys.stdin.isatty():
        raise ServiceError(
            "缺少管理员密码；请使用 --password 或 PYSHELD_PORTAL_ADMIN_PASSWORD",
            400,
        )
    first = getpass.getpass("管理员密码（至少 10 个字符）: ")
    if confirm and first != getpass.getpass("再次输入密码: "):
        raise ServiceError("两次输入的密码不一致", 400)
    return first


def _load_config(args) -> Config:
    overrides = {}
    if getattr(args, "host", None):
        overrides["host"] = args.host
    if getattr(args, "port", None) is not None:
        overrides["port"] = args.port
    if getattr(args, "dev", False):
        overrides["dev"] = True
    return Config.load(args.config, overrides=overrides)


def _logging(dev: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if dev else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _cmd_init(args) -> int:
    config = _load_config(args)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    secret = load_or_create_secret(config.secret_path)
    store = SigningKeyStore.for_data_dir(config.data_dir, config.signing_passphrase)
    key = store.ensure()
    Database(config.db_path).close()
    print(f"data dir:      {config.data_dir}")
    print(f"database:      {config.db_path}")
    print(f"signing key:   {config.signing_key_path}")
    print(f"signing key id:{key.key_id}")
    print(f"public key:    {key.public_b85}")
    print(f"fingerprint:   {key.fingerprint}")
    print()
    print("Inject the public key into the desktop build with:")
    print(f"  python tools/build_commercial.py build --public-key <root.pub> --site {config.site_url or 'https://your-domain'}")
    if args.username:
        service = create_service(config, secret=secret, signing_key=key)
        service.create_admin(args.username, _password(args.password, confirm=True))
        print(f"administrator created: {args.username}")
    return 0


def _cmd_run(args) -> int:
    config = _load_config(args)
    _logging(config.dev)
    try:
        run_server(config)
    except (ConfigError, ServiceError, SigningKeyError, SecurityError) as exc:
        print(f"[license-portal] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_admin_create(args) -> int:
    config = _load_config(args)
    _logging(False)
    try:
        service = create_service(config)
        service.create_admin(args.username, _password(args.password, confirm=True), role=args.role)
    except (ServiceError, SigningKeyError, SecurityError) as exc:
        print(f"[license-portal] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"administrator created: {args.username}")
    return 0


def _cmd_admin_reset(args) -> int:
    config = _load_config(args)
    _logging(False)
    try:
        service = create_service(config)
        service.set_admin_password(args.username, _password(args.password, confirm=True))
    except (ServiceError, SigningKeyError, SecurityError) as exc:
        print(f"[license-portal] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"password reset: {args.username}")
    return 0


def _cmd_signing_show(args) -> int:
    config = _load_config(args)
    store = SigningKeyStore.for_data_dir(config.data_dir, config.signing_passphrase)
    try:
        key = store.load()
    except SigningKeyError as exc:
        print(f"[license-portal] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"key id:       {key.key_id}")
    print(f"public key:   {key.public_b85}")
    print(f"fingerprint:  {key.fingerprint}")
    print(f"created:      {key.created}")
    print(f"file:         {store.path}")
    return 0


def _cmd_signing_import(args) -> int:
    config = _load_config(args)
    store = SigningKeyStore.for_data_dir(config.data_dir, config.signing_passphrase)
    if store.path.exists() and not args.force:
        print(
            f"签发密钥已存在：{store.path}；如需替换请加 --force（旧客户端将无法验证新授权）。",
            file=sys.stderr,
        )
        return 2
    try:
        seed = license_admin.load_root_key(args.key)
        key = store.import_seed(seed)
    except (license_admin.LicenseAdminError, SigningKeyError, OSError) as exc:
        print(f"[license-portal] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"imported key id: {key.key_id}")
    print(f"public key:      {key.public_b85}")
    print(f"fingerprint:     {key.fingerprint}")
    return 0


def _cmd_signing_rotate(args) -> int:
    config = _load_config(args)
    if not args.yes:
        print(
            "轮换签发密钥会使旧客户端无法验证新授权；重新构建客户端后才能继续使用。"
            "确认请加 --yes。",
            file=sys.stderr,
        )
        return 2
    store = SigningKeyStore.for_data_dir(config.data_dir, config.signing_passphrase)
    try:
        key = store.rotate()
    except SigningKeyError as exc:
        print(f"[license-portal] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"new key id:   {key.key_id}")
    print(f"new public key:{key.public_b85}")
    print("重新构建并分发客户端后才会生效。")
    return 0


def _cmd_selftest(args) -> int:
    with tempfile.TemporaryDirectory() as temporary:
        config = Config(
            data_dir=Path(temporary), email_verification=False, dev=True,
            secure_cookies=False,
        )
        service = create_service(config)
        assert service.signing_key.public_key
        assert service.dashboard()["counts"]["licenses"] == 0
    print(f"license-portal {__version__} selftest OK")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="license_portal",
        description="PyShield license portal (issuing, validation and admin console).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_config(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", help="JSON configuration file")

    init = subparsers.add_parser("init", help="create data directory, secret, signing key and database")
    add_config(init)
    init.add_argument("--username", help="optionally create an administrator")
    init.add_argument("--password", help="administrator password (or PYSHELD_PORTAL_ADMIN_PASSWORD)")
    init.set_defaults(func=_cmd_init)

    run = subparsers.add_parser("run", help="run the HTTP service")
    add_config(run)
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    run.add_argument("--dev", action="store_true", help="development mode (console mailer, http)")
    run.set_defaults(func=_cmd_run)

    admin = subparsers.add_parser("admin", help="administrator management")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    create = admin_sub.add_parser("create", help="create an administrator")
    add_config(create)
    create.add_argument("--username", required=True)
    create.add_argument("--password")
    create.add_argument("--role", default="admin", choices=["admin", "viewer"])
    create.set_defaults(func=_cmd_admin_create)
    reset = admin_sub.add_parser("reset-password", help="reset an administrator password")
    add_config(reset)
    reset.add_argument("--username", required=True)
    reset.add_argument("--password")
    reset.set_defaults(func=_cmd_admin_reset)

    signing = subparsers.add_parser("signing", help="signing key management")
    signing_sub = signing.add_subparsers(dest="signing_command", required=True)
    show = signing_sub.add_parser("show", help="print the active signing public key")
    add_config(show)
    show.set_defaults(func=_cmd_signing_show)
    import_root = signing_sub.add_parser(
        "import-root", help="import an existing pyshield root.key as the signing key",
    )
    add_config(import_root)
    import_root.add_argument("--key", required=True, help="path to root.key")
    import_root.add_argument("--force", action="store_true", help="replace an existing signing.key")
    import_root.set_defaults(func=_cmd_signing_import)
    rotate = signing_sub.add_parser("rotate", help="generate a new signing key")
    add_config(rotate)
    rotate.add_argument("--yes", action="store_true", help="confirm the rotation")
    rotate.set_defaults(func=_cmd_signing_rotate)

    selftest = subparsers.add_parser("selftest", help="run a quick self check")
    selftest.set_defaults(func=_cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, ServiceError, SigningKeyError, SecurityError, OSError) as exc:
        print(f"[license-portal] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
