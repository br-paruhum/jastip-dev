#!/usr/bin/env bash
#
# Seed / reset the Jastip STAGING database.
#
# Usage:
#   ./seed.sh           # re-run seed: re-add baseline pages, FAQ, admin, demo
#                       #   plans & blog post. Existing data is kept (idempotent).
#   ./seed.sh --reset   # FULL CLEAN SLATE: back up the DB, flush ALL data,
#                       #   then seed. Wipes everything (incl. the admin user,
#                       #   which seed recreates as admin@jastip.me/ChangeMe!2026).
#
# Run from anywhere; it locates the app dir itself.
set -euo pipefail

APP_DIR="/var/www/jastip-stg"
PY="${APP_DIR}/.venv/bin/python"
DB_NAME="jastip_stg"
BACKUP_DIR="/tmp"

cd "${APP_DIR}"

reset=0
case "${1:-}" in
  "")        reset=0 ;;
  --reset)   reset=1 ;;
  -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
  *) echo "Unknown argument: $1" >&2; echo "Usage: $(basename "$0") [--reset]" >&2; exit 2 ;;
esac

if [[ "${reset}" == "1" ]]; then
  echo "⚠️  --reset will DELETE ALL staging data, then re-seed."
  read -r -p "   Type 'yes' to continue: " confirm
  [[ "${confirm}" == "yes" ]] || { echo "Aborted."; exit 1; }

  # Safety backup (DATABASE_URL lives in .env: postgres://user:pass@host:port/db)
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup="${BACKUP_DIR}/${DB_NAME}_pre-reset_${stamp}.sql"
  url="$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)"
  proto="${url#postgres://}"
  creds="${proto%%@*}"; hostpart="${proto#*@}"
  user="${creds%%:*}"; pass="${creds#*:}"
  hostport="${hostpart%%/*}"; host="${hostport%%:*}"; port="${hostport#*:}"
  [[ "${port}" == "${host}" ]] && port="5432"

  echo "→ Backing up ${DB_NAME} → ${backup}"
  PGPASSWORD="${pass}" pg_dump -h "${host}" -p "${port}" -U "${user}" "${DB_NAME}" > "${backup}"
  echo "✓ Backup saved (restore with: PGPASSWORD=… psql -h ${host} -U ${user} ${DB_NAME} < ${backup})"

  echo "→ Flushing all data…"
  "${PY}" manage.py flush --noinput
fi

echo "→ Seeding…"
"${PY}" manage.py seed
echo "✓ Done."
