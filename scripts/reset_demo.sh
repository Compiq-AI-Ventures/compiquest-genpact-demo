#!/usr/bin/env bash
# reset_demo.sh — One-shot helper that drops + reseeds the Genpact demo tenant.
#
# Use this any time you've mutated state via the API / frontend and want a
# clean slate:
#
#   ./scripts/reset_demo.sh
#
# Equivalent to:
#   uv run python -m scripts.seed_genpact_master_data
#   uv run python -m scripts.seed_genpact_tenant
# but doesn't require you to be in the repo root or remember the module path.
#
# Idempotent. The genpact_* analytics tables (master data) are only rebuilt
# by seed_genpact_master_data; seed_genpact_tenant rebuilds the transactional
# tenant (users, roles, cycles, budget allocations, JVRE snapshots, ...) on
# top of them.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

# Propagate DATABASE_URL from .env if not already in the environment, so
# every sub-script connects to the same DB that the main seed uses.
if [ -f "$REPO_ROOT/.env" ] && [ -z "${DATABASE_URL:-}" ]; then
    export DATABASE_URL
    DATABASE_URL=$(grep -E '^DATABASE_URL=' "$REPO_ROOT/.env" | cut -d= -f2-)
fi

echo "→ Reseeding Genpact master data (genpact_* analytics tables)"
uv run python -m scripts.seed_genpact_master_data

echo ""
echo "→ Reseeding Genpact demo tenant (users, roles, cycles, budgets, JVRE)"
uv run python -m scripts.seed_genpact_tenant "$@"
