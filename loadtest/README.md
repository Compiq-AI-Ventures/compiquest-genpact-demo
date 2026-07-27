# Load test harness

Locust-based load test for CompIQCoreBe. Two purposes:

1. **Baseline** — establish a record of P50 / P95 / P99 latency per
   endpoint plus achievable RPS for the dev box. Every refactor going
   forward gets compared back to this baseline.
2. **Bottleneck discovery** — surface the obvious slow spots (bcrypt
   on login, audit-row writes, GUC + RLS overhead, connection-pool
   contention) before they bite in production.

This is NOT a capacity test. We're not sizing for a real workload yet.
Numbers from a single Windows dev box are not representative; rerun on
the Ubuntu server (or a real prod-like host) when you want to know
how big a deploy you actually need.

## Files

- `loadtest/locustfile.py` — three user classes (CasualUser, AdminUser,
  LoginStormUser) and a token-aware request helper.
- `../scripts/seed_loadtest.py` — provisions a `loadtest` tenant and
  N users with predictable credentials. Idempotent.

## One-time setup

```powershell
uv sync                       # installs Locust under [dependency-groups].dev
uv run alembic upgrade head   # bring the schema up to current head
```

## Per-run setup

1. **Seed the load-test tenant + users.** Idempotent; rerunning gives
   you the same starting state.

   ```powershell
   uv run python -m scripts.seed_loadtest --users 200
   ```

   Expected output: a one-line summary with the tenant id and the
   credential convention (`loadtest_user_001@loadtest.example.com` ..
   `loadtest_user_200@…`, password `loadtest-pass-12345`).

2. **Raise the login rate limit for this session.** The default
   `5/15minutes` will lock out almost everyone within seconds of
   spawning users.

   ```powershell
   $env:RATE_LIMIT_LOGIN = "10000/minute"
   $env:RATE_LIMIT_REGISTER = "10000/minute"   # only matters if you re-enable register
   ```

   (Skip this if you specifically want to test the rate limiter — in
   which case run `LoginStormUser` alone and expect a lot of 429s.)

3. **Start the API in load-test mode.** Drop `--reload`, add workers,
   silence the access log so latency numbers aren't muddied by
   per-request stdout.

   ```powershell
   uv run uvicorn app.main:app --workers 4 --no-access-log
   ```

   The structured `event: request` log line still fires per request,
   which is fine — those go through structlog and don't block uvicorn
   the way the access log can. If you want absolute quiet, also set
   `LOG_LEVEL=WARNING`.

## Running

### Smoke (1 minute, 10 users) — sanity check

Use this every time before a real run. It catches "did I forget to
seed?" / "did I forget to start the API?" / "is the network actually
talking?" in 60 seconds.

```powershell
uv run locust -f loadtest/locustfile.py `
    --host http://127.0.0.1:8000 `
    --headless -u 10 -r 5 -t 1m
```

Expect: ~0% failure rate, P95 latency comfortably under 200ms for
read endpoints, login slower (bcrypt-bound — see below).

### Baseline (5 minutes, 100 users) — the real run

```powershell
mkdir -Force loadtest\results | Out-Null
uv run locust -f loadtest/locustfile.py `
    --host http://127.0.0.1:8000 `
    --headless -u 100 -r 20 -t 5m `
    --csv loadtest/results/baseline
```

Produces `baseline_stats.csv`, `baseline_failures.csv`,
`baseline_stats_history.csv`, and `baseline_exceptions.csv`. Commit
the first one to the repo as the comparison anchor.

### Web UI (exploratory)

```powershell
uv run locust -f loadtest/locustfile.py --host http://127.0.0.1:8000
```

Open `http://localhost:8089`, set user count + spawn rate, watch the
real-time charts. Useful when you're trying to find the knee of the
curve.

## What to look for in the output

### Per-endpoint latency table

Locust prints a table at the end of the run. The columns that matter:

- **Median** — the typical user experience. Want this small.
- **95%ile, 99%ile** — the tail. Big tail = something's blocking.
- **req/s** — throughput. Compare across endpoints.
- **Fail #** — should be ~0 for everything except the storm scenario.

### Expected order-of-magnitude numbers (single Windows dev box, 4 workers)

These are ballparks; your numbers will differ. Use them to sanity-check
that nothing is wildly wrong.

- `GET /auth/me`               — P95 ~30-80ms
- `GET /departments`           — P95 ~50-150ms
- `POST /auth/login`           — P95 ~150-400ms (bcrypt-bound)
- `POST /auth/refresh`         — P95 ~30-100ms
- `POST /departments`          — P95 ~80-200ms (write + audit row)
- `PATCH /departments/[id]`    — P95 ~80-200ms

If `/auth/me` is over 200ms, something's wrong (probably connection
pool contention). If `/auth/login` is faster than 50ms, bcrypt isn't
running — check the configured rounds.

### Known failure modes (these are not bugs)

- **Storm scenario shows ~all 429s after the first 60 seconds.** The
  rate limiter is doing its job. Either bump `RATE_LIMIT_LOGIN` for
  the run, or run `LoginStormUser` alone.
- **Some refresh failures** when two VUs randomly pick the same email.
  Single-use refresh rotation means whoever rotates second loses.
  Bump `SEEDED_USER_COUNT` (and the `--users` arg to the seed script)
  if you care.
- **Audit-log writes get slower under sustained load.** Every login
  emits a `LOGIN_SUCCESS` (or `LOGIN_FAILED`) row via an independent
  session — that's write amplification. Move audit-log writes to a
  background queue (or to a different DB) before you scale.

## Interpreting bottlenecks

A short cheat-sheet of which Locust signal points at which fix:

| Signal | Likely cause | Fix |
|---|---|---|
| `POST /auth/login` P95 climbing with users | bcrypt rounds + uvicorn workers | More workers, OR drop bcrypt rounds in dev (not prod). |
| `GET /auth/me` P95 climbing with users | DB connection pool exhausted | Raise `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`. |
| 5xx rate spikes near the end | Connection pool timeouts | Same as above. |
| All endpoints slow simultaneously | Postgres at CPU/disk cap, or fsync bound | Check `pg_stat_statements`; consider `synchronous_commit=off` for dev. |
| Steady throughput, low CPU, no failures | You're bottlenecked on Locust itself | Run Locust on a different machine, or use distributed mode. |

## After the run

Save the `baseline_stats.csv` somewhere durable (commit it, or attach
to a project doc). The next refactor that touches a hot path should
re-run with the same parameters and the same seed count, and you'll
have an apples-to-apples comparison.

When you're done, restore the rate limits:

```powershell
Remove-Item Env:RATE_LIMIT_LOGIN
Remove-Item Env:RATE_LIMIT_REGISTER
```

Or just close the terminal — env vars don't persist across sessions.
