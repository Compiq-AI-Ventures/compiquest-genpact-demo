# CompIQCoreBe

Backend service that powers **JVRE (Job Value Recommendation Engine)**.
Built on FastAPI, async SQLAlchemy 2.x, and PostgreSQL with row-level
security.

Current version is **v0.2** — single-tenant-per-user model. A user
belongs to exactly one tenant, or to none (a "platform user").
Multi-tenancy lives at the architecture layer, not at the user layer.

## Tech stack

- **Web** — FastAPI + Uvicorn (async)
- **Persistence** — SQLAlchemy 2.x async ORM, asyncpg driver,
  PostgreSQL 15+ with FORCE ROW LEVEL SECURITY on tenant-scoped tables
- **Migrations** — Alembic
- **Auth** — JWT HS256 (jose), bcrypt password hashing (passlib),
  refresh-token rotation, Redis-backed (or in-memory) deny-list
- **Validation** — Pydantic v2 + pydantic-settings
- **Observability** — structlog JSON logs, X-Request-ID propagation
- **Hardening** — slowapi rate limiting, CORS, security headers,
  domain-error envelope, audit log
- **Tests** — pytest + pytest-asyncio + httpx, coverage gated at 80%
- **Load tests** — Locust harness under `loadtest/`
- **Lint** — ruff (`E`, `W`, `F`, `I`, `B`, `C4`, `UP`, `N`, `SIM`,
  `RUF`)

## Setup

