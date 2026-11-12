"""Command line interface for PyShield."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, builder, crypto, fingerprint, licensing


def _add_license_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-e", "--expire", help="expiry: number of days from today or YYYY-MM-DD")
    parser.add_argument(
        "-b", "--bind-device", action="append", default=[], metavar="FINGERPRINT",
        help="only run on this machine fingerprint; repeatable",
    )
    parser.add_argument("--customer", help="optional customer label stored in the license")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyshield", description="Protect Python/Django projects with PyShield.")
    parser.add_argument("--version", action="version", version=f"pyshield {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build a protected distribution")
    build.add_argument("-p", "--project", default="demo", help="project root containing manage.py")
    build.add_argument("-o", "--output", default="dist-pyshield", help="output directory")
    build.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN",
        help="extra fnmatch pattern for files to leave unencrypted; repeatable",
    )
    build.add_argument("--encrypt-migrations", action="store_true", help="encrypt Django migrations too")
    build.add_argument("--encrypt-tests", action="store_true", help="encrypt test modules too")
    build.add_argument(
        "--cipher", choices=["auto", "chacha", "chacha-pure", "aesgcm"], default="auto",
        help="module cipher; auto uses aesgcm when cryptography is installed, "
             "chacha uses ChaCha20-Poly1305 when available, chacha-pure is dependency-free",
    )
    _add_license_arguments(build)
    runtime = build.add_mutually_exclusive_group()
    runtime.add_argument("--runtime-pyc", action="store_true", help="ship the runtime as sourceless bytecode")
    runtime.add_argument(
        "--runtime-native", action="store_true",
        help="compile the runtime with Cython (requires Cython, setuptools and a C compiler)",
    )
    build.add_argument("--no-verify", action="store_true", help="skip manage.py check on the output")
    build.add_argument("--force", action="store_true", help="replace an existing, non-PyShield output directory")
    build.add_argument("--dry-run", action="store_true", help="print the plan without writing files")

    issue = subparsers.add_parser("issue", help="reissue a license from a build secret")
    issue.add_argument("--secret", required=True, help="path to the *.shield-secret.json file")
    issue.add_argument("--out", help="license output path (defaults to the path recorded in the secret)")
    _add_license_arguments(issue)
    issue.add_argument("--no-restrictions", action="store_true", help="issue an unlimited license")

    fp = subparsers.add_parser("fingerprint", help="print this machine's fingerprint")
    fp.add_argument("--json", action="store_true", help="print JSON instead of plain text")

    inspect = subparsers.add_parser("inspect", help="show the build and license recorded in a secret")
    inspect.add_argument("--secret", required=True, help="path to the *.shield-secret.json file")

    subparsers.add_parser("selftest", help="run crypto self tests")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            builder.build(builder.BuildOptions(
                project=args.project,
                output=args.output,
                excludes=args.exclude,
                encrypt_migrations=args.encrypt_migrations,
                encrypt_tests=args.encrypt_tests,
                expire=args.expire,
                bind_device=args.bind_device,
                cipher=args.cipher,
                customer=args.customer,
                runtime_pyc=args.runtime_pyc,
                runtime_native=args.runtime_native,
                no_verify=args.no_verify,
                force=args.force,
                dry_run=args.dry_run,
            ))
        elif args.command == "issue":
            builder.issue(builder.IssueOptions(
                secret=args.secret,
                out=args.out,
                expire=args.expire,
                bind_device=args.bind_device,
                customer=args.customer,
                no_restrictions=args.no_restrictions,
            ))
        elif args.command == "inspect":
            builder.inspect(builder.IssueOptions(secret=args.secret))
        elif args.command == "fingerprint":
            value = fingerprint.machine_fingerprint()
            print(json.dumps({"fingerprint": value}) if args.json else value)
        elif args.command == "selftest":
            print("crypto selftest OK:", crypto.selftest())
    except (builder.BuildError, licensing.LicenseError, crypto.CryptoError, OSError) as exc:
        print(f"[pyshield] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
