#!/usr/bin/env bash
# demo_walk.sh — Live screencast walk of the JVRE Workspace v0.1 API.
#
# Mirrors §7 of docs/specs/jvre_workspace_v0.1_api.md:
#   Walk A: MoM Budget Allocation (Otto Octavius funds his MoPs)
#   Walk B: MoP Pay Recommendation + MoM Pay Review
#           (Eddie Brock submits, Otto reviews + revises + approves)
#
# Usage:
#   ./scripts/demo_walk.sh                 # both walks, default pace
#   ./scripts/demo_walk.sh walk-a          # Walk A only
#   ./scripts/demo_walk.sh walk-b          # Walk B only (assumes Walk A
#                                          #   already submitted Otto's allocation)
#   ./scripts/demo_walk.sh reset           # reseed only — call between takes
#                                          #   to wipe state from a prior walk
#   PACE=0.3 ./scripts/demo_walk.sh        # speed up (default 0.8s)
#   PACE=2.0 ./scripts/demo_walk.sh        # slow down for narration
#   HOST=http://stage:8000 ./scripts/demo_walk.sh
#   ./scripts/demo_walk.sh --reseed        # reseed THEN run the walks
#                                          #   (combine with walk-a / walk-b too)
#
# Recording for a clean take:
#   1. ./scripts/reset_demo.sh             (or ./scripts/demo_walk.sh reset)
#   2. Resize terminal to 100x32 (or whatever your recorder likes).
#   3. clear && ./scripts/demo_walk.sh
#
# IMPORTANT: After every full walk, state is dirty (allocation SUBMITTED,
# recommendations APPROVED). To run again, reseed first — the seed script
# is idempotent and uses a fixed RNG seed, so the same characters land in
# the same roles every time.
#
# Requirements: bash, curl, jq. On Windows, run from Git Bash.
# Backend must already be running at $HOST (default http://127.0.0.1:8000).

set -euo pipefail

HOST="${HOST:-http://127.0.0.1:8000}"
PACE="${PACE:-0.8}"
DEMO_PASSWORD="oscorp-demo-12345"

# Resolve the backend repo root (this script lives at <root>/scripts/demo_walk.sh)
# so `uv run python -m scripts.seed_demo_tenant` works regardless of cwd.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Colors
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'
  CYAN=$'\033[36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  MAGENTA=$'\033[35m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; CYAN=""; GREEN=""; YELLOW=""; MAGENTA=""; RED=""; RESET=""
fi

# Presentation helpers
banner() {
  echo
  echo "${BOLD}${CYAN}======================================================================${RESET}"
  echo "${BOLD}${CYAN}  $*${RESET}"
  echo "${BOLD}${CYAN}======================================================================${RESET}"
  sleep "$PACE"
}

step() {
  echo
  echo "${BOLD}${MAGENTA}>> $*${RESET}"
  sleep "$PACE"
}

say() {
  echo "${DIM}# $*${RESET}"
  sleep "$PACE"
}

# Print + run a command. Variable assignments persist (eval runs in caller).
cmd() {
  echo "${YELLOW}\$${RESET} $1"
  sleep "$PACE"
  eval "$1"
  echo
  sleep "$PACE"
}

# API helper (used inside cmd strings)
api() {
  local method="$1" path="$2"; shift 2
  curl -sS -X "$method" "$HOST$path" \
    -H "Content-Type: application/json" \
    ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
    "$@"
}

# Reseed (called by --reseed flag or 'reset' subcommand)
do_reseed() {
  banner "Reseeding Oscorp demo tenant"
  say "Drops oscorp + cascades + rebuilds with a fixed RNG seed."
  cmd 'cd "$REPO_ROOT" && uv run python -m scripts.seed_demo_tenant'
}

# Parse args
RESEED=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --reseed) RESEED=1 ;;
    *)        ARGS+=("$arg") ;;
  esac
done

# Honour --reseed before any walk
if [ "$RESEED" = "1" ]; then
  do_reseed
fi

# Preflight
preflight() {
  banner "Preflight"
  say "Confirm backend is reachable at $HOST."
  if ! curl -sS -m 3 "$HOST/health" >/dev/null 2>&1; then
    echo "${RED}Backend unreachable at $HOST. Start it (uv run uvicorn app.main:app) and re-run.${RESET}"
    exit 1
  fi
  cmd "curl -sS $HOST/health | jq"
}

