#!/usr/bin/env bash
# Restores a backup made by backup_remote_postgresql.sh into a PostgreSQL database,
# whether a local one or a remote one reached through a tunnel.
#
# The destination is configured through libpq's standard environment variables
# (PGHOST, PGPORT, PGUSER, PGPASSWORD), most conveniently by pointing --env-file at a file
# that defines them. Unlike the backup script, nothing defaults to the production tunnel:
# if PGHOST and PGPORT are unset, libpq uses the local server. The database to restore
# into is named with --database (or PGDATABASE).
#
# A database that already exists is never touched unless --replace is given, in which case
# it is dropped and recreated first, disconnecting anybody using it. Because that is
# destructive, the script prints the destination and asks you to type the database's name
# before proceeding; --yes skips that prompt for unattended use.
#
# The restore runs in a single transaction and stops at the first error, so a failure
# leaves the new database empty rather than half-populated. Ownership and privileges from
# the source are not restored: the objects belong to the connecting role, which means the
# source's roles do not need to exist at the destination. If the backup creates
# extensions, the connecting role must be allowed to create them.
#
# Usage:
#   ./restore_postgresql.sh --backup-file <path> --database <name>
#       [--env-file <path>] [--replace] [--yes]
set -euo pipefail

BACKUP_FILE=""
ENV_FILE=""
DATABASE="${PGDATABASE:-}"
REPLACE=false
ASSUME_YES=false

usage() {
  echo "Usage: $0 --backup-file <path> --database <name> [--env-file <path>] [--replace] [--yes]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-file) BACKUP_FILE="${2:?--backup-file needs a path}"; shift 2 ;;
    --database) DATABASE="${2:?--database needs a name}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?--env-file needs a path}"; shift 2 ;;
    --replace) REPLACE=true; shift ;;
    --yes) ASSUME_YES=true; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

if [[ -z "$BACKUP_FILE" ]]; then
  echo "ERROR: --backup-file is required." >&2
  usage
fi
if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

if [[ -n "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: env file not found: $ENV_FILE" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  # --database on the command line wins; otherwise the env file may supply it.
  DATABASE="${DATABASE:-${PGDATABASE:-}}"
fi

if [[ -z "$DATABASE" ]]; then
  echo "ERROR: name the destination database with --database." >&2
  exit 1
fi

for tool in pg_restore psql pg_isready; do
  if ! command -v "$tool" >/dev/null; then
    echo "ERROR: $tool is not installed (on Ubuntu: sudo apt install postgresql-client)." >&2
    exit 1
  fi
done

# Connections for creating and dropping go to the maintenance database, since the target
# may not exist yet. PGDATABASE is cleared so that it cannot redirect those connections.
unset PGDATABASE
destination="${PGHOST:-local socket}:${PGPORT:-5432}"

if ! pg_isready --quiet --timeout 5; then
  echo "ERROR: nothing is accepting PostgreSQL connections at $destination." >&2
  echo "       If the destination is remote, is its tunnel running?" >&2
  exit 1
fi

exists="$(psql --no-psqlrc --tuples-only --no-align --dbname postgres \
  --set ON_ERROR_STOP=1 --set db="$DATABASE" \
  <<< "SELECT 1 FROM pg_database WHERE datname = :'db'")"

if [[ -n "$exists" && "$REPLACE" != true ]]; then
  echo "ERROR: database \"$DATABASE\" already exists at $destination." >&2
  echo "       Pass --replace to drop and recreate it." >&2
  exit 1
fi

echo "Backup:      $BACKUP_FILE" >&2
echo "Destination: database \"$DATABASE\" at $destination as ${PGUSER:-the current user}" >&2
if [[ -n "$exists" ]]; then
  echo "WARNING:     the existing database will be DROPPED and replaced." >&2
fi

if [[ "$ASSUME_YES" != true ]]; then
  read -r -p "Type the database name to continue: " answer
  if [[ "$answer" != "$DATABASE" ]]; then
    echo "Aborted." >&2
    exit 1
  fi
fi

if [[ -n "$exists" ]]; then
  psql --no-psqlrc --quiet --dbname postgres --set ON_ERROR_STOP=1 --set db="$DATABASE" \
    <<< 'DROP DATABASE :"db" WITH (FORCE)'
fi
psql --no-psqlrc --quiet --dbname postgres --set ON_ERROR_STOP=1 --set db="$DATABASE" \
  <<< 'CREATE DATABASE :"db"'

echo "Restoring..." >&2
if ! pg_restore --no-owner --no-privileges --exit-on-error --single-transaction \
    --dbname "$DATABASE" "$BACKUP_FILE"; then
  echo "ERROR: the restore failed; \"$DATABASE\" was created but is empty." >&2
  exit 1
fi
echo "Restore complete: $BACKUP_FILE -> $DATABASE at $destination" >&2
