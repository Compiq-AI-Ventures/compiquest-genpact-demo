"""Well-known role codes, scopes, and seed data.

Roles themselves now live in the ``roles`` database table
(see :class:`app.models.role.Role`). This module is the *code-side*
index of the role codes the application ships with — used to:

* Reference roles symbolically in route declarations
  (``require_roles([RoleCode.HR, RoleCode.C_AND_B])``) so a rename
  surfaces in the IDE instead of in production logs.
* Seed the database (test fixture, migration) without sprinkling
  hardcoded strings around.

Scope semantics
---------------
* :data:`RoleScope.PLATFORM` — held across the whole platform; not
  bound to any tenant. Granted via ``user_roles`` rows with
  ``tenant_id IS NULL``.
* :data:`RoleScope.TENANT` — only meaningful inside a single tenant.
  Granted via ``user_roles`` rows with ``tenant_id`` set. The same
  user can hold the same role in different tenants — that's two rows.

The scope is stored on :class:`Role` (column ``scope``) so the policy
layer can later enforce scope-vs-grant consistency
(e.g., reject ``user_roles(role_id=<HR>, tenant_id=NULL)``).

Custom roles created at runtime (rows added to ``roles`` directly)
won't appear in :class:`RoleCode`, and that's fine — they continue
to work end-to-end via the DB. Code that needs to reference a custom
role by name should add a constant to :class:`RoleCode` first; that's
a code change, which is appropriate when you're declaring an
authorization rule.
"""

from __future__ import annotations

from enum import StrEnum


class RoleCode(StrEnum):
    """Built-in role codes shipped with the application.

    ``StrEnum`` makes each member a real ``str``
    (``RoleCode.HR == "HR"``), so values flow through Pydantic, JSON,
    and ``require_roles([...])`` interchangeably.
    """

    # ---- Platform-level roles (no tenant context) -----------------
    SUPER_ADMIN = "SUPER_ADMIN"
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    SUPPORT_ADMIN = "SUPPORT_ADMIN"

    # ---- Tenant-level roles (always bound to a tenant) ------------
    TENANT_ADMIN = "TENANT_ADMIN"
    CXO = "CXO"
    CHRO = "CHRO"
    CFO = "CFO"
    HR = "HR"
    HRBP = "HRBP"
    C_AND_B = "C_AND_B"
    MANAGER_OF_MANAGERS = "MANAGER_OF_MANAGERS"
    MANAGER = "MANAGER"
    IC = "IC"


class RoleScope(StrEnum):
    """Authorization scope of a :class:`Role`."""

    PLATFORM = "PLATFORM"
    TENANT = "TENANT"


# Convenience splits — used by tests and (later) by the policy layer
# when validating scope-vs-grant consistency.
PLATFORM_ROLES: frozenset[str] = frozenset(
    {RoleCode.SUPER_ADMIN, RoleCode.PLATFORM_ADMIN, RoleCode.SUPPORT_ADMIN}
)
TENANT_ROLES: frozenset[str] = frozenset(
    {
        RoleCode.TENANT_ADMIN,
        RoleCode.CXO,
        RoleCode.CHRO,
        RoleCode.CFO,
        RoleCode.HR,
        RoleCode.HRBP,
        RoleCode.C_AND_B,
        RoleCode.MANAGER_OF_MANAGERS,
        RoleCode.MANAGER,
        RoleCode.IC,
    }
)

# Plain-string view of every well-known role code. ``require_roles()``
# validates its arguments against this set so unknown built-in roles
# fail fast at startup.
ALL_ROLES: frozenset[str] = frozenset(r.value for r in RoleCode)


# Default role rows seeded into the ``roles`` table. Used by the
# Alembic migration and by the test-suite fixture. Tuple shape:
# (code, name, description, scope).
DEFAULT_ROLES: tuple[tuple[str, str, str, str], ...] = (
    # ---- Platform-level -------------------------------------------
    (
        RoleCode.SUPER_ADMIN.value,
        "Super Admin",
        "Unrestricted platform-wide administrator.",
        RoleScope.PLATFORM.value,
    ),
    (
        RoleCode.PLATFORM_ADMIN.value,
        "Platform Admin",
        "Operates the platform but not customer data.",
        RoleScope.PLATFORM.value,
    ),
    (
        RoleCode.SUPPORT_ADMIN.value,
        "Support Admin",
        "Customer-support engineer with cross-tenant read access.",
        RoleScope.PLATFORM.value,
    ),
    # ---- Tenant-level ---------------------------------------------
    (
        RoleCode.TENANT_ADMIN.value,
        "Tenant Admin",
        "Owner / administrator of a single tenant.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.CXO.value,
        "C-Suite Executive",
        "Top-of-house executive role within a tenant.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.CHRO.value,
        "Chief Human Resources Officer",
        "Tenant's senior HR executive; owns the compensation framework "
        "and has read access across the cycle.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.CFO.value,
        "Chief Financial Officer",
        "Tenant's senior finance executive; owns the root budget "
        "allocation that seeds every downstream pool.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.HR.value,
        "Human Resources",
        "Core HR function within a tenant.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.HRBP.value,
        "HR Business Partner",
        "HR partner aligned to a business unit.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.C_AND_B.value,
        "Compensation & Benefits",
        "Owns pay structure, benefits design, and benchmarking.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.MANAGER_OF_MANAGERS.value,
        "Manager of Managers",
        "Oversees other managers; second-line leadership.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.MANAGER.value,
        "Manager",
        "First-line people manager.",
        RoleScope.TENANT.value,
    ),
    (
        RoleCode.IC.value,
        "Individual Contributor",
        "Individual contributor; no direct reports.",
        RoleScope.TENANT.value,
    ),
)
