# CompIQ Backend — Working Context for Claude

This file is a handoff for any Claude session picking up work on this
repo. Read it once before answering the user; everything below is
ground truth as of the last session.

## What this project is

**CompIQ** — a multi-tenant compensation planning backend.

- **Backend repo (this one):** `Caludetry` — FastAPI + SQLAlchemy 2.x
  async + PostgreSQL (with one RLS-enabled table as a template) + Redis
  (JWT deny-list, with in-memory fallback for dev).
- **Frontend repo (sibling):** `Caludetry-frontend` — Vite + React 19 +
  TypeScript + TanStack Query + React Router + Zustand + Tailwind +
  Radix primitives. **Functional only** — built for API exploration, NOT
  pixel-perfect to the locked design. Not the demo asset.

## State of v0.1

Three primary persona flows are live and feature-complete:

| Phase | Flow | Status |
|---|---|---|
| 4 | MoM Budget Allocation (CFO → MoM → MoP cascade) | ✓ |
| 5 | MoP Pay Recommendation (per-IC components + batch submit) | ✓ |
| 6 | MoM Pay Review (queue → approve / revise / annotate) | ✓ |

Auth, RBAC, audit log, multi-tenancy, refresh-token rotation,
rate-limiting, error envelope — all done.

**v0.2 is deferred** until designs land for C-Suite / HR / HRBP / C&B
persona workspaces. Tracked as task #157 in the user's task list. Also
deferred: iQuest AI chat, currency switcher UI, promotion approval gate,
audit log query endpoint.

## Where the user is in their work

The user is **systematically walking the v0.1 APIs to find discrepancies
between the design and the implementation.** They'll surface findings as
they go — handle them one at a time, don't try to anticipate or batch.
They have a strong engineering background; assume good judgement, push
back when something looks risky or off-spec.

## Dev environment

Local stack (everything via Homebrew on Mac):

- PostgreSQL 16 (`brew services start postgresql@16`)
- Redis (`brew services start redis`)
- Python via `uv` (the toolchain pins Python 3.11 itself)
- Node 22 (frontend only)
- `jq` (used heavily in the walkthroughs)

Standard backend boot:

```bash
uv sync
createdb compiqcorebe_db && createdb compiqcorebe_test
cp .env.example .env  # then edit DATABASE_URL, JWT_SECRET, REDIS_URL, CORS_ALLOW_ORIGINS
uv run alembic upgrade head
uv run python -m scripts.seed_demo_tenant
uv run uvicorn app.main:app --reload   # http://127.0.0.1:8000  /docs for Swagger
```

Super-admin bootstrap (one-shot, idempotent):

```bash
export INIT_SUPER_ADMIN_EMAIL=... INIT_SUPER_ADMIN_PASSWORD=...
uv run compiqcorebe bootstrap-super-admin
```

See `README.md` for full setup + architecture notes.

## Key reference files

| File | Purpose |
|---|---|
| `docs/specs/jvre_workspace_v0.1.md` | Full v0.1 spec |
| `docs/specs/jvre_workspace_v0.1_api.md` | API integration guide (the contract) |
| `docs/walkthroughs/mom_budget_iterative*.md` | MoM Budget Allocation walks (bash / PowerShell / curl / Swagger) |
| `docs/walkthroughs/mop_pay_recommendations*.md` | MoP Pay Recommendation walks (same four flavors + Mermaid diagrams) |
| `docs/walkthroughs/mom_pay_review*.md` | MoM Pay Review walks (same four flavors) |
| `scripts/reset_demo.sh` | Drop + reseed oscorp (idempotent, fixed RNG) |
| `scripts/seed_review_state.sh` | Reset + drive Otto's submit + Eddie's submit (state ready for MoM Pay Review) |
| `scripts/seed_demo_tenant.py` | The actual seed logic |
| `README.md` | Dev setup + architecture overview |

The **bash** walkthrough flavor is canonical; PowerShell / curl / Swagger
are translations. PowerShell is now legacy (was for Windows) — user is
on Mac.

## Demo personas (oscorp tenant, password `oscorp-demo-12345`)

| Email | Persona | Role | What they see |
|---|---|---|---|
| `cfo@oscorp.example.com` | Norman Osborn | CFO | Root allocation pre-submitted; workspace UI lands in v0.2 |
| `chro@oscorp.example.com` | Liz Allan | CHRO | Read-only; workspace UI lands in v0.2 |
| `mom1@oscorp.example.com` … `mom4@` | Otto Octavius, Curt Connors, Quentin Beck, Adrian Toomes | MANAGER_OF_MANAGERS | Budget Allocation + Pay Review |
| `mop1-1@oscorp.example.com` … `mop4-4@` | 16 named villains/antiheroes | MANAGER (MoP) | Pay Recommendations |

ICs (88 of them) are seeded but don't have a self-service workspace in
v0.1 — only show up as subjects in MoP/MoM screens.

## Database — quick map (17 tables)

