"""Shared constants + helpers for the Genpact seed scripts."""

from __future__ import annotations

from pathlib import Path

from app.models.tenant import Tenant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Tenant identity ------------------------------------------------------------
TENANT_CODE = "genpact"
TENANT_NAME = "Genpact"
TENANT_DOMAIN = "genpact.com"
DEMO_PASSWORD = "genpact-demo-12345"
# Reporting currency for the tenant's transactional layer. Employees are
# paid in local currencies (INR / PLN / USD / MXN / PHP) but every pay
# recommendation, benchmark, budget line, and JVRE snapshot is stored — and
# rendered — in USD, converted from local via ``genpact_currency_master`` at
# seed time. This matches the "USD" toggle at the top of every screen.
DEFAULT_CURRENCY = "USD"

# Source workbooks (copied into the repo so the seed is self-contained) ------
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EMPLOYEE_WORKBOOK = _DATA_DIR / "Genpact_FA_Synthetic_Employee_Dataset.xlsx"
JVRE_WORKBOOK = _DATA_DIR / "JVRE_output.xlsx"

# The compensation cycle the JVRE_output recommendations target.
ACTIVE_CYCLE_FY = "FY2026"
# Fiscal years present in the employee master, oldest -> newest.
ALL_FYS = ("2023", "2024", "2025", "2026")


async def get_or_create_tenant(db: AsyncSession) -> Tenant:
    """Return the Genpact tenant, creating it if absent."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.code == TENANT_CODE))
    ).scalar_one_or_none()
    if tenant is not None:
        # Enforce the reporting currency on every re-seed — the transactional
        # layer is USD regardless of what an older seed may have written.
        if tenant.default_currency_code != DEFAULT_CURRENCY:
            tenant.default_currency_code = DEFAULT_CURRENCY
            await db.flush()
        return tenant
    tenant = Tenant(
        name=TENANT_NAME,
        code=TENANT_CODE,
        domain=TENANT_DOMAIN,
        default_currency_code=DEFAULT_CURRENCY,
    )
    db.add(tenant)
    await db.flush()
    return tenant
