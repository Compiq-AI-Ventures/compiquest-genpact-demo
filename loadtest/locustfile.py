"""Locust scenarios for CompIQCoreBe.

Three user classes are defined; Locust spawns them in roughly the
weights below so a single run gives a realistic mix:

* ``CasualUser``     (weight 6) — read-heavy: ``/auth/me`` and listing
                                  departments. Models a logged-in user
                                  navigating the app.
* ``AdminUser``      (weight 3) — mixed read/write: creates and renames
                                  departments. Models a tenant admin
                                  doing real provisioning work.
* ``LoginStormUser`` (weight 1) — hammers ``POST /auth/login`` to
                                  exercise the bcrypt path and the
                                  per-IP rate limiter. Expects to see
                                  a lot of 429s once the limiter fires
                                  (we treat 429 as a "successful"
                                  response so the failure-rate column
                                  stays meaningful).

Prerequisites
-------------
1. Run the schema migration::

       uv run alembic upgrade head

2. Seed the load-test tenant + users::

       uv run python -m scripts.seed_loadtest --users 200

3. Raise the login rate limit for the duration of the run, or the
   storm scenario (and most ``on_start`` logins) will be locked out
   within seconds::

       # PowerShell
       $env:RATE_LIMIT_LOGIN = "10000/minute"

       # bash
       export RATE_LIMIT_LOGIN="10000/minute"

4. Start the API. For load tests, drop ``--reload`` and add workers::

       uv run uvicorn app.main:app --workers 4 --no-access-log

   (``--reload`` forces single-worker and prints debug noise.)

Running
-------
Headless smoke (~1 minute, 10 users)::

    uv run locust -f loadtest/locustfile.py \
        --host http://127.0.0.1:8000 \
        --headless -u 10 -r 5 -t 1m

Headless baseline (~5 minutes, 100 users)::

    uv run locust -f loadtest/locustfile.py \
        --host http://127.0.0.1:8000 \
        --headless -u 100 -r 20 -t 5m \
        --csv loadtest/results/baseline

Web UI (interactive)::

    uv run locust -f loadtest/locustfile.py --host http://127.0.0.1:8000

What the credentials look like
------------------------------
The seed script provisions ``loadtest_user_001`` through
``loadtest_user_NNN`` under ``loadtest.example.com``, all sharing the
password ``loadtest-pass-12345``. Each VU picks one at random in
``on_start`` and reuses it for the session. Two VUs CAN collide on the
same email — that's realistic (one human in two browser tabs) and only
matters for the refresh-rotation path: the second VU's refresh after a
collision will return 401 because the first VU already rotated the
token. Set ``--users`` on the seed script high enough that collisions
are rare for your VU count.
"""

from __future__ import annotations

import contextlib
import random
import uuid

from locust import HttpUser, between, task

# Match scripts/seed_loadtest.py.
LOAD_TENANT_DOMAIN = "loadtest.example.com"
LOAD_PASSWORD = "loadtest-pass-12345"
SEEDED_USER_COUNT = 200


def _random_credential() -> str:
    n = random.randint(1, SEEDED_USER_COUNT)
    return f"loadtest_user_{n:03d}@{LOAD_TENANT_DOMAIN}"


