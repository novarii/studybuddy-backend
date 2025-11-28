#!/usr/bin/env bash

set -euo pipefail

DB_SERVICE=${DB_SERVICE:-db}
DB_USER=${DB_USER:-postgres}
DB_NAME=${DB_NAME:-studybuddy}

MIGRATIONS_DIR="migrations/versions"

if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "Missing migrations directory: $MIGRATIONS_DIR" >&2
  exit 1
fi

for migration in $(ls "$MIGRATIONS_DIR"/*.sql | sort); do
  echo "Applying migration: $migration"
  docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" < "$migration"
done

echo "All migrations applied."
