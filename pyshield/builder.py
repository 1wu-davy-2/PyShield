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
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import __version__, crypto, ed25519, fingerprint, licensing

RUNTIME_PACKAGE = "_shield_runtime"
MARKER_FILE = ".pyshield-build.json"
LICENSE_FILE = licensing.LICENSE_FILENAME
PAYLOAD_CHUNK = 1000
NATIVE_MODULES = ("_core", "_crypto", "_ed25519", "_chacha_fast")
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
    encrypt_migrations: bool = False
    encrypt_tests: bool = False
    expire: str | None = None
    bind_device: list[str] = field(default_factory=list)
    cipher: str = "auto"
    customer: str | None = None
    runtime_pyc: bool = False
    runtime_native: bool = False
    no_verify: bool = False
    force: bool = False
    dry_run: bool = False


@dataclass
class BuildResult:
    output: Path
    secret: Path
    license: Path
    build_id: str
    modules: int
    cipher: str


def log(message: str) -> None:
    print(f"[pyshield] {message}")


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
        elif name in {"db.sqlite3", LICENSE_FILE, MARKER_FILE}:
            ignored.add(name)
        elif name.endswith(licensing.SECRET_SUFFIX):
            ignored.add(name)
    return ignored


def default_excludes(encrypt_migrations: bool = False, encrypt_tests: bool = False) -> list[str]:
    patterns: list[str] = []
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


def protect_module(module: Module, build_key: bytes, cipher: str) -> str:
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
    sealed = crypto.seal(key, raw, crypto.module_aad(module.module_id), cipher=cipher)
    return crypto.b85encode(sealed)


def install_runtime(
    output: Path,
    build_id: str,
    master_key: bytes,
    signing_public_key: bytes,
    cipher: str,
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


def write_marker(output: Path, build_id: str, cipher: str, module_count: int, runtime_mode: str) -> None:
    marker = {
        "format": 1,
        "tool": f"pyshield {__version__}",
        "build_id": build_id,
        "cipher": cipher,
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


def verify_build(output: Path, license_path: Path, expire_date: date | None, fingerprints: list[str]) -> None:
    if expire_date is not None and expire_date < date.today():
        log("skipping verification: the build is already expired")
        return
    if fingerprints and fingerprint.machine_fingerprint() not in fingerprints:
        log("skipping verification: the build is bound to another machine")
        return
    env = os.environ.copy()
    env["PYSHELD_LICENSE"] = str(license_path)
    log("running manage.py check on the protected output")
    result = subprocess.run(
        [sys.executable, str(output / "manage.py"), "check"],
        cwd=output, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        details = (result.stdout + result.stderr).strip()
        raise BuildError("protected project failed manage.py check:\n" + details)


def clean_pycache(root: Path) -> None:
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def build(options: BuildOptions) -> BuildResult:
    project = Path(options.project).resolve()
    output = Path(options.output).resolve()

    if not (project / "manage.py").is_file():
        raise BuildError(f"manage.py not found in {project}")
    if options.runtime_native and options.runtime_pyc:
        raise BuildError("--runtime-native and --runtime-pyc are mutually exclusive")
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

    local = fingerprint.machine_fingerprint()
    log(f"pyshield {__version__} on CPython {sys.version_info[0]}.{sys.version_info[1]}")
    log(f"project:  {project}")
    log(f"output:   {output}")
    log(f"cipher:   {cipher}")
    log(f"runtime:  {runtime_mode}")
    if cipher in {"aesgcm", "chacha20poly1305"}:
        log(f"note: {cipher} builds require the 'cryptography' package on the deployment machine")
    log(f"expire:   {expire_date.isoformat() if expire_date else 'never'}")
    log(f"bound to: {', '.join(fingerprints) if fingerprints else 'any machine'}")
    if options.bind_device and local not in fingerprints:
        log(f"note: this machine's fingerprint is {local}")
    log(f"modules:  {len(modules)}")
    if options.dry_run:
        for module in modules:
            log(f"  would protect {module.relative.as_posix()} as {module.module_id}")
        return BuildResult(output, licensing.secret_path_for(output), output / LICENSE_FILE, "-", len(modules), cipher)

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
        log("copying project data files")
        shutil.copytree(project, temp, ignore=copy_ignore)

        log("encrypting Python modules")
        for module in modules:
            payload = protect_module(module, build_key, cipher)
            stub = make_stub(payload, module.module_id)
            target = temp / module.relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(stub, encoding="utf-8", newline="\n")

        log("installing runtime")
        install_runtime(
            temp, build_id, master_key, signing_public_key, cipher,
            options.runtime_pyc, options.runtime_native,
        )

        python_version = f"{sys.version_info[0]}.{sys.version_info[1]}"
        log("writing license")
        sealed = licensing.create_license(
            master_key, build_id, build_key, cipher, python_version, signing_key,
            expires=expire_date, fingerprints=fingerprints, customer=options.customer,
        )
        licensing.write_license(temp / LICENSE_FILE, sealed)
        write_marker(temp, build_id, cipher, len(modules), runtime_mode)

        if output.exists():
            shutil.rmtree(output)
        os.replace(temp, output)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    secret_path = licensing.secret_path_for(output)
    licensing.write_secret(secret_path, {
        "format": licensing.FORMAT,
        "tool": f"pyshield {__version__}",
        "build_id": build_id,
        "master_key": crypto.b85encode(master_key),
        "build_key": crypto.b85encode(build_key),
        "signing_key": crypto.b85encode(signing_key),
        "signing_public_key": crypto.b85encode(signing_public_key),
        "cipher": cipher,
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "output": str(output),
        "license": str(output / LICENSE_FILE),
    })

    license_path = output / LICENSE_FILE
    if not options.no_verify:
        verify_build(output, license_path, expire_date, fingerprints)
    clean_pycache(output)

    log(f"done: {output}")
    log(f"license:  {license_path}")
    log(f"secret:   {secret_path} (keep private; needed to reissue licenses)")
    log(f"deploy the whole directory: {output}")
    return BuildResult(output, secret_path, license_path, build_id, len(modules), cipher)


@dataclass
class IssueOptions:
    secret: str
    out: str | None = None
    expire: str | None = None
    bind_device: list[str] = field(default_factory=list)
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

    if options.no_restrictions:
        expire_date = None
        fingerprints: list[str] = []
    else:
        try:
            expire_date = licensing.parse_expire(options.expire)
        except licensing.LicenseError as exc:
            raise BuildError(str(exc)) from exc
        fingerprints = licensing.normalize_fingerprints(options.bind_device)

    sealed = licensing.create_license(
        master_key, build_id, build_key, cipher, python_version, signing_key,
        expires=expire_date, fingerprints=fingerprints, customer=options.customer,
    )

    if options.out:
        target = Path(options.out).resolve()
    else:
        target = Path(secret.get("license") or Path(secret["output"]) / LICENSE_FILE).resolve()
    licensing.write_license(target, sealed)

    payload = licensing.open_license(master_key, build_id, sealed, signing_public_key)
    log(f"license written: {target}")
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
    log(f"license: {target}")
    log(licensing.describe(payload))
    return payload
