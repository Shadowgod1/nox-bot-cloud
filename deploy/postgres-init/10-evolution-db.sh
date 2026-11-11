#!/usr/bin/env bash
# Runs once, on the first start of an empty pg_data volume (docker-entrypoint-initdb.d).
# Creates the second database "evolution" owned by its own role.
set -euo pipefail

if [[ -z "${ILER_EVO_DB_PASSWORD:-}" ]]; then
  echo "10-evolution-db: ILER_EVO_DB_PASSWORD is not set" >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v evo_pw="$ILER_EVO_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE evolution LOGIN PASSWORD %L', :'evo_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evolution')\gexec
SELECT 'CREATE DATABASE evolution OWNER evolution'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'evolution')\gexec
SQL
