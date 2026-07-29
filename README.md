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
apps/core/        First app: landing page + health check
tests/            Test suite (pytest)
docker-compose.yml            base services
docker-compose.override.yml   dev (auto-loaded): runserver + hot reload
docker-compose.prod.yml       prod: gunicorn + migrate + collectstatic
deploy.sh         Option-A deploy (git pull + rebuild) — run on the VPS
.github/workflows/ci.yml      CI: lint + type-check + tests
```

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
