param(
    [string]$Destination = "pyshield-portal-src.tar.gz"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not [System.IO.Path]::IsPathRooted($Destination)) {
    $Destination = Join-Path (Get-Location) $Destination
}
$Destination = [System.IO.Path]::GetFullPath($Destination)

$Excludes = @(
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "build",
    "dist",
    "dist-*",
    "HA7HqyYHFsn4CepB-grok-workspace",
    "review",
    ".tmp-portal-demo",
    ".pyshield-demo",
    ".pyshield-native",
    "license-data",
    "pyshield/_tool_key.py",
    "pyshield/_site.py",
    "root.key",
    "root.pub",
    "*.lic",
    "*.shield-secret.json",
    "portal.sqlite3*",
    "portal.secret",
    "signing.key",
    "signing.pass",
    "license-portal/config.json",
    "license-portal/docker/.env",
    "license-portal/web/node_modules",
    "license-portal/web/dist",
    "license-portal/web/.vite"
)

$TarArgs = @("-czf", $Destination)
$TarArgs += $Excludes | ForEach-Object { "--exclude=$_" }
$TarArgs += @("-C", $RepoRoot, "pyshield", "license-portal", ".dockerignore")

# Git for Windows puts GNU tar on PATH; GNU tar treats "E:\..." as a remote
# host. Prefer the Windows bsdtar, which understands native paths.
$TarExe = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path $TarExe)) {
    $TarExe = "tar"
}
& $TarExe @TarArgs
if ($LASTEXITCODE -ne 0) {
    throw "$TarExe failed with exit code $LASTEXITCODE"
}

$archive = Get-Item $Destination
Write-Host ("Created {0} ({1:N1} MiB)" -f $archive.FullName, ($archive.Length / 1MB))
Write-Host "Upload it to the Linux server, extract it into the source directory,"
Write-Host "then run: docker compose build --pull && docker compose up -d"
