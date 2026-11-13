"""Build a PyShield-protected distribution of a Python/Django project."""

from __future__ import annotations

import fnmatch
import importlib.machinery
import json
import marshal
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import __version__, crypto, ed25519, fingerprint, licensing, manifest

RUNTIME_PACKAGE = "_shield_runtime"
MARKER_FILE = ".pyshield-build.json"
MANIFEST_FILE = manifest.MANIFEST_FILENAME
LICENSE_FILE = licensing.LICENSE_FILENAME
PAYLOAD_CHUNK = 1000
NATIVE_MODULES = ("_core", "_crypto", "_ed25519", "_chacha_fast")
INTEGRITY_MODES = ("protected", "full", "off")
NATIVE_SETUP = """\
import os

from setuptools import Extension, setup
from Cython.Build import cythonize

PACKAGE = "_shield_runtime"
MODULES = ("_core", "_crypto", "_ed25519", "_chacha_fast")
BASE = os.path.join("src", PACKAGE)
SOURCES = []
for name in MODULES:
    pyx = os.path.join(BASE, name + ".pyx")
    py = os.path.join(BASE, name + ".py")
    if os.path.exists(pyx):
        SOURCES.append((name, pyx))
    elif os.path.exists(py):
        SOURCES.append((name, py))

EXTENSIONS = [Extension(PACKAGE + "." + name, [path]) for name, path in SOURCES]
setup(
    name="pyshield-runtime-native",
    packages=[PACKAGE],
    package_dir={"": "src"},
    ext_modules=cythonize(
        EXTENSIONS,
        language_level=3,
        compiler_directives={
            "language_level": 3,
            "boundscheck": False,
            "wraparound": False,
            "initializedcheck": False,
        },
    ),
)
"""

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "staticfiles", RUNTIME_PACKAGE,
}

IGNORED_SUFFIXES = {".pyc", ".pyo", ".log", ".sqlite3"}


class BuildError(Exception):
    """Raised when a build cannot be completed."""


class BuildCancelled(BuildError):
    """Raised when the caller cancels a running build."""


@dataclass
class Module:
    path: Path
    relative: Path
    module_id: str


@dataclass
class BuildOptions:
    project: str = "demo"
    output: str = "dist-pyshield"
    excludes: list[str] = field(default_factory=list)
    mutable_patterns: list[str] = field(default_factory=list)
    encrypt_migrations: bool = False
    encrypt_tests: bool = False
    expire: str | None = None
    grace_days: int = 0
    bind_device: list[str] = field(default_factory=list)
    bind_first_run: bool = False
    cipher: str = "auto"
    customer: str | None = None
    integrity: str = "protected"
    runtime_pyc: bool = False
    runtime_native: bool = False
    no_verify: bool = False
    force: bool = False
    dry_run: bool = False
    verify_python: str | None = None
    cancel: threading.Event | None = field(default=None, repr=False, compare=False)
    progress: Callable[[str, int], None] | None = field(default=None, repr=False, compare=False)
    log_sink: Callable[[str], None] | None = field(default=None, repr=False, compare=False)


@dataclass
class BuildResult:
    output: Path
    secret: Path
    license: Path
    build_id: str
    modules: int
    cipher: str
    elapsed: float = 0.0
    output_size: int = 0


def log(message: str) -> None:
    if sys.stdout is not None:
        print(f"[pyshield] {message}", flush=True)


def report_log(options: BuildOptions, message: str) -> None:
    log(message)
    if options.log_sink is not None:
        try:
            options.log_sink(message)
        except Exception:
            pass


def report_stage(options: BuildOptions, stage: str, message: str, percent: int) -> None:
    report_log(options, message)
    report_progress(options, stage, percent)


def report_progress(options: BuildOptions, stage: str, percent: int) -> None:
    if options.progress is not None:
        try:
            options.progress(stage, percent)
        except Exception:
            pass


