#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
destination="${1:-$PWD/pyshield-portal-src.tar.gz}"

if [[ "$destination" != /* ]]; then
    destination="$PWD/$destination"
fi

excludes=(
    --exclude=.git
    --exclude=.venv
    --exclude=.pytest_cache
    --exclude=__pycache__
    --exclude='*.pyc'
    --exclude='*.pyo'
    --exclude=build
    --exclude=dist
    --exclude='dist-*'
    --exclude=HA7HqyYHFsn4CepB-grok-workspace
    --exclude=review
    --exclude=.tmp-portal-demo
    --exclude=.pyshield-demo
    --exclude=.pyshield-native
    --exclude=license-data
    --exclude=pyshield/_tool_key.py
    --exclude=pyshield/_site.py
    --exclude=root.key
    --exclude=root.pub
    --exclude='*.lic'
    --exclude='*.shield-secret.json'
    --exclude='portal.sqlite3*'
    --exclude=portal.secret
    --exclude=signing.key
    --exclude=signing.pass
    --exclude=license-portal/config.json
    --exclude=license-portal/docker/.env
    --exclude=license-portal/web/node_modules
    --exclude=license-portal/web/dist
    --exclude=license-portal/web/.vite
)

tar -czf "$destination" "${excludes[@]}" \
    -C "$repo_root" pyshield license-portal .dockerignore

echo "Created $destination ($(du -h "$destination" | cut -f1))"
echo "Upload it to the Linux server, extract it into the source directory,"
echo "then run: docker compose build --pull && docker compose up -d"
