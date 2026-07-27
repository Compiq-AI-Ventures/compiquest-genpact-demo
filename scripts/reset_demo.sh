#!/usr/bin/env bash
# reset_demo.sh — One-shot helper that drops + reseeds the Oscorp demo tenant.
#
# Use this between recording takes (or any time you've mutated state via
# Walk A / Walk B / the React frontend / curl and want a clean slate):
#
#   ./scripts/reset_demo.sh
#
# Equivalent to:
#   uv run python -m scripts.seed_demo_tenant
# but doesn't require you to be in the repo root or remember the module path.
#
# Idempotent. Uses a fixed RNG seed, so the same Spider-Man characters land
# in the same roles every time.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

# Propagate DATABASE_URL from .env if not already in the environment, so
# every sub-script connects to the same DB that the main seed uses.
if [ -f "$REPO_ROOT/.env" ] && [ -z "${DATABASE_URL:-}" ]; then
    export DATABASE_URL
    DATABASE_URL=$(grep -E '^DATABASE_URL=' "$REPO_ROOT/.env" | cut -d= -f2-)
fi

echo "→ Reseeding Oscorp demo tenant from $REPO_ROOT"
uv run python -m scripts.seed_demo_tenant "$@"

# Post-seed enrichment scripts (added with the manager-dashboard work).
# Each looks up the demo tenant by code, so they only work after
# seed_demo_tenant has run — chained here so the demo flow stays
# one-command.
echo ""
echo "→ Linking users to departments"
uv run python -m scripts.seed_departments

echo ""
echo "→ Setting job titles on users"
uv run python -m scripts.seed_job_titles
