#!/usr/bin/env bash
# Deploy ("Option A"): pull, build, back up DB, migrate ONCE, collectstatic, start.
# Run this ON THE VPS from the repo directory.
#
# Migrations + collectstatic run here (via `run --rm`) and NOT in the web
# container's start command, so container/worker starts never race to create the
# schema (which caused "duplicate key ... pg_type django_migrations").
set -euo pipefail

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "==> Pulling latest code"
git pull --ff-only

echo "==> Building images"
$COMPOSE build

echo "==> Starting database + redis"
$COMPOSE up -d db redis

echo "==> Backing up database (best-effort; skipped on an empty first deploy)"
mkdir -p backups
TS=$(date +%Y%m%d-%H%M%S)
$COMPOSE exec -T db pg_dump -U "${POSTGRES_USER:-np}" "${POSTGRES_DB:-np}" \
    > "backups/np-${TS}.sql" 2>/dev/null || echo "   (backup skipped)"

echo "==> Applying migrations (once, before serving)"
$COMPOSE run --rm web python manage.py migrate --noinput

echo "==> Collecting static files"
# Ignore static/src/ — that's the Tailwind SOURCE (input.css with @import
# "tailwindcss" / @source ...). Only the built static/css/app.css is served;
# collecting the source makes WhiteNoise's manifest post-processor choke on
# the un-resolvable @import reference.
$COMPOSE run --rm web python manage.py collectstatic --noinput --ignore=src

echo "==> Starting app (web + worker)"
$COMPOSE up -d

echo "==> Done. Current status:"
$COMPOSE ps