# ---------------------------------------------------------------------------
# Mixin: login + refresh + token-aware requests
# ---------------------------------------------------------------------------
class _AuthMixin:
    """Login + refresh + a token-aware request wrapper.

    Subclasses inherit ``self.client`` from :class:`HttpUser` and gain
    ``self.access_token`` / ``self.refresh_token`` after ``on_start``.

    The ``authed`` helper retries once on 401 (refreshing the access
    token in between), which mirrors the contract a real client should
    implement — see the discussion in the docs on token lifecycle.
    """

    access_token: str | None = None
    refresh_token: str | None = None
    email: str | None = None

    def login(self) -> None:
        email = _random_credential()
        with self.client.post(  # type: ignore[attr-defined]
            "/auth/login",
            json={"email": email, "password": LOAD_PASSWORD},
            name="POST /auth/login",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                data = r.json()["data"]
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
                self.email = email
                r.success()
            else:
                # 429 here is interesting (rate limit fired) but still
                # a failure for the authenticated scenarios — they
                # need a token to do anything.
                self.access_token = None
                self.refresh_token = None
                r.failure(
                    f"login failed: {r.status_code} "
                    f"{(r.text or '')[:120]}"
                )

    def refresh(self) -> None:
        if not self.refresh_token:
            return self.login()
        with self.client.post(  # type: ignore[attr-defined]
            "/auth/refresh",
            json={"refresh_token": self.refresh_token},
            name="POST /auth/refresh",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                data = r.json()["data"]
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
                r.success()
            else:
                # Refresh failed — fall back to a fresh login next
                # request.
                self.access_token = None
                self.refresh_token = None
                r.failure(f"refresh failed: {r.status_code}")

    def authed(
        self,
        method: str,
        path: str,
        *,
        name: str | None = None,
        **kwargs,
    ):
        """Issue an authenticated request, refreshing on 401 once."""
        if not self.access_token:
            self.login()
            if not self.access_token:
                return None

        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.access_token}"

        # First attempt — wrap with catch_response so we can mark
        # 401-then-recover as a non-failure.
        with self.client.request(  # type: ignore[attr-defined]
            method,
            path,
            name=name or path,
            headers=headers,
            catch_response=True,
            **kwargs,
        ) as first:
            if first.status_code != 401:
                # Surface 4xx/5xx as failures, 2xx/3xx as success.
                if first.status_code >= 400:
                    first.failure(f"{method} {path} -> {first.status_code}")
                else:
                    first.success()
                return first
            # 401 — try a refresh and retry once.
            first.success()  # don't count the 401 itself as a failure

        self.refresh()
        if not self.access_token:
            return None
        headers["Authorization"] = f"Bearer {self.access_token}"
        return self.client.request(  # type: ignore[attr-defined]
            method,
            path,
            name=name or path,
            headers=headers,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# CasualUser — read-heavy
# ---------------------------------------------------------------------------
class CasualUser(_AuthMixin, HttpUser):
    """A logged-in user mostly clicking around the read surface."""

    weight = 6
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.login()

    @task(10)
    def view_me(self) -> None:
        self.authed("GET", "/auth/me", name="GET /auth/me")

    @task(5)
    def list_departments(self) -> None:
        self.authed("GET", "/departments", name="GET /departments")


# ---------------------------------------------------------------------------
# AdminUser — mixed read/write
# ---------------------------------------------------------------------------
class AdminUser(_AuthMixin, HttpUser):
    """A tenant admin: list, create, rename."""

    weight = 3
    wait_time = between(1.0, 3.0)

    created_dept_ids: list[str]

    def on_start(self) -> None:
        self.login()
        self.created_dept_ids = []

    @task(2)
    def list_departments(self) -> None:
        self.authed("GET", "/departments", name="GET /departments")

    @task(1)
    def create_department(self) -> None:
        # Codes must be unique within the tenant; UUID-derived suffix
        # makes collisions effectively impossible.
        code = f"D{uuid.uuid4().hex[:8].upper()}"
        response = self.authed(
            "POST",
            "/departments",
            name="POST /departments",
            json={"code": code, "name": f"Dept {code}"},
        )
        if response is not None and response.status_code == 201:
            # Body not JSON-decodable / missing the expected field —
            # skip; the request stat itself already recorded the
            # outcome, and missing this ID just means this VU has one
            # less department to rename later.
            with contextlib.suppress(KeyError, ValueError):
                self.created_dept_ids.append(response.json()["data"]["id"])

    @task(1)
    def rename_department(self) -> None:
        if not self.created_dept_ids:
            # Nothing of ours to rename yet — go create one instead.
            return
        dept_id = random.choice(self.created_dept_ids)
        self.authed(
            "PATCH",
            f"/departments/{dept_id}",
            name="PATCH /departments/[id]",
            json={"name": f"Renamed-{uuid.uuid4().hex[:6]}"},
        )


# ---------------------------------------------------------------------------
# LoginStormUser — hammers /auth/login
# ---------------------------------------------------------------------------
class LoginStormUser(HttpUser):
    """Hammers ``POST /auth/login`` to surface bcrypt cost and rate limit.

    Doesn't keep a token. Every iteration is a one-shot login.
    """

    weight = 1
    wait_time = between(0.1, 0.5)

    @task
    def login_then_drop(self) -> None:
        email = _random_credential()
        with self.client.post(
            "/auth/login",
            json={"email": email, "password": LOAD_PASSWORD},
            name="POST /auth/login (storm)",
            catch_response=True,
        ) as r:
            # 200 OK and 429 RATE_LIMITED are both expected outcomes
            # under load — the rate limiter firing IS what we wanted
            # to verify. Anything else (5xx, 401, etc.) is a real
            # failure.
            if r.status_code in (200, 429):
                r.success()
            else:
                r.failure(
                    f"unexpected status: {r.status_code} "
                    f"{(r.text or '')[:120]}"
                )
