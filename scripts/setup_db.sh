#!/usr/bin/env bash
# Provision the local Postgres role + database for jastip development.
# Requires sudo (to act as the postgres superuser). Run once:
#   bash scripts/setup_db.sh
# Then set in .env:
#   DATABASE_URL=postgres://jastip:jastip@127.0.0.1:5432/jastip_dev
set -euo pipefail

DB_NAME="${DB_NAME:-jastip_dev}"
DB_USER="${DB_USER:-jastip}"
DB_PASS="${DB_PASS:-jastip}"

echo ">> Creating role '$DB_USER' and database '$DB_NAME' (sudo postgres)…"
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
      CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';
   END IF;
END
\$\$;
SELECT 'creating db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec
SQL

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
  || sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"

sudo -u postgres psql -c "ALTER DATABASE $DB_NAME OWNER TO $DB_USER;"
echo ">> Done. Set DATABASE_URL=postgres://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME in .env"