Identity: `tenants`, `users`, `roles`, `user_roles`, `departments`
(the last is the one RLS-enabled table — template for extending).
Activity: `audit_logs`. Cycle scaffolding: `compensation_cycles`,
`reporting_relationships`. Reference: `jvre_snapshots`,
`market_benchmarks`, `compensation_history`. Budget:
`budget_allocations`, `budget_allocation_lines`. Pay rec:
`pay_recommendations`, `pay_recommendation_components`,
`pay_recommendation_overrides`, `pay_recommendation_annotations`.

JWT deny-list and refresh tokens are **NOT** in Postgres — they live in
Redis (with in-memory fallback). Money is `Numeric(18,2)` with
`currency_code` denormalised onto every monetary row.

## User's preferred shell setup (Mac, zsh)

The user has these helpers in `~/.zshrc` (paraphrased — actual snippet
in chat history of the prior session). Naming uses `COMPIQ_API` not
`HOST` because zsh reserves `$HOST` for the machine hostname:

```zsh
export COMPIQ_API="${COMPIQ_API:-http://127.0.0.1:8000}"

api() {
    local method=$1 path=$2; shift 2
    if [[ -n $TOKEN ]]; then
        curl -sS -X "$method" "${COMPIQ_API}${path}" -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" "$@"
    else
        curl -sS -X "$method" "${COMPIQ_API}${path}" -H 'Content-Type: application/json' "$@"
    fi
}

compiq-login() {
    local localpart=${1:?usage: compiq-login <localpart>}
    local email="${localpart}@oscorp.example.com"
    local resp
    resp=$(curl -sS -X POST "${COMPIQ_API}/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$email\",\"password\":\"oscorp-demo-12345\"}")
    export TOKEN=$(echo "$resp" | jq -r '.data.access_token // empty')
    [[ -z $TOKEN ]] && { echo "login failed: $resp" >&2; return 1; }
    export CYCLE_ID=$(api GET /comp-cycles/active | jq -r '.data.id // empty')
    echo "logged in as $email"
    echo "TOKEN=...${TOKEN: -16}"
    echo "CYCLE_ID=$CYCLE_ID"
}
```

A typical session becomes `compiq-login mom1` then `api GET /comp-cycles/$CYCLE_ID/my-budget-allocation | jq .data`.

## Open thread (likely still unresolved)

The user's first `compiq-login mom1` on Mac succeeded in getting a TOKEN
but the secondary `api GET /comp-cycles/active` call inside the function
errored with `api:3: command not found: curl`. The login curl itself
worked (TOKEN was returned), so curl is on PATH — almost certainly the
function body got mangled when pasted (smart quotes from chat client, or
line-continuation backslash dropped). Diagnosis steps suggested but not
yet executed by the user:

1. `which curl` — confirm PATH
2. `functions api` — inspect what zsh actually parsed
3. Re-paste a single-line-curl version of the function (shown in chat)

When the user starts the Mac Claude session, this is probably what
they'll resume from. Confirm it's fixed before diving into walkthroughs.

## Known gotchas to flag during walks

These have tripped the user before — call them out proactively:

- **`POST /budget-allocations/{id}/align-with-jvre` is destructive on
  every call.** First call materializes lines from JVRE; subsequent
  calls reset ALL line overrides. To inspect lines without mutating,
  use `GET /budget-allocations/{id}/lines`. The walkthroughs mention
  this but the warning is too quiet — flag it before they retry.
- **`jvre_alignment_tolerance` is display-only.** It's on the cycle row
  but not enforced anywhere — pure frontend hint for the "JVRE Aligned"
  badge. Submit doesn't gate on it. User confirmed this is the desired
  behavior in v0.1.
- **`REVISED` recommendations are reviewer-only-editable.** Per v0.1,
  once a rec is submitted, the actor (MoP) can't re-edit. `REVISED` is
  the reviewer's iteration marker, not a hand-off back to actor.
- **The seed pre-submits the CFO's allocation** so MoMs have funded
  pools. MoP/Review walks have their own prereq: run
  `./scripts/seed_review_state.sh` to also drive Otto's submit + Eddie's
  submit in one shot.

## Backlog (for context, not to act on)

Open tasks from the user's task list (not to be tackled unless they
ask):

- #142: audit-row write amplification on read endpoints
- #143: DB connection pool sizing + Postgres max_connections
- #144: bcrypt cost factor — env-gated for dev vs prod
- #157: v0.2 spec rev (folds in C-Suite, HR, HRBP, C&B)

## Working style the user expects

- Terse, dense prose over bullet-heavy summaries
- Edit files in the repo directly rather than dumping code in chat for
  manual copying
- When proposing changes, show enough diff to understand the change —
  don't restate whole files
- One focused clarifying question rather than guessing if scope is
  unclear
- Push back when something looks risky or off-spec
- Don't apologise unnecessarily; own mistakes and move on
- Trust the user's engineering judgement

## What the user is NOT doing right now

- Building new features
- The React frontend (it's there, may even run, but not the focus)
- The demo (cancelled — frontend wasn't pixel-perfect enough)
- v0.2 work (waiting on designs)
