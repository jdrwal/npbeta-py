# npbeta (Python/Django rewrite)

Modern rewrite of the legacy PHP `npbeta` rental-property management app.

**Stack:** Django 5 · PostgreSQL 16 · Redis + Celery · HTMX + Django templates ·
Gunicorn (prod) · pytest / ruff / mypy · Docker Compose.

## Status

Phase 0 — project skeleton. Domain models and business logic are ported in
later phases (see the migration plan).

## Requirements

- Docker (or Podman) with Compose
- Make (optional, convenience)

## Local development

```bash
cp .env.example .env          # adjust secrets
make up                       # build + start web, db, redis, worker
# open http://localhost:8000  and http://localhost:8000/healthz/
```

Using Podman instead of Docker:

```bash
make up COMPOSE="podman compose"
```

Common tasks:

```bash
make migrate        # apply DB migrations
make superuser      # create an admin user
make test           # ruff + mypy + pytest
make down           # stop everything
```

## Layout

```
config/           Django project (settings, urls, wsgi/asgi, celery)
apps/accounts/    Custom user model + legacy MD5 password hasher
apps/core/        Domain models, admin, and the load_legacy ETL command
tests/            Test suite (pytest)
data-migration/   Legacy SQL dumps (gitignored)
docker-compose.yml            base services (+ legacydb under `etl` profile)
docker-compose.override.yml   dev (auto-loaded): runserver + hot reload
docker-compose.prod.yml       prod: gunicorn + migrate + collectstatic
deploy.sh         Option-A deploy (git pull + rebuild) — run on the VPS
.github/workflows/ci.yml      CI: lint + type-check + tests
```

## Data migration (legacy import)

One-off import of the old PHP/MariaDB data into the new models. The legacy
MariaDB runs only for this, behind the `etl` compose profile.

```bash
# 1. Start the legacy MariaDB (+ app DB and cache)
podman compose --profile etl up -d db redis legacydb

# 2. Apply Django migrations to PostgreSQL
podman compose run --rm web python manage.py migrate

# 3. Load the legacy dump into MariaDB (dump is gitignored)
podman compose --profile etl exec -T legacydb \
    mariadb -uroot -plegacy np < data-migration/<dump>.sql

# 4. Import into PostgreSQL (float→Decimal, FKs, soft-delete, MD5→legacy hash)
podman compose run --rm web python manage.py load_legacy --flush
```

Use `docker compose` instead of `podman compose` if you run Docker. The
`load_legacy` command preserves primary keys, cleans binary-float artefacts and
skips orphaned rows; re-run with `--flush` to reset and re-import.

## Deployment (Option A)

On the VPS, from the repo directory:

```bash
./deploy.sh
```

It pulls, backs up the DB, rebuilds containers, migrates and collects static
files. Put the production secrets in `.env` on the server (never committed).

## Configuration

All settings come from environment variables — see `.env.example`. The same
image runs locally and in production; only `.env` differs.
