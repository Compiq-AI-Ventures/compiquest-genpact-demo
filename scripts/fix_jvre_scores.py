"""One-shot script: recalculate jvre_score from existing criticality +
market_position + promotion_readiness columns so the score is internally
consistent with the chip signals.

Does NOT touch recommended_base / recommended_variable / recommended_lti_fmv
because those are captured as historical snapshots in budget_allocation_lines
and changing them live would create a mismatch.

Run:
    uv run python -m scripts.fix_jvre_scores
"""

from __future__ import annotations

import asyncio
import os
import random
from decimal import Decimal

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

load_dotenv()

from app.models.jvre_snapshot import JvreSnapshot

# Mirrors the ranges in seed_demo_tenant.py.
_SCORE_RANGE: dict[tuple[str, str], tuple[float, float]] = {
    ("CRITICAL", "BELOW_MARKET"): (7.5, 9.8),
    ("MODERATE_HIGH", "MARKET_ALIGNED"): (4.0, 7.0),
    ("LOW_RISK", "ABOVE_MARKET"): (1.5, 4.5),
}


def _recalc_score(
    criticality: str | None,
    market_position: str | None,
    promotion_readiness: str | None,
    rng: random.Random,
) -> Decimal:
    key = (criticality or "", market_position or "")
    lo, hi = _SCORE_RANGE.get(key, (3.0, 6.0))
    if promotion_readiness == "READY":
        lo = min(lo + 0.8, hi)
    return Decimal(str(rng.uniform(lo, hi))).quantize(Decimal("0.01"))


async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    rng = random.Random(42)  # fixed seed for reproducibility

    async with AsyncSession(engine) as db:
        snapshots = list((await db.execute(select(JvreSnapshot))).scalars().all())
        print(f"Updating {len(snapshots)} jvre_snapshot rows …")

        for snap in snapshots:
            snap.jvre_score = _recalc_score(
                snap.criticality,
                snap.market_position,
                snap.promotion_readiness,
                rng,
            )

        await db.commit()
        print("Done.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