# Walk A
walk_a() {
  banner "WALK A - MoM Budget Allocation (Phase 4 flow)"
  say "Persona: Otto Octavius (MANAGER_OF_MANAGERS, Engineering)."

  step "1. Sign in as Otto and grab the access token"
  cmd 'TOKEN=$(api POST /auth/login -d "{\"email\":\"mom1@oscorp.example.com\",\"password\":\"'"$DEMO_PASSWORD"'\"}" | jq -r .data.access_token); echo "TOKEN=...${TOKEN: -16}"'

  step "2. Read the active compensation cycle"
  cmd 'CYCLE_ID=$(api GET /comp-cycles/active | jq -r .data.id); api GET /comp-cycles/active | jq .data'

  step "3. Pull Otto's budget allocation (the left-panel data)"
  cmd 'ALLOC=$(api GET "/comp-cycles/$CYCLE_ID/my-budget-allocation"); ALLOC_ID=$(echo "$ALLOC" | jq -r .data.id); echo "$ALLOC" | jq ".data | {id, status, total_pool, strategic_reserve, budget_for_allocation, jvre_reserve}"'

  step "4. Set strategic reserve to 0 - full pool flows downstream"
  cmd 'api PUT "/comp-cycles/$CYCLE_ID/my-budget-allocation" -d "{\"strategic_reserve\": 0}" | jq ".data | {strategic_reserve, budget_for_allocation}"'

  step "5. Click 'Allocate Budget' - JVRE materializes one line per direct report"
  cmd 'LINES=$(api POST "/budget-allocations/$ALLOC_ID/align-with-jvre"); echo "$LINES" | jq ".data.items[] | {recipient_name, allocated_amount, jvre_rec_amount, criticality}"'

  step "6. Quick-edit the first line down by 5%"
  cmd 'LINE_ID=$(echo "$LINES" | jq -r ".data.items[0].id"); NEW_AMT=$(echo "$LINES" | jq ".data.items[0].allocated_amount | tonumber * 0.95"); api PUT "/budget-allocations/$ALLOC_ID/lines/$LINE_ID" -d "{\"allocated_amount\": $NEW_AMT}" | jq ".data.items[0] | {recipient_name, allocated_amount, base_pool, variable_pool, lti_grant_fmv_pool, reserve_pool}"'
  say "Sub-pools rescaled proportionally - the contract works as documented."

  step "7. Reset that line back to JVRE's recommendation"
  cmd 'api POST "/budget-allocations/$ALLOC_ID/lines/$LINE_ID/refresh-view" | jq ".data.items[0] | {recipient_name, allocated_amount}"'

  step "8. Submit the allocation - locks Otto, cascades PENDING children to each MoP"
  cmd 'api POST "/budget-allocations/$ALLOC_ID/submit" | jq ".data | {status, submitted_at, total_pool}"'

  step "9. Switch to Eddie Brock (MoP under Otto) - confirm the cascade"
  cmd 'TOKEN=$(api POST /auth/login -d "{\"email\":\"mop1-1@oscorp.example.com\",\"password\":\"'"$DEMO_PASSWORD"'\"}" | jq -r .data.access_token); api GET "/comp-cycles/$CYCLE_ID/my-budget-allocation" | jq ".data | {status, total_pool, parent_allocation_id}"'
  say "Eddie now has his own PENDING allocation, funded by Otto's submitted line."
}

