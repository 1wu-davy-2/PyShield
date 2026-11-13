"""Command line interface for PyShield."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (
    __version__, activation, builder, crypto, fingerprint, licensing, tool_license,
)


def _add_license_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-e", "--expire", help="expiry: number of days from today or YYYY-MM-DD")
    binding = parser.add_mutually_exclusive_group()
    binding.add_argument(
        "-b", "--bind-device", action="append", default=[], metavar="FINGERPRINT",
        help="only run on this machine fingerprint; repeatable",
    )
    binding.add_argument(
        "--bind-first-run", action="store_true",
        help="bind to the first machine that starts the protected program",
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
        "--integrity", choices=["protected", "full", "off"], default="protected",
        help="signed manifest coverage: protected code and runtime, full (also data files), or off",
    )
    build.add_argument(
        "--mutable", action="append", default=[], metavar="PATTERN",
        help="extra fnmatch pattern for files that may change on site; repeatable",
    )
    build.add_argument(
        "--grace-days", type=int, default=0,
        help="days after expiry during which the build still runs",
    )
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
    build.add_argument("--ci", action="store_true", help="mark the build as CI usage (requires the ci feature)")

    issue = subparsers.add_parser("issue", help="reissue a license from a build secret")
    issue.add_argument("--secret", required=True, help="path to the *.shield-secret.json file")
    issue.add_argument("--out", help="license output path (defaults to the path recorded in the secret)")
    _add_license_arguments(issue)
    issue.add_argument(
        "--grace-days", type=int, default=None,
        help="days after expiry during which the build still runs (defaults to the build value)",
    )
    issue.add_argument("--no-restrictions", action="store_true", help="issue an unlimited license")

    verify = subparsers.add_parser("verify", help="verify a protected distribution on site")
    verify.add_argument("-d", "--dist", required=True, help="protected distribution directory")
    verify.add_argument("--secret", help="build secret used to check the manifest signature and license")
    verify.add_argument("--public-key", help="base85 Ed25519 public key instead of a build secret")
    verify.add_argument("--license", help="license file to check (defaults to the one recorded in the secret)")
    verify.add_argument("--full", action="store_true", help="also verify templates and data files")
    verify.add_argument("--skip-license", action="store_true", help="only verify package integrity")
    verify.add_argument("--json", action="store_true", help="print a JSON report")

    fp = subparsers.add_parser("fingerprint", help="print this machine's fingerprint")
    fp.add_argument("--json", action="store_true", help="print JSON instead of plain text")

    inspect = subparsers.add_parser("inspect", help="show the build and license recorded in a secret")
    inspect.add_argument("--secret", required=True, help="path to the *.shield-secret.json file")

    license_parser = subparsers.add_parser("license", help="manage the PyShield tool license")
    license_sub = license_parser.add_subparsers(dest="license_command", required=True)
    license_status = license_sub.add_parser("status", help="show the current tool license state")
    license_status.add_argument("--json", action="store_true")
    license_activate = license_sub.add_parser("activate", help="activate a tool license online")
    license_activate.add_argument("--license", required=True, help="signed tool entitlement file")
    license_activate.add_argument("--server", default=None, help="activation server URL")
    license_activate.add_argument("--machine", default=None, help="override this machine fingerprint")
    license_install = license_sub.add_parser("install", help="install an already machine-bound entitlement")
    license_install.add_argument("--license", required=True)
    license_install.add_argument("--machine", default=None)
    license_request = license_sub.add_parser("request", help="create an offline activation request")
    license_request.add_argument("--license", required=True)
    license_request.add_argument("--out", required=True)
    license_request.add_argument("--machine", default=None)
    license_apply = license_sub.add_parser("apply", help="apply an offline activation response")
    license_apply.add_argument("--response", required=True)
    license_apply.add_argument("--machine", default=None)
    license_sub.add_parser("deactivate", help="remove the installed tool license")
    license_heartbeat = license_sub.add_parser("heartbeat", help="send an online heartbeat")
    license_heartbeat.add_argument("--server", default=None)
    license_heartbeat.add_argument("--used-builds", type=int, default=0)
    license_heartbeat.add_argument("--machine", default=None)
    license_revocations = license_sub.add_parser("revocations", help="install a signed revocation list")
    source = license_revocations.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="signed revocation JSON file")
    source.add_argument("--url", action="store_true", help="fetch the list from the activation server")
    license_revocations.add_argument("--server", default=None)

    subparsers.add_parser("selftest", help="run crypto self tests")
    return parser


def _license_status_text(data: dict) -> str:
    mode = str(data.get("mode") or "unknown")
    labels = {
        "development": "开发模式（未配置根公钥）",
        "licensed": "已授权",
        "trial": "试用",
        "invalid": "无效",
    }
    lines = [f"状态:     {labels.get(mode, mode)}"]
    if data.get("reason"):
        lines.append(f"说明:     {data['reason']}")
    if data.get("license_id"):
        lines.append(f"授权编号: {data['license_id']}")
    if data.get("customer"):
        lines.append(f"客户:     {data['customer']}")
    if data.get("edition"):
        lines.append(f"版本:     {data['edition']}")
    if data.get("expires"):
        lines.append(f"到期:     {data['expires']}（宽限 {data.get('grace_days', 0)} 天）")
    if data.get("features"):
        lines.append(f"功能:     {', '.join(data['features'])}")
    trial = data.get("trial") or {}
    if trial:
        lines.append(f"试用剩余: {trial.get('days_left', '?')} 天 / {trial.get('builds_left', '?')} 次构建")
    quota = data.get("quota") or {}
    if quota:
        lines.append(f"本月构建: {quota.get('used', 0)}/{quota.get('limit', 0)}")
    lines.append(f"机器指纹: {data.get('machine') or '-'}")
    lines.append(f"授权文件: {data.get('license_path') or '-'}")
    lines.append(f"根公钥:   {'已配置' if data.get('public_key_configured') else '未配置'}")
    return "\n".join(lines)


def _run_license_command(args) -> int:
    command = args.license_command
    if command == "status":
        data = tool_license.status_dict()
        if args.json:
            print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(_license_status_text(data))
    elif command == "activate":
        entitlement = activation.activate(
            args.license, server=args.server, machine=args.machine,
        )
        print(f"已激活: {entitlement.license_id}（{entitlement.edition}）")
        print(f"机器:   {', '.join(entitlement.fingerprints)}")
        print(f"到期:   {entitlement.expires.isoformat() if entitlement.expires else '永久'}")
    elif command == "install":
        entitlement = activation.install_license(
            args.license, machine=args.machine,
        )
        print(f"已安装: {entitlement.license_id}（{entitlement.edition}）")
    elif command == "request":
        target = activation.create_offline_request(
            args.license, args.out, machine=args.machine,
        )
        print(f"离线申请已写入: {target}")
    elif command == "apply":
        entitlement = activation.apply_offline_response(
            args.response, machine=args.machine,
        )
        print(f"已应用离线授权: {entitlement.license_id}（{entitlement.edition}）")
    elif command == "deactivate":
        print("已移除本机工具授权。" if activation.deactivate() else "本机没有已安装的工具授权。")
    elif command == "heartbeat":
        entitlement = tool_license.load_entitlement()
        if entitlement is None:
            raise tool_license.ToolLicenseError("no tool license is installed")
        payload = activation.heartbeat(
            entitlement, server=args.server, machine=args.machine,
            used_builds=args.used_builds,
        )
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    elif command == "revocations":
        if args.url:
            count = activation.fetch_revocations(server=args.server)
        else:
            try:
                document = json.loads(Path(args.file).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise tool_license.ToolLicenseError(f"cannot read revocation list: {exc}") from exc
            count = tool_license.install_revocations(document)
        print(f"撤销列表已安装：{count} 条。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            required = [tool_license.FEATURE_NATIVE_RUNTIME] if args.runtime_native else []
            if args.ci:
                required.append(tool_license.FEATURE_CI)
            license_status = tool_license.require(features=required)
            builder.build(builder.BuildOptions(
                project=args.project,
                output=args.output,
                excludes=args.exclude,
                mutable_patterns=args.mutable,
                encrypt_migrations=args.encrypt_migrations,
                encrypt_tests=args.encrypt_tests,
                expire=args.expire,
                grace_days=args.grace_days,
                bind_device=args.bind_device,
                bind_first_run=args.bind_first_run,
                cipher=args.cipher,
                customer=args.customer,
                integrity=args.integrity,
                runtime_pyc=args.runtime_pyc,
                runtime_native=args.runtime_native,
                no_verify=args.no_verify,
                force=args.force,
                dry_run=args.dry_run,
                tool_info=license_status.watermark(),
            ))
            if not args.dry_run:
                tool_license.record_build(license_status)
                builder.update_tool_info(Path(args.output), license_status.watermark())
        elif args.command == "issue":
            tool_license.require(features=[tool_license.FEATURE_LICENSE_ISSUANCE])
            builder.issue(builder.IssueOptions(
                secret=args.secret,
                out=args.out,
                expire=args.expire,
                grace_days=args.grace_days,
                bind_device=args.bind_device,
                bind_first_run=args.bind_first_run,
                customer=args.customer,
                no_restrictions=args.no_restrictions,
            ))
        elif args.command == "inspect":
            builder.inspect(builder.IssueOptions(secret=args.secret))
        elif args.command == "verify":
            report = builder.verify(builder.VerifyOptions(
                dist=args.dist,
                secret=args.secret,
                public_key=args.public_key,
                license=args.license,
                full=args.full,
                skip_license=args.skip_license,
            ))
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(f"distribution: {report['dist']}")
                print(f"build id:     {report['build_id'] or '-'}")
                print(f"integrity:    {report['integrity']}")
                if report["manifest"] is None:
                    print("manifest:     not checked")
                elif not report["manifest"].get("enabled", True):
                    print("manifest:     disabled")
                else:
                    print(f"manifest:     {report['manifest']['files']} files, "
                          f"{report['manifest'].get('integrity')}")
                if report["license"] is None:
                    print("license:      not checked")
                else:
                    print(f"license:      expires {report['license'].get('expires') or 'never'}")
                if report["failures"]:
                    print("failures:")
                    for failure in report["failures"]:
                        print(f"  - {failure}")
                else:
                    print("result:       OK")
            return 0 if report["ok"] else 1
        elif args.command == "license":
            return _run_license_command(args)
        elif args.command == "fingerprint":
            value = fingerprint.machine_fingerprint()
            print(json.dumps({"fingerprint": value}) if args.json else value)
        elif args.command == "selftest":
            print("crypto selftest OK:", crypto.selftest())
    except (
        builder.BuildError, licensing.LicenseError, tool_license.ToolLicenseError,
        activation.ActivationError, crypto.CryptoError, OSError,
    ) as exc:
        print(f"[pyshield] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