def check_cancel(options: BuildOptions) -> None:
    if options.cancel is not None and options.cancel.is_set():
        raise BuildCancelled("build cancelled")



def path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = set()
    base = Path(directory)
    for name in names:
        path = base / name
        if path.is_symlink():
            ignored.add(name)
        elif name in IGNORED_DIRS:
            ignored.add(name)
        elif path.is_dir() and name.startswith("."):
            ignored.add(name)
        elif path.suffix in IGNORED_SUFFIXES or path.suffix == ".pyc":
            ignored.add(name)
        elif name in {"db.sqlite3", LICENSE_FILE, MARKER_FILE, MANIFEST_FILE}:
            ignored.add(name)
        elif name.endswith(licensing.SECRET_SUFFIX):
            ignored.add(name)
    return ignored


def default_excludes(encrypt_migrations: bool = False, encrypt_tests: bool = False) -> list[str]:
    # Site-local configuration stays plaintext so it can be edited after deployment.
    patterns: list[str] = [
        "local_settings.py",
        "*/local_settings.py",
        "settings_local.py",
        "*/settings_local.py",
    ]
    if not encrypt_migrations:
        patterns += ["migrations", "*/migrations/*", "*/migrations.py"]
    if not encrypt_tests:
        patterns += ["tests", "tests/*", "*/tests/*", "tests.py", "*/tests.py", "test_*.py", "*/test_*.py"]
    return patterns


