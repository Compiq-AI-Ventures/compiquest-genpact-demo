"""Pydantic schemas for the P&L Head Executive Summary dashboard.

Org-wide, not BU-scoped (see ``docs/`` plan discussion) — every PNL_HEAD
caller gets the identical company-wide figures. Field names line up
1:1 with the verified derivations documented in
``pnl_dashboard_service.py``.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class PnlExecutiveSummaryResponse(BaseModel):
    beginning_base_cost: Decimal
    beginning_headcount: int

    increment_pct: float

    new_hire_total_cost: Decimal

    projected_new_base: Decimal

    external_compa_ratio: float
    internal_compa_ratio: float

    attrition_rate_fy24: float
    attrition_rate_fy25: float
    attrition_rate_fy26: float

    leadership_retention_fy24: float
    leadership_retention_fy26: float
    leadership_headcount: int

    backfill_cost: Decimal
    backfill_hire_count: int

    new_hire_median_cost: Decimal

    bullets: list[str]