This project uses [UV](https://docs.astral.sh/uv/) for dependency
management.

### Install dependencies

```bash
uv sync
```

Installs runtime + dev deps and creates a `.venv` automatically.

### Configure environment

Copy `.env.example` → `.env` and set at least:

- `DATABASE_URL` — `postgresql+asyncpg://USER:PASSWORD@HOST:5432/compiqcorebe_db`
- `JWT_SECRET_KEY` — strong random value. Generate with
  `python -c "import secrets; print(secrets.token_urlsafe(64))"`. The
  app refuses to boot in production with the dev sentinel.
- `REDIS_URL` (optional) — enables a multi-process JWT deny-list. If
  unset, falls back to an in-process backend (single-instance only).
- `RATE_LIMIT_LOGIN`, `RATE_LIMIT_REGISTER` — slowapi format
  (`5/15minutes` defaults; raise for load tests).

### Apply the schema

```bash
uv run alembic upgrade head
```

The DB URL is read from `DATABASE_URL`; it is NOT duplicated in
`alembic.ini`.

### Run the development server

```bash
uv run uvicorn app.main:app --reload
```

API at <http://localhost:8000>.

- Interactive docs (Swagger UI): <http://localhost:8000/docs>
- Alternative docs (ReDoc): <http://localhost:8000/redoc>

## First deploy

CompIQCoreBe is enterprise-only — there is no public registration. The
first user (a platform `SUPER_ADMIN`) is created with a one-shot CLI
command. Every subsequent user goes through the admin API.

```bash
# 1. Apply the schema.
uv run alembic upgrade head

# 2. Create the first SUPER_ADMIN. Idempotent: a no-op if one exists.
INIT_SUPER_ADMIN_EMAIL=ops@example.com \
INIT_SUPER_ADMIN_PASSWORD='<strong-random-password>' \
uv run compiqcorebe bootstrap-super-admin

# 3. Boot the API.
uv run uvicorn app.main:app
```

After step 2, **unset** the `INIT_SUPER_ADMIN_*` variables in your
secret manager — they're not consulted again. Subsequent admins are
provisioned via `POST /admin/users` (platform) or
`POST /admin/tenants/{tenant_id}/users` (tenant) using the new
super-admin's bearer token.

## Architecture overview

### Single tenant per user

Every user row carries a nullable `tenant_id`:

- `tenant_id IS NULL` → platform user (operator / super admin / support).
- `tenant_id` set → tenant user, scoped to that tenant for life.

Email is unique **per tenant** (`UNIQUE (tenant_id, email) NULLS NOT DISTINCT`),
so the same email can exist in multiple tenants plus optionally as one
platform user.

### Login resolution chain

`POST /auth/login` resolves the target user in this order:

1. If the request supplied `tenant_code`, look up that tenant directly
   and find the user there. (Caller is explicitly naming a tenant.)
2. Otherwise, try the platform-user table first (`tenant_id IS NULL`).
3. On a miss, derive the tenant from the email's domain
   (`alice@acme.com` → tenant where `domain = 'acme.com'`) and look
   the user up there.

All failure modes collapse into a single `INVALID_CREDENTIALS` response
(no enumeration leak). Successful logins return an `access_token` +
`refresh_token` pair plus the access TTL in seconds.

### JWT lifecycle

- Access tokens are short-lived (`ACCESS_TOKEN_EXPIRE_MINUTES`, default
  30). Carry `sub`, `email`, `tenant_id`, `roles`, `jti`.
- Refresh tokens are longer-lived (`REFRESH_TOKEN_EXPIRE_DAYS`,
  default 14). Single-use rotation: on `POST /auth/refresh`, the
  presented refresh token is added to the deny-list and a fresh
  refresh+access pair is minted.
- `POST /auth/logout` adds the access token's `jti` to the deny-list.
  Subsequent calls with that token return 401.
- The deny-list backend is Redis when `REDIS_URL` is set, otherwise
  in-process. Production deployments **must** set `REDIS_URL`.

### Authorization model — role scopes

Roles live in the `roles` table and have a **scope**:

- **PLATFORM** scope — `SUPER_ADMIN`, `PLATFORM_ADMIN`,
  `SUPPORT_ADMIN`. Can be held only by platform users.
- **TENANT** scope — `TENANT_ADMIN`, `MANAGER`, `HR`, `C_AND_B`,
  `CXO`, etc. Can be held only by tenant users.

A user holds zero or more roles via the `user_roles` join table
(composite PK `user_id` + `role_id`). The `app/core/authorization.py`
module assembles a `RoleProfile` (tenant_id + platform_roles +
tenant_roles) on each request; route dependencies
(`require_platform_roles([...])`, `require_tenant_roles([...])`) use
that to allow or deny.

A platform user hitting a tenant-scoped endpoint gets 400
`TENANT_CONTEXT_REQUIRED` — platform admins cross tenant boundaries
via `/admin/*` endpoints with explicit tenant ids in the path.

### Tenant lifecycle

Tenants have a `status` column with three values:

- `ACTIVE` — normal operations.
- `SUSPENDED` — login still issues a token (no enumeration leak), but
  every protected call returns 403 `TENANT_INACTIVE`. Restorable.
- `DISABLED` — terminal. The status validator rejects any transition
  back to `ACTIVE` or `SUSPENDED`.

The admin endpoints (`/admin/tenants`, `/admin/tenants/{id}`) work
even on `SUSPENDED` and `DISABLED` tenants so operators can recover.

### Tenant data isolation (defense in depth)

Tenant-scoped tables (currently `departments`) are protected by:

1. **Repository filter** — every query carries an explicit
   `WHERE tenant_id = :tid`.
2. **GUC** — `app.current_tenant` is set per request via
   `get_tenant_scoped_db` (`SELECT set_config('app.current_tenant', :tid, false)`).
3. **Postgres RLS** — `ALTER TABLE … FORCE ROW LEVEL SECURITY` plus a
   policy that compares `tenant_id::text` to `current_setting('app.current_tenant', true)`.

Belt + suspenders: a missing repository filter is hidden by RLS; a
missing GUC blocks the query at the policy. Cross-tenant work by
platform admins uses `get_unrestricted_db` (sets
`app.platform_override = 'true'`) and is gated behind PLATFORM-scope
role grants.

### Audit log

`app/services/audit_log_service.py` exposes two entry points:

- `log_action(db, …)` — writes a row inside the caller's transaction.
  Used for success paths so the audit row commits or rolls back with
  the action it records.
- `log_action_independent(…)` — opens its own short-lived session.
  Used for failure paths (failed login, denied access) so the row
  survives the rollback that the raise will trigger.

Both are best-effort and never raise — a failure logs `audit_log_failed`
and the caller continues. Sensitive fields (passwords, tokens, full
request bodies) must never reach the metadata column; the module
docstring lists what to keep out.

## Project structure

```
.
├── app/
│   ├── main.py                        # FastAPI app + middleware + router wiring
│   ├── cli.py                         # bootstrap-super-admin operator CLI
│   ├── core/
│   │   ├── config.py                  # Pydantic Settings (env-driven)
│   │   ├── database.py                # async engine, session factory, Base
│   │   ├── exceptions.py              # DomainError + global handlers
│   │   ├── middleware.py              # request-ID, security headers
│   │   ├── logging.py                 # structlog JSON config
│   │   ├── rate_limit.py              # slowapi limiter
│   │   ├── token_denylist.py          # Redis or in-memory JWT deny-list
│   │   ├── roles.py                   # RoleCode enum + RoleScope + DEFAULT_ROLES
│   │   ├── authorization.py           # RoleProfile + is_authorized helpers
│   │   └── security.py                # password hashing + JWT signing
│   ├── models/
│   │   ├── user.py                    # User (single tenant_id, M2M roles)
│   │   ├── tenant.py                  # Tenant + TenantStatus
│   │   ├── role.py                    # Role (rows in roles table)
│   │   ├── user_role.py               # (user_id, role_id) join
│   │   ├── department.py              # First tenant-scoped business entity
│   │   └── audit_log.py
│   ├── repositories/                  # User / Tenant / Role / Department / AuditLog
│   ├── schemas/                       # auth / user / tenant / department / admin_user
│   ├── services/
│   │   ├── auth_service.py            # login + refresh + logout
│   │   ├── admin_user_service.py      # create_platform_user / create_tenant_user
│   │   ├── tenant_service.py          # create + list + update tenant
│   │   ├── department_service.py
│   │   └── audit_log_service.py
│   ├── dependencies/
│   │   ├── db_dependency.py           # get_db (UnitOfWork)
│   │   ├── scoped_db_dependency.py    # get_tenant_scoped_db / get_unrestricted_db
│   │   ├── auth_dependency.py         # get_current_user (JWT → User)
│   │   ├── tenant_dependency.py       # get_active_tenant_id
│   │   ├── role_dependency.py         # require_platform_roles / require_tenant_roles
│   │   └── admin_dependency.py        # require_admin_for_tenant (admin context)
│   ├── routers/
│   │   ├── auth_router.py             # /auth/{login,refresh,logout,me,…}
│   │   ├── admin_router.py            # /admin/users, /admin/tenants/{id}/users
│   │   ├── tenant_router.py           # /admin/tenants (CRUD)
│   │   └── department_router.py       # /departments (CRUD)
│   └── utils/
│       └── response_builder.py        # success_response / error_response envelopes
├── alembic/
│   ├── env.py                         # async + platform_override GUC
│   ├── script.py.mako
│   └── versions/                      # migration files (linear chain)
├── tests/
│   ├── conftest.py                    # async fixtures + RLS test role
│   ├── _helpers.py                    # create_platform_user / create_tenant_user / auth_headers
│   ├── test_auth_login.py
│   ├── test_auth_me.py
│   ├── test_auth_logout.py
│   ├── test_auth_refresh.py
│   ├── test_admin_create_user.py
│   ├── test_tenant_admin.py
│   ├── test_departments.py            # incl. RLS isolation proof
│   ├── test_audit_log.py
│   ├── test_rbac.py
│   ├── test_cli.py
│   ├── test_security.py
│   └── test_settings.py               # JWT-secret production guard
├── scripts/
│   └── seed_loadtest.py               # idempotent loadtest tenant + N users
├── loadtest/
│   ├── locustfile.py                  # CasualUser / AdminUser / LoginStormUser
│   └── README.md                      # run + interpret instructions
├── alembic.ini
├── pyproject.toml
├── .python-version
├── .env.example
├── .gitignore
└── README.md
```

## Development

### Lint and format with Ruff

```bash
uv run ruff check .                    # check for issues
uv run ruff check --fix .              # auto-fix
uv run ruff format .                   # format
```

### Run tests

A dedicated test database is required. Set `TEST_DATABASE_URL` once
in your shell — there is deliberately no fallback.

```bash
# PowerShell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/compiqcorebe_test"

# bash
export TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/compiqcorebe_test"
```

Then:

```bash
uv run pytest                          # full suite
uv run pytest -x --tb=short            # stop at first failure, terse traceback
uv run pytest --cov=app --cov-report=term-missing
```

The suite drops + recreates the schema once per session, then
truncates between tests. Coverage is gated at 80% (`fail_under` in
`pyproject.toml`).

### Database migrations (Alembic)

```bash
uv run alembic upgrade head            # apply pending migrations
uv run alembic current                 # show applied revision
uv run alembic revision --autogenerate -m "describe change"
uv run alembic downgrade -1            # roll back one migration
```

### Load tests

Locust harness under `loadtest/`. Use it to establish a baseline before
shipping a release and to compare against after a refactor.

```bash
# 1. Seed (idempotent — drops + recreates the loadtest tenant)
uv run python -m scripts.seed_loadtest --users 200

# 2. Raise login rate limit so on_start logins don't trip the limiter
export RATE_LIMIT_LOGIN="10000/minute"

# 3. Start API in load-test mode (no --reload, multi-worker)
uv run uvicorn app.main:app --workers 4 --no-access-log

# 4. Run a smoke (1 min, 10 VUs) then a baseline (5 min, 100 VUs)
uv run locust -f loadtest/locustfile.py \
    --host http://127.0.0.1:8000 \
    --headless -u 100 -r 20 -t 5m \
    --csv loadtest/results/baseline
```

See `loadtest/README.md` for the full run + interpret guide, expected
order-of-magnitude numbers, and a cheat-sheet for mapping Locust
signals to fixes.

## API surface

### Auth

- `POST /auth/login` — email + password (+ optional `tenant_code`).
  Returns `{access_token, refresh_token, token_type, expires_in}`.
- `POST /auth/refresh` — refresh token in body. Rotates both tokens.
- `POST /auth/logout` — bearer token in header. Adds `jti` to deny-list.
- `GET  /auth/me` — current user + tenant summary + flat role list.
- Various `/auth/{platform-admin,tenant-admin,admin,manager}-test`
  endpoints exist for RBAC integration testing.

### Admin

- `POST   /admin/users` — create a platform user (SUPER_ADMIN /
  PLATFORM_ADMIN only).
- `POST   /admin/tenants` — create a tenant + initial TENANT_ADMIN
  atomically.
- `GET    /admin/tenants` — paginated list (status filter,
  SUPPORT_ADMIN read access).
- `GET    /admin/tenants/{id}` — read one.
- `PATCH  /admin/tenants/{id}` — rename, change domain, status
  transition.
- `POST   /admin/tenants/{tenant_id}/users` — create a user in that
  tenant. Caller must be a platform admin or a TENANT_ADMIN of that
  tenant.

### Departments (tenant-scoped, RLS-protected)

- `POST   /departments` — create.
- `GET    /departments` — list (filtered by current tenant).
- `GET    /departments/{id}` — read one.
- `PATCH  /departments/{id}` — rename / re-describe.
- `DELETE /departments/{id}` — remove.

## Error envelope

Every non-2xx response (validation, domain error, HTTP exception,
unexpected error) is wrapped in a single shape:

```json
{
  "status": "fail",
  "error_code": "INVALID_CREDENTIALS",
  "message": "Invalid email or password.",
  "details": { ... }
}
```

`401`s carry `WWW-Authenticate: Bearer` (RFC 6750). `403`s carry the
`FORBIDDEN` code and write an `ACCESS_DENIED` audit row. Validation
errors (`422`) put the per-field issues in `details.errors`.

## Documentation

Three Word documents capture the deeper architecture and operator
reference; they live outside the repo (or under `docs/` if you
materialize them locally — `docs/` is gitignored):

- **Solution Document** — system overview, security model, deployment
  topology.
- **Developer Knowledge Document** — module-by-module deep dive.
- **How-To: Tenants & Users** — operator runbook for the common
  lifecycle tasks.

The README is the entry point; the .docx files are the reference.

## Known follow-ups

Tracked for the next round of hardening, in rough priority order:

1. **Audit-row write amplification** — every read-side request writes
   an independent audit row, doubling the connection-pool pressure.
   Consider sampling, async queueing, or removing read-side audits
   that aren't security-meaningful.
2. **DB connection pool sizing** — defaults of `5 + 10` per worker
   saturate around 60 concurrent VUs. Bump
   `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` and Postgres `max_connections`
   together.
3. **bcrypt cost factor** — gate by environment so dev runs at
   `rounds=10` while staging/prod stay at `rounds=12+`.
4. **First JVRE vertical slice** — the actual product. Platform is
   in place; the domain entities (jobs, compensation data, the
   recommendation engine itself) are next.

## License

Proprietary — all rights reserved.
