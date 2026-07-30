"""Pydantic schemas for the P&L Head Executive Summary dashboard.

Org-wide, not BU-scoped — every PNL_HEAD caller gets the identical
company-wide figures. Field names line up 1:1 with the KPI derivations
documented in ``pnl_dashboard_service.py``.

The cycle spans three fiscal years: ``prev_fy`` (comparison base),
``fy`` (the actuals everything is measured on) and ``projected_fy``
(the forward-looking projection).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class PnlExecutiveSummaryResponse(BaseModel):
    fy_label: str
    prev_fy_label: str
    projected_fy_label: str

    # 1. Beginning base cost
    beginning_base_cost: Decimal
    beginning_headcount: int
    beginning_base_cost_prev: Decimal
    beginning_base_cost_delta: Decimal
    beginning_base_cost_delta_pct: float

    # 2. Increment
    increment_pct: float
    increment_amount: Decimal

    # 3. New hire cost (total = backfill + net new)
    new_hire_total_cost: Decimal
    new_hire_count: int
    backfill_cost: Decimal
    backfill_count: int
    net_new_hire_cost: Decimal
    net_new_hire_count: int

    # 4. Projected new base
    projected_new_base: Decimal
    projected_headcount: int
    effective_increment_pct: float
    wage_inflation_pct: float

    # 5. Attrition
    attrition_rate: float
    attrition_rate_prev: float
    attrition_delta_pp: float
    attrition_additional_cost: Decimal

    # 6. Leadership retention
    leadership_retention: float

    # 7. New hire median cost (incoming vs outgoing base pay)
    new_hire_median_cost: Decimal

    bullets: list[str]