def module_id_for(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def is_excluded(relative: Path, module_id: str, patterns: list[str]) -> bool:
    text = relative.as_posix()
    return any(
        fnmatch.fnmatchcase(text, pattern) or fnmatch.fnmatchcase(module_id, pattern)
        for pattern in patterns
    )


def find_packages(project: Path) -> list[Path]:
    packages = []
    for entry in sorted(project.iterdir(), key=lambda item: item.name):
        if not entry.is_dir() or entry.is_symlink() or entry.name in IGNORED_DIRS:
            continue
        if entry.name.startswith("."):
            continue
        if (entry / "__init__.py").is_file():
            packages.append(entry)
    return packages


def discover_modules(project: Path, patterns: list[str]) -> list[Module]:
    modules: dict[str, Module] = {}

    def add(path: Path) -> None:
        relative = path.relative_to(project)
        module_id = module_id_for(relative)
        if not module_id or is_excluded(relative, module_id, patterns):
            return
        if module_id in modules:
            raise BuildError(f"duplicate module id {module_id!r} for {relative}")
        modules[module_id] = Module(path=path, relative=relative, module_id=module_id)

    manage = project / "manage.py"
    if not manage.is_file():
        raise BuildError(f"manage.py not found in {project}")
    add(manage)

    for package in find_packages(project):
        for path in sorted(package.rglob("*.py")):
            if not path.is_file() or path.is_symlink():
                continue
            if any(part in IGNORED_DIRS or part.startswith(".") for part in path.relative_to(project).parts[:-1]):
                continue
            add(path)

    return [modules[key] for key in sorted(modules)]


def make_stub(payload: str, module_id: str) -> str:
    chunks = [payload[index:index + PAYLOAD_CHUNK] for index in range(0, len(payload), PAYLOAD_CHUNK)]
    lines = [
        "# PyShield protected module. Generated by pyshield; do not edit.",
        "",
        "from _shield_runtime import load_module as _pyshield_load_module",
        "",
        "_pyshield_load_module(",
        "    __name__,",
        "    __file__,",
        "    (",
    ]
    lines.extend(f'        "{chunk}"' for chunk in chunks)
    lines.extend([
        "    ),",
        f"    {module_id!r},",
        ")",
    ])
    return "\n".join(lines) + "\n"


def protect_module(module: Module, build_key: bytes, build_id: str, cipher: str) -> str:
    source = module.path.read_bytes()
    filename = module.relative.as_posix()
    try:
        code = compile(source, filename, "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise BuildError(f"cannot compile {module.relative}: {exc}") from exc
    try:
        raw = zlib.compress(marshal.dumps(code), 9)
    except (ValueError, TypeError) as exc:
        raise BuildError(f"cannot serialize {module.relative}: {exc}") from exc
    key = crypto.derive_module_key(build_key, module.module_id)
    sealed = crypto.seal(key, raw, crypto.module_aad(build_id, module.module_id), cipher=cipher)
    return crypto.b85encode(sealed)


def install_runtime(
    output: Path,
    build_id: str,
    master_key: bytes,
    signing_public_key: bytes,
    cipher: str,
    integrity: str,
    runtime_pyc: bool,
    runtime_native: bool,
) -> None:
    source_dir = Path(__file__).resolve().parent
    template = source_dir / "runtime_template" / RUNTIME_PACKAGE
    runtime = output / RUNTIME_PACKAGE
    shutil.copytree(template, runtime)
    shutil.copy2(source_dir / "crypto.py", runtime / "_crypto.py")
    shutil.copy2(source_dir / "fingerprint.py", runtime / "_machine.py")
    shutil.copy2(source_dir / "ed25519.py", runtime / "_ed25519.py")

    core = runtime / "_core.py"
    text = core.read_text(encoding="utf-8")
    replacements = {
        "__PYSHELD_BUILD_ID__": build_id,
        "__PYSHELD_MASTER_KEY__": crypto.b85encode(master_key),
        "__PYSHELD_SIGNING_PUBLIC_KEY__": crypto.b85encode(signing_public_key),
        "__PYSHELD_PYTHON_VERSION__": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "__PYSHELD_CIPHER__": cipher,
        "__PYSHELD_INTEGRITY__": integrity,
    }
    for placeholder, value in replacements.items():
        if placeholder not in text:
            raise BuildError(f"runtime template is missing placeholder {placeholder}")
        text = text.replace(placeholder, value)
    if "__PYSHELD_" in text:
        raise BuildError("runtime template contains unresolved placeholders")
    core.write_text(text, encoding="utf-8", newline="\n")

    if runtime_native:
        compile_runtime_native(runtime)
    elif runtime_pyc:
        compile_runtime(runtime)
    (runtime / "_chacha_fast.pyx").unlink(missing_ok=True)


def find_mingw() -> Path | None:
    candidates: list[Path] = []
    prefix = os.environ.get("MINGW_PREFIX")
    if prefix:
        candidates.append(Path(prefix) / "bin")
    if os.name == "nt":
        candidates.extend([
            Path("C:/mingw64/bin"),
            Path("C:/msys64/mingw64/bin"),
            Path("C:/ProgramData/mingw64/mingw64/bin"),
        ])
    for candidate in candidates:
        if (candidate / "gcc.exe").is_file():
            return candidate
    compiler = shutil.which("gcc") or shutil.which("cc")
    return Path(compiler).parent if compiler else None


def compile_runtime_native(runtime: Path) -> None:
    try:
        import Cython  # noqa: F401
        import setuptools  # noqa: F401
    except ImportError as exc:
        raise BuildError(
            "--runtime-native requires Cython and setuptools; "
            "install them with: pip install cython setuptools"
        ) from exc

    build_dir = runtime.parent / ".pyshield-native"
    shutil.rmtree(build_dir, ignore_errors=True)
    try:
        package_source = build_dir / "src" / RUNTIME_PACKAGE
        shutil.copytree(runtime, package_source)
        (build_dir / "setup.py").write_text(NATIVE_SETUP, encoding="utf-8", newline="\n")

        command = [sys.executable, "setup.py", "build_ext"]
        env = os.environ.copy()
        mingw = find_mingw() if os.name == "nt" else None
        if mingw is not None:
            env["PATH"] = str(mingw) + os.pathsep + env.get("PATH", "")
            command.append("--compiler=mingw32")

        log("compiling native runtime with Cython")
        completed = subprocess.run(
            command, cwd=build_dir, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            details = (completed.stdout + completed.stderr).strip()
            raise BuildError("cannot compile native runtime:\n" + details[-4000:])

        library_root = next(
            (path for path in build_dir.glob("build/lib*") if path.is_dir()), None
        )
        if library_root is None:
            raise BuildError("native runtime build produced no library directory")

        for name in NATIVE_MODULES:
            for suffix in importlib.machinery.EXTENSION_SUFFIXES:
                candidate = library_root / RUNTIME_PACKAGE / (name + suffix)
                if candidate.is_file():
                    shutil.copy2(candidate, runtime / candidate.name)
                    break
            else:
                raise BuildError(f"native runtime did not produce {name} extension")

        for name in NATIVE_MODULES:
            (runtime / (name + ".py")).unlink(missing_ok=True)
            (runtime / (name + ".pyx")).unlink(missing_ok=True)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def compile_runtime(runtime: Path) -> None:
    for source in sorted(runtime.glob("*.py")):
        target = source.with_suffix(".pyc")
        try:
            py_compile.compile(
                str(source), cfile=str(target), dfile=str(source), doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
            )
        except py_compile.PyCompileError as exc:
            raise BuildError(f"cannot compile runtime file {source.name}: {exc}") from exc
        if source.name != "__init__.py":
            source.unlink()
    init_source = runtime / "__init__.py"
    if init_source.exists():
        init_source.unlink()


def write_marker(output: Path, build_id: str, cipher: str, integrity: str, module_count: int, runtime_mode: str) -> None:
    marker = {
        "format": 1,
        "tool": f"pyshield {__version__}",
        "build_id": build_id,
        "cipher": cipher,
        "integrity": integrity,
        "runtime": runtime_mode,
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "modules": module_count,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (output / MARKER_FILE).write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def can_remove_output(output: Path) -> bool:
    if not output.exists():
        return True
    if not output.is_dir():
        return False
    try:
        if not any(output.iterdir()):
            return True
    except OSError:
        return False
    return (output / MARKER_FILE).is_file()


def verify_build(
    output: Path,
    license_path: Path,
    expire_date: date | None,
    fingerprints: list[str],
    python_executable: str | None = None,
    bind_mode: str = licensing.BIND_ANY,
) -> None:
    if expire_date is not None and expire_date < date.today():
        log("skipping verification: the build is already expired")
        return
    if bind_mode == licensing.BIND_FIXED and fingerprints:
        if fingerprint.machine_fingerprint() not in fingerprints:
            log("skipping verification: the build is bound to another machine")
            return
    executable = python_executable
    if not executable:
        if getattr(sys, "frozen", False):
            log("skipping verification: no external Python interpreter was selected")
            return
        executable = sys.executable
    env = os.environ.copy()
    env["PYSHELD_LICENSE"] = str(license_path)

    temp_binding: Path | None = None
    if bind_mode == licensing.BIND_FIRST_RUN:
        # Never consume the customer's first-run binding on the build machine.
        handle = tempfile.NamedTemporaryFile(
            prefix="pyshield-verify-", suffix=".binding", delete=False,
        )
        temp_binding = Path(handle.name)
        handle.close()
        temp_binding.unlink(missing_ok=True)
        env[licensing.BINDING_FILE_ENV] = str(temp_binding)

    log(f"running manage.py check with {executable}")
    try:
        result = subprocess.run(
            [executable, str(output / "manage.py"), "check"],
            cwd=output, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    finally:
        if temp_binding is not None:
            temp_binding.unlink(missing_ok=True)
    if result.returncode != 0:
        details = (result.stdout + result.stderr).strip()
        raise BuildError("protected project failed manage.py check:\n" + details)


def directory_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def clean_pycache(root: Path) -> None:
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def build(options: BuildOptions) -> BuildResult:
    started = time.monotonic()
    project = Path(options.project).resolve()
    output = Path(options.output).resolve()

    report_stage(options, "init", "validating build options", 2)
    if not (project / "manage.py").is_file():
        raise BuildError(f"manage.py not found in {project}")
    if options.runtime_native and options.runtime_pyc:
        raise BuildError("--runtime-native and --runtime-pyc are mutually exclusive")
    if options.integrity not in INTEGRITY_MODES:
        raise BuildError(f"integrity must be one of: {', '.join(INTEGRITY_MODES)}")
    try:
        grace_days = licensing.parse_grace_days(options.grace_days)
    except licensing.LicenseError as exc:
        raise BuildError(str(exc)) from exc
    runtime_mode = "native" if options.runtime_native else "pyc" if options.runtime_pyc else "source"
    if output == project or path_is_within(project, output):
        raise BuildError("output directory must not be the project directory or one of its parents")
    if path_is_within(output, project):
        raise BuildError("output directory must be outside the project directory")

    patterns = default_excludes(options.encrypt_migrations, options.encrypt_tests) + list(options.excludes)
    modules = discover_modules(project, patterns)
    if not modules:
        raise BuildError(f"no Python modules found in {project}")

    try:
        cipher = crypto.resolve_cipher(options.cipher)
    except ValueError as exc:
        raise BuildError(str(exc)) from exc

    try:
        expire_date = licensing.parse_expire(options.expire)
    except licensing.LicenseError as exc:
        raise BuildError(str(exc)) from exc
    fingerprints = licensing.normalize_fingerprints(options.bind_device)
    if options.bind_first_run and fingerprints:
        raise BuildError("--bind-first-run and --bind-device are mutually exclusive")
    if options.bind_first_run:
        bind_mode = licensing.BIND_FIRST_RUN
    elif fingerprints:
        bind_mode = licensing.BIND_FIXED
    else:
        bind_mode = licensing.BIND_ANY

    local = fingerprint.machine_fingerprint()
    report_log(options, f"pyshield {__version__} on CPython {sys.version_info[0]}.{sys.version_info[1]}")
    report_log(options, f"project:  {project}")
    report_log(options, f"output:   {output}")
    report_log(options, f"cipher:   {cipher}")
    report_log(options, f"runtime:  {runtime_mode}")
    report_log(options, f"integrity: {options.integrity}")
    if options.integrity == "full":
        report_log(options, "note: full integrity verifies templates and static data on every start")
    if cipher in {"aesgcm", "chacha20poly1305"}:
        report_log(options, f"note: {cipher} builds require the 'cryptography' package on the deployment machine")
    report_log(options, f"expire:   {expire_date.isoformat() if expire_date else 'never'}")
    if grace_days:
        report_log(options, f"grace:    {grace_days} days after expiry")
    if bind_mode == licensing.BIND_FIRST_RUN:
        report_log(options, "bound to: first machine at first launch")
    else:
        report_log(options, f"bound to: {', '.join(fingerprints) if fingerprints else 'any machine'}")
        if options.bind_device and local not in fingerprints:
            report_log(options, f"note: this machine's fingerprint is {local}")
    report_log(options, f"modules:  {len(modules)}")
    if options.dry_run:
        for module in modules:
            report_log(options, f"  would protect {module.relative.as_posix()} as {module.module_id}")
        return BuildResult(
            output, licensing.secret_path_for(output), output / LICENSE_FILE,
            "-", len(modules), cipher, time.monotonic() - started,
        )

    if not can_remove_output(output) and not options.force:
        raise BuildError(
            f"{output} exists and is not a pyshield build; "
            "pass --force to replace it"
        )

    build_id = os.urandom(8).hex()
    build_key = crypto.random_bytes(32)
    master_key = crypto.random_bytes(32)
    signing_key = crypto.random_bytes(32)
    signing_public_key = ed25519.public_key(signing_key)
    temp = output.parent / f".{output.name}.tmp"

    if temp.exists():
        shutil.rmtree(temp, ignore_errors=True)

    try:
        check_cancel(options)
        report_stage(options, "copy", "copying project data files", 10)
        shutil.copytree(project, temp, ignore=copy_ignore)
        check_cancel(options)

        total = len(modules)
        report_stage(options, "encrypt", f"encrypting {total} Python modules", 15)
        for index, module in enumerate(modules, 1):
            check_cancel(options)
            payload = protect_module(module, build_key, build_id, cipher)
            stub = make_stub(payload, module.module_id)
            target = temp / module.relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(stub, encoding="utf-8", newline="\n")
            report_log(options, f"  protected {module.relative.as_posix()}")
            report_progress(options, "encrypt", 15 + int(45 * index / total))

        check_cancel(options)
        report_stage(options, "runtime", "installing runtime", 66)
        install_runtime(
            temp, build_id, master_key, signing_public_key, cipher,
            options.integrity, options.runtime_pyc, options.runtime_native,
        )

        python_version = f"{sys.version_info[0]}.{sys.version_info[1]}"
        report_stage(options, "license", "writing license", 72)
        try:
            sealed = licensing.create_license(
                master_key, build_id, build_key, cipher, python_version, signing_key,
                expires=expire_date, fingerprints=fingerprints, customer=options.customer,
                grace_days=grace_days, bind_mode=bind_mode,
            )
        except licensing.LicenseError as exc:
            raise BuildError(str(exc)) from exc
        licensing.write_license(temp / LICENSE_FILE, sealed)
        write_marker(
            temp, build_id, cipher, options.integrity, len(modules), runtime_mode
        )

        if options.integrity != "off":
            report_stage(options, "manifest", "writing signed package manifest", 78)
            payload = manifest.build_payload(
                temp,
                build_id,
                cipher,
                python_version,
                signing_key,
                options.integrity,
                [module.relative.as_posix() for module in modules],
                options.mutable_patterns,
            )
            manifest.write_manifest(temp / MANIFEST_FILE, payload)

        check_cancel(options)
        report_stage(options, "publish", "publishing output directory", 84)
        if output.exists():
            shutil.rmtree(output)
        os.replace(temp, output)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    secret_path = licensing.secret_path_for(output)
    report_stage(options, "secret", "writing build secret", 88)
    licensing.write_secret(secret_path, {
        "format": licensing.FORMAT,
        "tool": f"pyshield {__version__}",
        "build_id": build_id,
        "master_key": crypto.b85encode(master_key),
        "build_key": crypto.b85encode(build_key),
        "signing_key": crypto.b85encode(signing_key),
        "signing_public_key": crypto.b85encode(signing_public_key),
        "cipher": cipher,
        "integrity": options.integrity,
        "manifest": str(output / MANIFEST_FILE) if options.integrity != "off" else None,
        "bind_mode": bind_mode,
        "fingerprints": fingerprints,
        "customer": options.customer or None,
        "grace_days": grace_days,
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "output": str(output),
        "license": str(output / LICENSE_FILE),
    })

    license_path = output / LICENSE_FILE
    if not options.no_verify:
        report_stage(options, "verify", "verifying protected output", 92)
        verify_build(
            output, license_path, expire_date, fingerprints,
            python_executable=options.verify_python, bind_mode=bind_mode,
        )
    else:
        report_log(options, "skipping verification: disabled by the caller")
    report_stage(options, "cleanup", "cleaning bytecode caches", 96)
    clean_pycache(output)

    result = BuildResult(
        output, secret_path, license_path, build_id, len(modules), cipher,
        time.monotonic() - started, directory_size(output),
    )
    report_log(options, f"done: {output}")
    report_log(options, f"license:  {license_path}")
    report_log(options, f"secret:   {secret_path} (keep private; needed to reissue licenses)")
    report_log(options, f"deploy the whole directory: {output}")
    report_stage(options, "done", f"build complete in {result.elapsed:.1f}s", 100)
    return result



@dataclass
class IssueOptions:
    secret: str
    out: str | None = None
    expire: str | None = None
    grace_days: int | None = None
    bind_device: list[str] = field(default_factory=list)
    bind_first_run: bool = False
    customer: str | None = None
    no_restrictions: bool = False


def issue(options: IssueOptions) -> tuple[Path, dict]:
    secret_path = Path(options.secret).resolve()
    secret = licensing.read_secret(secret_path)
    master_key = crypto.b85decode(secret["master_key"])
    build_key = crypto.b85decode(secret["build_key"])
    signing_key = crypto.b85decode(secret["signing_key"])
    signing_public_key = licensing.secret_public_key(secret)
    build_id = secret["build_id"]
    cipher = secret.get("cipher", "chacha")
    python_version = secret.get("python", f"{sys.version_info[0]}.{sys.version_info[1]}")

    if options.out:
        target = Path(options.out).resolve()
    else:
        target = Path(secret.get("license") or Path(secret["output"]) / LICENSE_FILE).resolve()

    previous: dict | None = None
    if target.is_file():
        try:
            previous = licensing.open_license(
                master_key, build_id, target.read_bytes(), signing_public_key,
            )
        except (OSError, licensing.LicenseError) as exc:
            log(f"note: the current license at {target} cannot be reused ({exc})")

    if options.no_restrictions:
        expire_date = None
        bind_mode = licensing.BIND_ANY
        fingerprints: list[str] = []
    else:
        expire_value = options.expire
        if expire_value is None and previous is not None:
            expire_value = previous.get("expires")
        try:
            expire_date = licensing.parse_expire(expire_value)
        except licensing.LicenseError as exc:
            raise BuildError(str(exc)) from exc

        if options.bind_first_run and options.bind_device:
            raise BuildError("--bind-first-run and --bind-device are mutually exclusive")
        if options.bind_first_run:
            bind_mode = licensing.BIND_FIRST_RUN
            fingerprints = []
        elif options.bind_device:
            bind_mode = licensing.BIND_FIXED
            fingerprints = licensing.normalize_fingerprints(options.bind_device)
        elif previous is not None:
            bind_mode = licensing.bind_mode_of(previous)
            fingerprints = licensing.normalize_fingerprints(previous.get("fingerprints") or [])
        else:
            try:
                bind_mode = licensing.normalize_bind_mode(
                    secret.get("bind_mode"), secret.get("fingerprints") or [],
                )
            except licensing.LicenseError as exc:
                raise BuildError(str(exc)) from exc
            fingerprints = licensing.normalize_fingerprints(secret.get("fingerprints") or [])

    customer = options.customer if options.customer is not None else (
        (previous or {}).get("customer") or secret.get("customer")
    )
    if options.grace_days is not None:
        grace_source = options.grace_days
    elif previous is not None:
        grace_source = previous.get("grace_days", secret.get("grace_days", 0))
    else:
        grace_source = secret.get("grace_days", 0)
    try:
        grace_days = licensing.parse_grace_days(grace_source)
    except licensing.LicenseError as exc:
        raise BuildError(str(exc)) from exc

    try:
        sealed = licensing.create_license(
            master_key, build_id, build_key, cipher, python_version, signing_key,
            expires=expire_date, fingerprints=fingerprints, customer=customer,
            grace_days=grace_days, bind_mode=bind_mode,
        )
    except licensing.LicenseError as exc:
        raise BuildError(str(exc)) from exc

    licensing.write_license(target, sealed)

    payload = licensing.open_license(master_key, build_id, sealed, signing_public_key)
    log(f"license written: {target}")
    log("replace only this license file on site; the protected package stays as it is")
    log(licensing.describe(payload))
    return target, payload


def inspect(options: IssueOptions) -> dict:
    secret_path = Path(options.secret).resolve()
    secret = licensing.read_secret(secret_path)
    master_key = crypto.b85decode(secret["master_key"])
    build_id = secret["build_id"]
    target = Path(secret.get("license") or Path(secret["output"]) / LICENSE_FILE).resolve()
    payload = licensing.open_license(master_key, build_id, target.read_bytes(), licensing.secret_public_key(secret))
    log(f"secret:  {secret_path}")
    log(f"build:   {secret.get('output', '?')}")
    log(f"integrity: {secret.get('integrity', 'protected')}")
    if secret.get("manifest"):
        log(f"manifest: {secret['manifest']}")
    log(f"license: {target}")
    log(licensing.describe(payload))
    return payload


@dataclass
class VerifyOptions:
    dist: str
    secret: str | None = None
    public_key: str | None = None
    license: str | None = None
    full: bool = False
    skip_license: bool = False


def verify(options: VerifyOptions) -> dict:
    """Check a protected distribution the way an on-site operator would."""
    root = Path(options.dist).resolve()
    if not root.is_dir():
        raise BuildError(f"distribution directory not found: {root}")

    marker: dict = {}
    marker_path = root / MARKER_FILE
    if marker_path.is_file():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError(f"cannot read build marker {marker_path}: {exc}") from exc

    secret: dict | None = None
    if options.secret:
        secret = licensing.read_secret(Path(options.secret).resolve())

    public_key: bytes | None = None
    if secret is not None:
        public_key = licensing.secret_public_key(secret)
    if options.public_key:
        try:
            public_key = crypto.b85decode(options.public_key)
        except Exception as exc:
            raise BuildError("--public-key must be a base85 encoded Ed25519 public key") from exc
    if public_key is None:
        raise BuildError("pass --secret or --public-key so the manifest signature can be checked")

    integrity = str((secret or {}).get("integrity") or marker.get("integrity") or "protected")
    build_id = marker.get("build_id") or (secret or {}).get("build_id")
    report: dict = {
        "dist": str(root),
        "build_id": build_id,
        "integrity": integrity,
        "manifest": None,
        "license": None,
        "failures": [],
    }

    if secret is not None and marker.get("build_id") and secret["build_id"] != marker["build_id"]:
        report["failures"].append("build secret does not belong to this distribution")

    manifest_path = root / MANIFEST_FILE
    if integrity == "off":
        report["manifest"] = {"enabled": False}
    elif not manifest_path.is_file():
        report["failures"].append(f"package manifest is missing: {MANIFEST_FILE}")
    else:
        try:
            payload = manifest.load_manifest(manifest_path, public_key)
        except manifest.ManifestError as exc:
            report["failures"].append(str(exc))
        else:
            if build_id and payload.get("build_id") != build_id:
                report["failures"].append("package manifest belongs to a different build")
            report["manifest"] = {
                "enabled": True,
                "files": len(payload["files"]),
                "build_id": payload.get("build_id"),
                "integrity": payload.get("integrity"),
            }
            report["failures"].extend(
                manifest.verify_files(root, payload, full=options.full or integrity == "full")
            )

    if not options.skip_license and secret is not None:
        license_path = (
            Path(options.license).resolve()
            if options.license
            else Path(secret.get("license") or root / LICENSE_FILE).resolve()
        )
        if not license_path.is_file():
            report["failures"].append(f"license is missing: {license_path}")
        else:
            try:
                payload = licensing.open_license(
                    crypto.b85decode(secret["master_key"]), secret["build_id"],
                    license_path.read_bytes(), licensing.secret_public_key(secret),
                )
            except licensing.LicenseError as exc:
                report["failures"].append(str(exc))
            else:
                problems = licensing.validate_payload(payload)
                report["failures"].extend(problems)
                report["license"] = {
                    "path": str(license_path),
                    "expires": payload.get("expires"),
                    "not_before": payload.get("not_before"),
                    "grace_days": payload.get("grace_days", 0),
                    "bind_mode": licensing.bind_mode_of(payload),
                    "fingerprints": payload.get("fingerprints") or [],
                    "customer": payload.get("customer"),
                }

    report["ok"] = not report["failures"]
    return report

