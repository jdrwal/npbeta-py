#!/usr/bin/env bash
# Simple "Option A" deploy: pull, back up DB, rebuild, migrate.
# Run this ON THE VPS from the repo directory.
set -euo pipefail

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "==> Pulling latest code"
git pull --ff-only

echo "==> Backing up database"
mkdir -p backups
TS=$(date +%Y%m%d-%H%M%S)
# Best-effort backup; skip if db container isn't up yet (first deploy).
if $COMPOSE ps db >/dev/null 2>&1; then
    $COMPOSE exec -T db pg_dump -U "${POSTGRES_USER:-np}" "${POSTGRES_DB:-np}" \
        > "backups/np-${TS}.sql" || echo "   (backup skipped — db not running yet)"
fi

echo "==> Building and starting containers"
$COMPOSE up -d --build

echo "==> Applying migrations"
$COMPOSE exec -T web python manage.py migrate --noinput

echo "==> Collecting static files"
$COMPOSE exec -T web python manage.py collectstatic --noinput

echo "==> Done. Current status:"
$COMPOSE ps
