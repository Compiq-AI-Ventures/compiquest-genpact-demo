# Deployment — CompIQ Backend (Genpact instance)

This file has two parts:

1. **[Run it on your own computer](#part-1--run-it-on-your-own-computer-beginner-friendly)** —
   a step-by-step guide for anyone setting this up for the first time,
   no prior experience assumed.
2. **[Production deployment](#part-2--production-deployment)** — the
   condensed checklist for deploying to a real server.

---

## Part 1 — Run it on your own computer (beginner-friendly)

By the end of these steps you'll have the backend running on
`http://localhost:8000` with sample data already loaded, so you can log
in and click around immediately.

### Step 0 — Install the tools you need

Check each one before installing — you might already have it.

| Tool | Check if you have it | If not, install it |
|---|---|---|
| **Git** | `git --version` | [git-scm.com/downloads](https://git-scm.com/downloads) |
| **Python 3.11 or newer** | `python3 --version` | [python.org/downloads](https://www.python.org/downloads/) |
| **uv** (manages Python packages for this project) | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (Mac/Linux) — or see [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) for Windows |
| **PostgreSQL 15+** (the database) | `psql --version` | Mac: `brew install postgresql@15` · Windows/Linux: [postgresql.org/download](https://www.postgresql.org/download/) |

You don't need Redis for local use — it's optional and only matters
when running multiple copies of the backend at once (see Part 2).

### Step 1 — Get the code

```bash
git clone https://github.com/Compiq-AI-Ventures/compiquest-genpact-demo.git
cd compiquest-genpact-demo
```

### Step 2 — Install the project's dependencies

```bash
uv sync
```

This reads the project's dependency list and creates a private
`.venv` folder with everything installed — it won't touch anything
else on your machine.

### Step 3 — Start PostgreSQL and create a database

Make sure PostgreSQL is running:

- **Mac (installed via brew):** `brew services start postgresql@15`
- **Windows/Linux:** PostgreSQL usually starts itself as a background
  service after installation. If unsure, open "Services" (Windows) or
  run `sudo systemctl start postgresql` (Linux).

Then create an empty database for this project:

```bash
psql -U postgres -c "CREATE DATABASE \"compiquest-demo\";"
```

If it asks for a password and you don't know it, the default local
Postgres user is usually `postgres` with no password, or password
`postgres` — try `psql -U postgres` first and see what happens.

### Step 4 — Create your `.env` file

This file holds your local settings (database connection, secret
keys, etc.). Copy the example and you're done — the defaults already
match what Step 3 created:

```bash
cp .env.example .env
```

Open `.env` in any text editor and check that this line matches the
database you created in Step 3 (it should, by default):

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/compiquest-demo
```

If your Postgres username/password is different, edit this line to
match: `postgresql+asyncpg://USERNAME:PASSWORD@localhost:5432/compiquest-demo`.

Everything else in `.env` already has sensible defaults for local use
— you don't need to touch anything else to get started.

### Step 5 — Build the database tables

```bash
uv run alembic upgrade head
```

This creates all the tables the app needs. You'll only see it print a
few lines and finish — that's normal, it means it worked.

### Step 6 — Load sample data

This fills the database with a realistic demo company (employees,
managers, pay cycles, etc.) so you have something to look at
immediately:

```bash
./scripts/reset_demo.sh
```

(If that command says "permission denied", run
`chmod +x scripts/reset_demo.sh` once, then try again.)

### Step 7 — Start the server

```bash
uv run uvicorn app.main:app --reload
```

Leave this running in its own terminal window. You should see a line
like `Uvicorn running on http://127.0.0.1:8000`.

### Step 8 — Check it worked

Open **http://localhost:8000/docs** in your browser — you should see
an interactive API documentation page. If that loads, the backend is
running correctly.

To confirm the database connection specifically is healthy too, open
a **second** terminal (leave the server running in the first one) and
run:

```bash
curl http://localhost:8000/health/db
```

You should see `{"status":"healthy","database":"connected",...}`.

### Connecting the frontend

If you're also running the CompIQ frontend locally, point it at
`http://localhost:8000` (or `/api` if it's configured to proxy — check
that project's own setup docs). Demo login credentials are printed by
the seed script in Step 6, or ask whoever shared this repo with you.

### Troubleshooting

| Problem | Likely fix |
|---|---|
| `uv: command not found` | Close and reopen your terminal after installing uv, or restart your computer. |
| `psql: command not found` | PostgreSQL's `bin` folder isn't on your PATH — reinstall and check "Add to PATH", or find `psql` in your Postgres install folder and run it with its full path. |
| `connection refused` when running any `uv run` database command | PostgreSQL isn't running — see Step 3. |
| `password authentication failed` | Your `.env` username/password doesn't match your local Postgres. Fix the `DATABASE_URL` line in `.env` (Step 4). |
| `relation "..." does not exist` | You skipped Step 5 (or it failed) — run `uv run alembic upgrade head` again. |
| Server starts but the frontend shows no data | You skipped Step 6 — run `./scripts/reset_demo.sh`. |
| Anything else | Check the terminal running `uvicorn` for an error message — it usually names the exact problem. |

### Starting fresh

If you ever want to wipe the sample data and reload it clean (e.g.
after clicking around and submitting things), just re-run:

```bash
./scripts/reset_demo.sh
```

It's safe to run as many times as you like.

---

## Part 2 — Production deployment

Condensed checklist for a real server. Assumes familiarity with
environment variables, reverse proxies, and process managers.

### Requirements

- Python 3.11 (managed via `uv`)
- PostgreSQL 15+
- Redis (strongly recommended for anything multi-replica — see below)

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment

Copy `.env.example` → `.env` and set at minimum:

| Variable | Notes |
|---|---|
| `ENVIRONMENT` | `production` — disables `/docs`, `/redoc`, `/openapi.json` |
| `DATABASE_URL` | `postgresql+asyncpg://USER:PASSWORD@HOST:5432/DBNAME` |
| `JWT_SECRET_KEY` | Generate: `python -c "import secrets; print(secrets.token_urlsafe(64))"`. App refuses to boot in production with the dev sentinel value. |
| `REDIS_URL` | Required for multi-replica deployments — the JWT deny-list falls back to per-process memory otherwise, so a logout on one replica won't apply to traffic hitting another. |
| `CORS_ALLOW_ORIGINS` | Explicit origin list. Never `*` with credentials enabled. |
| `RATE_LIMIT_STORAGE_URI` | `redis://host:6379/0` for multi-pod; otherwise limits are per-process. |

Leave `INIT_SUPER_ADMIN_*` unset for now — used once in step 4.

### 3. Apply migrations

```bash
uv run alembic upgrade head
```

### 4. Seed data (first deploy only)

```bash
uv run python -m scripts.seed_genpact_master_data   # analytics reference tables
uv run python -m scripts.seed_genpact_tenant         # tenant, users, roles, cycles, budgets, JVRE
```

Or `./scripts/reset_demo.sh` to run both idempotently.

### 5. Bootstrap the first admin

```bash
INIT_SUPER_ADMIN_EMAIL=ops@yourdomain.com \
INIT_SUPER_ADMIN_PASSWORD='<strong-random-password>' \
uv run compiqcorebe bootstrap-super-admin
```

Idempotent — a no-op if a super-admin already exists. After this
succeeds, **unset `INIT_SUPER_ADMIN_*`** in your secret manager; they
are not consulted again. Subsequent admins go through
`POST /admin/users` / `POST /admin/tenants/{tenant_id}/users`.

### 6. Start the service

```bash
uv run uvicorn app.main:app --reload
```

Put a reverse proxy (nginx / ALB / etc.) in front for TLS termination.
Drop `--reload` in production — it's a dev convenience that watches
files for changes and restarts, which costs CPU for no benefit on a
server.

### 7. Verify

```bash
curl https://your-host/health/db
```

Should return `{"status":"healthy","database":"connected",...}`. Also
confirm a real login succeeds (`POST /auth/login`) and that
`/docs`/`/redoc`/`/openapi.json` return 404 (they're disabled outside
`development`, confirming `ENVIRONMENT=production` took effect).

### Rollback

```bash
uv run alembic downgrade -1
```

### Notes / gotchas

- `AWS_REGION` / `BEDROCK_MODEL_ID` / AWS credentials are only needed
  if iQuest AI (rationale streaming, executive summaries, chat) is
  enabled for this deployment; otherwise leave blank. `AI_PROVIDER`
  in `app/core/config.py` is the single switch for every LLM call site
  — `bedrock` or `ollama`.
- bcrypt cost factor and DB pool sizing (`DB_POOL_SIZE`,
  `DB_MAX_OVERFLOW`) are dev-tuned defaults — raise both for
  production load (see `README.md` → Known follow-ups).
- `POST /budget-allocations/{id}/align-with-jvre` is destructive on
  every call after the first (resets line overrides) — not a
  deployment concern, but don't run it against prod data to "test".
- To reset a submitted budget allocation or pay recommendation back
  to editable for re-testing, use
  `uv run python -m scripts.reset_budget_allocation --email <email>`
  (see the script's own docstring for options).
