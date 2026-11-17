#!/bin/sh
set -eu

umask 077

DATA_DIR="${PYSHELD_PORTAL_DATA_DIR:-/etc/pyshield-portal}"
case "$DATA_DIR" in
    /*) ;;
    *)
        echo "[entrypoint] PYSHELD_PORTAL_DATA_DIR must be an absolute path" >&2
        exit 1
        ;;
esac
DATA_DIR="$(realpath -m "$DATA_DIR")"
case "$DATA_DIR" in
    /|/etc|/usr|/var|/app|/home|/root)
        echo "[entrypoint] refusing unsafe data directory: $DATA_DIR" >&2
        exit 1
        ;;
esac
CONFIG_FILE="$DATA_DIR/config.json"
ADMIN_USER="${PYSHELD_PORTAL_ADMIN_USER:-admin}"
ADMIN_PASSWORD="${PYSHELD_PORTAL_ADMIN_PASSWORD:-admin123@}"
DEFAULT_ADMIN_PASSWORD=0
if [ -z "${PYSHELD_PORTAL_ADMIN_PASSWORD:-}" ]; then
    DEFAULT_ADMIN_PASSWORD=1
fi

# The bind-mounted data directory is created by Docker as root. Hand it to the
# unprivileged application user once, then run the portal without root.
if [ "$(id -u)" -eq 0 ]; then
    mkdir -p "$DATA_DIR"
    chown -R pyshield:pyshield "$DATA_DIR"
    chmod 0700 "$DATA_DIR" || true
    exec gosu pyshield "$0" "$@"
fi

export PYSHELD_PORTAL_DATA_DIR="$DATA_DIR"
export PYSHELD_PORTAL_HOST="${PYSHELD_PORTAL_HOST:-0.0.0.0}"
export PYSHELD_PORTAL_PORT="${PYSHELD_PORTAL_PORT:-8091}"
export PYSHELD_PORTAL_WEB_DIR="${PYSHELD_PORTAL_WEB_DIR:-/app/license-portal/web/dist}"
export PYSHELD_PORTAL_ADMIN_PASSWORD="$ADMIN_PASSWORD"

# Explicit commands are useful for maintenance, for example:
#   docker compose run --rm portal signing show
if [ "$#" -gt 0 ]; then
    exec python -m license_portal "$@"
fi

if [ ! -f "$DATA_DIR/portal.sqlite3" ]; then
    echo "[entrypoint] first start: creating database, secrets and signing key in $DATA_DIR"
    if [ "$DEFAULT_ADMIN_PASSWORD" -eq 1 ]; then
        echo "[entrypoint] WARNING: using the built-in bootstrap password; change it after login"
    fi
    if [ "${#ADMIN_PASSWORD}" -lt 10 ]; then
        echo "[entrypoint] WARNING: bootstrap password is shorter than the normal 10-character policy"
    fi

    set -- init
    if [ -f "$CONFIG_FILE" ]; then
        set -- "$@" --config "$CONFIG_FILE"
    fi
    set -- "$@" --username "$ADMIN_USER"

    # admin123@ is shorter than the normal 10-character policy. This flag is
    # intentionally limited to the one-time init path; admin create/reset keep
    # the strong policy.
    if [ "${#ADMIN_PASSWORD}" -lt 10 ]; then
        set -- "$@" --allow-weak-password
    fi

    python -m license_portal "$@"
fi

# Do not keep the bootstrap credential in the long-running server process.
unset PYSHELD_PORTAL_ADMIN_PASSWORD || true

set -- run
if [ -f "$CONFIG_FILE" ]; then
    set -- "$@" --config "$CONFIG_FILE"
fi

echo "[entrypoint] starting PyShield license portal on ${PYSHELD_PORTAL_HOST}:${PYSHELD_PORTAL_PORT}"
exec python -m license_portal "$@"
