#!/usr/bin/env bash
#
# Recreate the fictional demo dataset on the local dev stack.
#
# Seeds a landlord + tenant account with 4 properties (one split into 4
# separately-let rooms), four years of tenancy and payment history, a few
# arrears and a few unpaid monthly taxes. Idempotent: it wipes and recreates
# ONLY the two demo accounts (demo.wynajmujacy@example.com / anna.kowalska@
# example.com), never touching any real account. Safe to run repeatedly.
#
# Usage:
#   scripts/seed_demo.sh                 # uses `podman compose`
#   COMPOSE="docker compose" scripts/seed_demo.sh
#
# Any extra arguments are passed through to the management command, e.g.:
#   scripts/seed_demo.sh --seed 7        # different reproducible RNG seed
#
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="${COMPOSE:-podman compose}"

echo ">> Seeding demo data via: ${COMPOSE} run --rm web python manage.py seed_demo $*"
${COMPOSE} run --rm web python manage.py seed_demo "$@"
