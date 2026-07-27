"""Tests for ``app.core.config.Settings`` startup validation.

These are pure-Python tests — no DB, no client, no event loop. They
guard the fail-fast contract that production deployments must not
boot with the development sentinel ``JWT_SECRET_KEY``.

The validator is part of the "ten things that must never break"
list: a misconfigured production deploy that signs JWTs with a
publicly-known secret is a credential-stuffing buffet, so we keep
a regression test pinned right next to it.
"""

from __future__ import annotations

import pytest
from app.core.config import _INSECURE_DEV_JWT_SECRET, Settings


# ---------------------------------------------------------------------------
# The bad path: production env + dev sentinel → ValueError
# ---------------------------------------------------------------------------
def test_production_rejects_insecure_dev_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Booting with ENVIRONMENT=production while still using the dev
    JWT sentinel must fail-fast with a clear error."""
    # Make sure no .env entry overrides what we're passing in.
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(
            environment="production",
            jwt_secret_key=_INSECURE_DEV_JWT_SECRET,
            _env_file=None,  # type: ignore[call-arg]
        )


def test_production_rejects_default_jwt_secret_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same guard, exercised via the env-var path (which is how the
    misconfiguration would actually arrive in a real deploy)."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Counter-examples: the validator must NOT fire when not in production,
# or when a real secret is supplied.
# ---------------------------------------------------------------------------
def test_development_with_dev_secret_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dev sentinel is fine in development — that's the whole
    point of having one."""
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    settings = Settings(
        environment="development",
        jwt_secret_key=_INSECURE_DEV_JWT_SECRET,
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.is_production is False
    assert settings.jwt_secret_key == _INSECURE_DEV_JWT_SECRET


def test_staging_with_dev_secret_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``production`` triggers the guard — staging tolerates the
    sentinel so dev → staging promotion doesn't require rotating
    secrets twice."""
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    settings = Settings(
        environment="staging",
        jwt_secret_key=_INSECURE_DEV_JWT_SECRET,
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.is_production is False


def test_production_with_strong_secret_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator only refuses the *sentinel*. A real secret must
    pass through cleanly."""
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    strong = "this-is-a-real-secret-not-the-sentinel-9f3c1a"
    settings = Settings(
        environment="production",
        jwt_secret_key=strong,
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.is_production is True
    assert settings.jwt_secret_key == strong
