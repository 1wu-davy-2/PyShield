"""On-site diagnostics: python -m _shield_runtime status|verify|fingerprint."""

from __future__ import annotations

import argparse
import json
import sys

from . import LicenseError, license_status, verify_package
from . import _machine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m _shield_runtime",
        description="Check a PyShield deployment (license, clock state, package integrity).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show license and clock details")
    status.add_argument("--json", action="store_true", help="print JSON")

    verify = subparsers.add_parser("verify", help="verify the signed package manifest")
    verify.add_argument("--full", action="store_true", help="also hash templates and data files")
    verify.add_argument("--json", action="store_true", help="print JSON")

    fp = subparsers.add_parser(
        "fingerprint", help="print this machine's fingerprint (no license needed)"
    )
    fp.add_argument("--json", action="store_true", help="print JSON")
    return parser


def print_status(report: dict) -> None:
    fingerprints = report.get("fingerprints") or []
    mode = report.get("bind_mode") or "any"
    if mode == "fixed":
        binding = ", ".join(fingerprints) if fingerprints else "fixed (no fingerprints)"
    elif mode == "first-run":
        binding = f"first-run -> {report.get('bound_fingerprint') or '-'}"
    else:
        binding = "any machine"
    lines = [
        f"build id:      {report['build_id']}",
        f"license:       {report['license']}",
        f"not before:    {report.get('not_before') or '-'}",
        f"expires:       {report.get('expires') or 'never'}",
        f"grace days:    {report.get('grace_days', 0)}",
        f"in grace:      {'yes' if report.get('in_grace') else 'no'}",
        f"last seen:     {report.get('last_seen') or '-'}",
        f"customer:      {report.get('customer') or '-'}",
        f"binding:       {binding}",
        f"cipher:        {report.get('cipher')}",
        f"integrity:     {report.get('integrity')}",
    ]
    print("\n".join(lines))


def print_verify(report: dict) -> None:
    if not report.get("enabled"):
        print("package integrity: disabled for this build")
        return
    print(f"package integrity: {'OK' if report['ok'] else 'FAILED'}")
    print(f"files checked:     {report['checked']}")
    for failure in report["failures"]:
        print(f"  - {failure}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "fingerprint":
        value = _machine.machine_fingerprint()
        if args.json:
            print(json.dumps({"fingerprint": value}, indent=2, sort_keys=True))
        else:
            print(value)
        return 0
    if args.command == "status":
        try:
            report = license_status(refresh=True)
        except LicenseError as exc:
            print(f"[pyshield] ERROR: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_status(report)
        return 0

    report = verify_package(full=args.full)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_verify(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