# Walk B
walk_b() {
  banner "WALK B - MoP Pay Recommendation + MoM Pay Review (Phases 5 + 6)"
  say "Continues from Walk A - Eddie Brock writes recs, Otto reviews them."

  if [ -z "${CYCLE_ID:-}" ]; then
    say "(Standalone start - logging in fresh as Eddie Brock.)"
    cmd 'TOKEN=$(api POST /auth/login -d "{\"email\":\"mop1-1@oscorp.example.com\",\"password\":\"'"$DEMO_PASSWORD"'\"}" | jq -r .data.access_token); CYCLE_ID=$(api GET /comp-cycles/active | jq -r .data.id); echo "CYCLE_ID=$CYCLE_ID"'
  fi

  step "1. Eddie's subjects - recommendation_id is null because he has not started"
  cmd 'api GET "/comp-cycles/$CYCLE_ID/my-recommendations" | jq ".data.items[] | {subject_name, status, recommendation_id, jvre_rec_total, criticality}"'

  step "2. Walk every subject: open (or create) a rec, then save it"
  cmd 'for SUBJECT_ID in $(api GET "/comp-cycles/$CYCLE_ID/my-recommendations" | jq -r ".data.items[].subject_user_id"); do REC_ID=$(api POST "/comp-cycles/$CYCLE_ID/recommendations" -d "{\"subject_user_id\":\"$SUBJECT_ID\"}" | jq -r .data.id); api POST "/pay-recommendations/$REC_ID/save" >/dev/null; echo "  saved rec $REC_ID for subject ${SUBJECT_ID:0:8}"; done'

  step "3. Submit the batch - every DRAFT flips to SUBMITTED in one call"
  cmd 'api POST "/comp-cycles/$CYCLE_ID/my-recommendations/submit" | jq ".data.items[] | {subject_name, status}"'

  step "4. Switch back to Otto Octavius for review"
  cmd 'TOKEN=$(api POST /auth/login -d "{\"email\":\"mom1@oscorp.example.com\",\"password\":\"'"$DEMO_PASSWORD"'\"}" | jq -r .data.access_token); echo "Logged in as Otto."'

  step "5. Pull the review queue - grouped by submitter"
  cmd 'QUEUE=$(api GET /pay-recommendations/pending-review); echo "$QUEUE" | jq ".data.submitters[] | select(.member_count > 0) | {submitter_name, member_count, review_status}"'

  step "6. Pick Eddie's first recommendation to review"
  cmd 'REC_ID=$(echo "$QUEUE" | jq -r ".data.submitters[] | select(.submitter_name | test(\"Brock\")) | .members[0].recommendation_id"); api GET "/pay-recommendations/$REC_ID" | jq ".data | {subject_name, status, components: [.components[] | {component, jvre_rec_value, mgr_rec_value, mom_rec_value, final_value}]}"'

  step "7. Override BASE_PAY upward - first reviewer write flips to UNDER_REVIEW"
  cmd 'BASE_VAL=$(api GET "/pay-recommendations/$REC_ID" | jq ".data.components[] | select(.component==\"BASE_PAY\") | .mgr_rec_value | tonumber"); NEW_VAL=$(echo "$BASE_VAL * 1.05" | awk "{printf \"%.0f\", \$1}"); api PUT "/pay-recommendations/$REC_ID/components/BASE_PAY" -d "{\"value\":$NEW_VAL,\"reason_code\":\"ROLE_CRITICALITY_REVIEW\",\"role_criticality\":\"HIGH\",\"promotion_consideration\":false}" | jq ".data | {status, override}"'

  step "8. Drop a free-text annotation under Otto's name"
  cmd 'api POST "/pay-recommendations/$REC_ID/annotations" -d "{\"text\":\"Pay structure adjusted; promotion deferred to next cycle.\"}" | jq ".data | {actor_name, text, created_at}"'

  step "9. Mark REVISED with a structured note (sends back to Eddie for iteration)"
  cmd 'api POST "/pay-recommendations/$REC_ID/revise" -d "{\"annotation_text\":\"Final adjustment per role criticality review.\"}" | jq ".data | {status, annotation_count: (.annotations | length)}"'

  step "10. Approve - terminal state, writes RECOMMENDATION_APPROVED audit row"
  cmd 'api POST "/pay-recommendations/$REC_ID/approve" | jq ".data | {status, approved_at}"'

  banner "Done."
  say "Audit trail for this rec lives in audit_logs - query by request_id from the X-Request-ID header on any of the calls above."
  say "Run ./scripts/demo_walk.sh reset (or ./scripts/reset_demo.sh) before the next take."
}

# Dispatch
case "${ARGS[0]:-all}" in
  reset)
    # 'reset' subcommand: reseed only, no preflight, no walks.
    # If --reseed already ran above, don't double-seed.
    if [ "$RESEED" != "1" ]; then
      do_reseed
    fi
    ;;
  walk-a)   preflight; walk_a ;;
  walk-b)   preflight; walk_b ;;
  all|"")   preflight; walk_a; walk_b ;;
  *)        echo "Usage: $0 [reset|walk-a|walk-b|all] [--reseed]"; exit 2 ;;
esac
