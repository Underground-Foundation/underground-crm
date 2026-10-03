#!/usr/bin/env bash
# Backs up the remote (production) PostgreSQL database to a local file.
#
# This script assumes an SSH tunnel is already running, forwarding 127.0.0.1:15432
# on this machine to PostgreSQL on the remote host. The
# tunnel's local port is 15432 rather than 5432 so that a missing tunnel produces
# "connection refused" instead of quietly backing up a local development database.
# This script checks if something is listening before it begins.
#
# The script can accept an environment file for configuring the variables
# PGHOST, PGPORT, PGUSER, PGDATABASE, and PGPASSWORD.
#
# The dump uses pg_dump's custom format (compressed, and restorable selectively with
# pg_restore). It is written to a temporary name and renamed on success, so an
# interrupted run can never leave a truncated file that looks like a finished backup.
#
# Usage:
#   ./backup_remote_postgresql.sh [--env-file <path>] [--output-dir <directory>]
set -euo pipefail

ENV_FILE=""
OUTPUT_DIR="backups"

usage() {
  echo "Usage: $0 [--env-file <path>] [--output-dir <directory>]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="${2:?--env-file needs a path}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:?--output-dir needs a directory}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

if [[ -n "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: env file not found: $ENV_FILE" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-15432}"
for name in PGUSER PGDATABASE; do
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: $name is not set. Export it or supply it through --env-file." >&2
    exit 1
  fi
done

if ! command -v pg_dump >/dev/null; then
  echo "ERROR: pg_dump is not installed (on Ubuntu: sudo apt install postgresql-client)." >&2
  exit 1
fi

if ! pg_isready --quiet --timeout 5; then
  echo "ERROR: nothing is accepting PostgreSQL connections at $PGHOST:$PGPORT." >&2
  echo "       Is the tunnel running? Start ~/fusion/tunnel-postgresql.sh first." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
timestamp="$(date +%Y-%m-%dT%H%M%S)"
destination="$OUTPUT_DIR/${PGDATABASE}-${timestamp}.dump"
partial="$destination.partial"
trap 'rm -f "$partial"' EXIT

echo "Backing up $PGDATABASE from $PGHOST:$PGPORT to $destination" >&2
pg_dump --format=custom --no-password --file "$partial"
mv "$partial" "$destination"
echo "Backup complete: $destination ($(du -h "$destination" | cut -f1))" >&2
