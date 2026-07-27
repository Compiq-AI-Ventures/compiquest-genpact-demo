#!/usr/bin/env bash
# seed_review_state.sh — Drive the system to "MoM Pay Review ready" state.
#
# After this runs:
#   - Otto's allocation is SUBMITTED (cascades to all 4 MoPs)
#   - Eddie's recs are all SUBMITTED (visible in Otto's pending-review queue)
#
# Idempotent — safe to re-run between recording takes. Skips any stage
# that's already been completed.
#
# Usage:
#   ./scripts/seed_review_state.sh                 # full reseed + drive
#   SKIP_RESEED=1 ./scripts/seed_review_state.sh   # skip reseed if you
#                                                    just want to re-drive
#                                                    state without losing
#                                                    other tenant data
#   HOST=http://stage:8000 ./scripts/seed_review_state.sh
#
# Requires bash, curl, jq. Run from Git Bash on Windows.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-http://127.0.0.1:8000}"
DEMO_PASSWORD="oscorp-demo-12345"
SKIP_RESEED="${SKIP_RESEED:-0}"

api() {
  local method="$1" path="$2"; shift 2
  curl -sS -X "$method" "$HOST$path" \
    -H "Content-Type: application/json" \
    ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
    "$@"
}

# Stage 1: clean state ────────────────────────────────────────────────────
if [ "$SKIP_RESEED" = "1" ]; then
  echo "→ SKIP_RESEED=1: not reseeding"
else
  echo "→ Reseeding Oscorp tenant"
  bash "$REPO_ROOT/scripts/reset_demo.sh"
fi

# Stage 2: Otto submits ───────────────────────────────────────────────────
echo "→ Logging in as Otto (MoM)"
TOKEN=$(api POST /auth/login -d "{
  \"email\": \"mom1@oscorp.example.com\",
  \"password\": \"$DEMO_PASSWORD\"
}" | jq -r .data.access_token)

CYCLE_ID=$(api GET /comp-cycles/active | jq -r .data.id)

OTTO=$(api GET "/comp-cycles/$CYCLE_ID/my-budget-allocation")
OTTO_ID=$(echo "$OTTO" | jq -r .data.id)
OTTO_STATUS=$(echo "$OTTO" | jq -r .data.status)

if [ "$OTTO_STATUS" = "PENDING" ]; then
  echo "  align-with-jvre"
  api POST "/budget-allocations/$OTTO_ID/align-with-jvre" > /dev/null
  echo "  submit"
  api POST "/budget-allocations/$OTTO_ID/submit" > /dev/null
  echo "  ✓ Otto SUBMITTED"
else
  echo "  ✓ Otto already $OTTO_STATUS — skipping"
fi

# Stage 3: Eddie creates + saves + submits ───────────────────────────────
echo "→ Logging in as Eddie (MoP)"
TOKEN=$(api POST /auth/login -d "{
  \"email\": \"mop1-1@oscorp.example.com\",
  \"password\": \"$DEMO_PASSWORD\"
}" | jq -r .data.access_token)

# Count subjects whose rec hasn't been submitted yet
PENDING=$(api GET "/comp-cycles/$CYCLE_ID/my-recommendations" \
  | jq '[.data.items[] | select(.status == "PENDING" or .status == "DRAFT")] | length')

if [ "$PENDING" -gt 0 ]; then
  echo "  drafting + saving recs for $PENDING subjects"
  for SUB in $(api GET "/comp-cycles/$CYCLE_ID/my-recommendations" \
    | jq -r '.data.items[] | select(.recommendation_id == null) | .subject_user_id'); do
    RID=$(api POST "/comp-cycles/$CYCLE_ID/recommendations" -d "{
      \"subject_user_id\": \"$SUB\"
    }" | jq -r .data.id)
    api POST "/pay-recommendations/$RID/save" > /dev/null
  done

  echo "  batch submit"
  api POST "/comp-cycles/$CYCLE_ID/my-recommendations/submit" > /dev/null
  echo "  ✓ Eddie's recs SUBMITTED"
else
  echo "  ✓ Eddie's recs already submitted — skipping"
fi

# Stage 4: Show what Otto sees ───────────────────────────────────────────
echo
echo "→ Logging back in as Otto for final state"
TOKEN=$(api POST /auth/login -d "{
  \"email\": \"mom1@oscorp.example.com\",
  \"password\": \"$DEMO_PASSWORD\"
}" | jq -r .data.access_token)

echo
echo "✔ Ready for the MoM Pay Review walkthrough."
echo "  Otto's pending-review queue:"
api GET /pay-recommendations/pending-review \
  | jq '.data.submitters[] | select(.member_count > 0) | {submitter_name, member_count, review_status}'
