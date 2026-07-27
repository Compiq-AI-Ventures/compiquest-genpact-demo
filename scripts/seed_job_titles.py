"""Seed job titles and departments for Oscorp demo tenant users.

Run with:
    uv run python3 scripts/seed_job_titles.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionLocal
from sqlalchemy import text

# Identify the demo tenant by its stable ``code``. See the note on the
# same pattern in ``seed_departments.py`` — a hardcoded UUID here only
# ever worked on the author's machine because every ``seed_demo_tenant``
# run mints a fresh UUID.
TENANT_CODE = "oscorp"

USERS: dict[str, tuple[str, str]] = {
    # C-Suite
    "cfo@oscorp.example.com": ("Chief Financial Officer", "Finance"),
    "chro@oscorp.example.com": ("Chief HR Officer", "Human Resources"),
    # MoMs
    "mom1@oscorp.example.com": ("VP Engineering", "Engineering"),
    "mom2@oscorp.example.com": ("VP Product & Biotech", "Product / Biotech"),
    "mom3@oscorp.example.com": ("VP Cyber Security", "Cyber Security"),
    "mom4@oscorp.example.com": ("VP Quality Assurance", "Quality Assurance"),
    # MoPs under MoM1 — Engineering
    "mop1-1@oscorp.example.com": ("Engineering Manager", "Engineering"),
    "mop1-2@oscorp.example.com": ("Engineering Manager", "Engineering"),
    "mop1-3@oscorp.example.com": ("Engineering Manager", "Engineering"),
    "mop1-4@oscorp.example.com": ("Senior Engineering Manager", "Engineering"),
    # MoPs under MoM2 — Product / Biotech
    "mop2-1@oscorp.example.com": ("Product Manager", "Product / Biotech"),
    "mop2-2@oscorp.example.com": ("Senior Product Manager", "Product / Biotech"),
    "mop2-3@oscorp.example.com": ("Product Manager", "Product / Biotech"),
    "mop2-4@oscorp.example.com": ("Product Manager", "Product / Biotech"),
    # MoPs under MoM3 — Cyber Security
    "mop3-1@oscorp.example.com": ("Security Manager", "Cyber Security"),
    "mop3-2@oscorp.example.com": ("Security Manager", "Cyber Security"),
    "mop3-3@oscorp.example.com": ("Senior Security Manager", "Cyber Security"),
    "mop3-4@oscorp.example.com": ("Security Manager", "Cyber Security"),
    # MoPs under MoM4 — Quality Assurance
    "mop4-1@oscorp.example.com": ("QA Manager", "Quality Assurance"),
    "mop4-2@oscorp.example.com": ("QA Manager", "Quality Assurance"),
    "mop4-3@oscorp.example.com": ("Senior QA Manager", "Quality Assurance"),
    "mop4-4@oscorp.example.com": ("QA Manager", "Quality Assurance"),
    # ICs under MoM1 — Engineering (ic1-x-x)
    "ic1-1-1@oscorp.example.com": ("Staff Engineer", "Engineering"),
    "ic1-1-2@oscorp.example.com": ("Software Engineer", "Engineering"),
    "ic1-1-3@oscorp.example.com": ("Software Engineer", "Engineering"),
    "ic1-1-4@oscorp.example.com": ("Mid Engineer", "Engineering"),
    "ic1-2-1@oscorp.example.com": ("Junior Engineer", "Engineering"),
    "ic1-2-2@oscorp.example.com": ("Software Engineer", "Engineering"),
    "ic1-2-3@oscorp.example.com": ("Software Engineer", "Engineering"),
    "ic1-2-4@oscorp.example.com": ("Mid Engineer", "Engineering"),
    "ic1-2-5@oscorp.example.com": ("Senior Engineer", "Engineering"),
    "ic1-3-1@oscorp.example.com": ("Junior Engineer", "Engineering"),
    "ic1-3-2@oscorp.example.com": ("Software Engineer", "Engineering"),
    "ic1-3-3@oscorp.example.com": ("Software Engineer", "Engineering"),
    "ic1-3-4@oscorp.example.com": ("Senior Engineer", "Engineering"),
    "ic1-3-5@oscorp.example.com": ("Mid Engineer", "Engineering"),
    "ic1-3-6@oscorp.example.com": ("Senior Engineer", "Engineering"),
    "ic1-4-1@oscorp.example.com": ("Junior Engineer", "Engineering"),
    "ic1-4-2@oscorp.example.com": ("Mid Engineer", "Engineering"),
    "ic1-4-3@oscorp.example.com": ("Software Engineer", "Engineering"),
    "ic1-4-4@oscorp.example.com": ("Mid Engineer", "Engineering"),
    "ic1-4-5@oscorp.example.com": ("Mid Engineer", "Engineering"),
    "ic1-4-6@oscorp.example.com": ("Staff Engineer", "Engineering"),
    "ic1-4-7@oscorp.example.com": ("Staff Engineer", "Engineering"),
    # ICs under MoM2 — Product / Biotech (ic2-x-x)
    "ic2-1-1@oscorp.example.com": ("Senior Product Designer", "Product / Biotech"),
    "ic2-1-2@oscorp.example.com": ("Product Designer", "Product / Biotech"),
    "ic2-1-3@oscorp.example.com": ("Product Analyst", "Product / Biotech"),
    "ic2-1-4@oscorp.example.com": ("Junior Product Designer", "Product / Biotech"),
    "ic2-2-1@oscorp.example.com": ("Product Designer", "Product / Biotech"),
    "ic2-2-2@oscorp.example.com": ("Senior Product Designer", "Product / Biotech"),
    "ic2-2-3@oscorp.example.com": ("Product Analyst", "Product / Biotech"),
    "ic2-2-4@oscorp.example.com": ("Junior Product Designer", "Product / Biotech"),
    "ic2-2-5@oscorp.example.com": ("Product Designer", "Product / Biotech"),
    "ic2-3-1@oscorp.example.com": ("Biotech Researcher", "Product / Biotech"),
    "ic2-3-2@oscorp.example.com": ("Senior Researcher", "Product / Biotech"),
    "ic2-3-3@oscorp.example.com": ("Research Analyst", "Product / Biotech"),
    "ic2-3-4@oscorp.example.com": ("Biotech Researcher", "Product / Biotech"),
    "ic2-3-5@oscorp.example.com": ("Senior Researcher", "Product / Biotech"),
    "ic2-3-6@oscorp.example.com": ("Research Analyst", "Product / Biotech"),
    "ic2-4-1@oscorp.example.com": ("Product Analyst", "Product / Biotech"),
    "ic2-4-2@oscorp.example.com": ("Product Designer", "Product / Biotech"),
    "ic2-4-3@oscorp.example.com": ("Senior Product Designer", "Product / Biotech"),
    "ic2-4-4@oscorp.example.com": ("Product Analyst", "Product / Biotech"),
    "ic2-4-5@oscorp.example.com": ("Product Designer", "Product / Biotech"),
    "ic2-4-6@oscorp.example.com": ("Junior Product Designer", "Product / Biotech"),
    "ic2-4-7@oscorp.example.com": ("Product Analyst", "Product / Biotech"),
    # ICs under MoM3 — Cyber Security (ic3-x-x)
    "ic3-1-1@oscorp.example.com": ("Security Analyst", "Cyber Security"),
    "ic3-1-2@oscorp.example.com": ("Security Engineer", "Cyber Security"),
    "ic3-1-3@oscorp.example.com": ("Junior Security Analyst", "Cyber Security"),
    "ic3-1-4@oscorp.example.com": ("Security Analyst", "Cyber Security"),
    "ic3-2-1@oscorp.example.com": ("Senior Security Engineer", "Cyber Security"),
    "ic3-2-2@oscorp.example.com": ("Security Engineer", "Cyber Security"),
    "ic3-2-3@oscorp.example.com": ("Security Analyst", "Cyber Security"),
    "ic3-2-4@oscorp.example.com": ("Junior Security Analyst", "Cyber Security"),
    "ic3-2-5@oscorp.example.com": ("Security Engineer", "Cyber Security"),
    "ic3-3-1@oscorp.example.com": ("Security Researcher", "Cyber Security"),
    "ic3-3-2@oscorp.example.com": ("Senior Security Analyst", "Cyber Security"),
    "ic3-3-3@oscorp.example.com": ("Security Analyst", "Cyber Security"),
    "ic3-3-4@oscorp.example.com": ("Security Engineer", "Cyber Security"),
    "ic3-3-5@oscorp.example.com": ("Security Researcher", "Cyber Security"),
    "ic3-3-6@oscorp.example.com": ("Senior Security Engineer", "Cyber Security"),
    "ic3-4-1@oscorp.example.com": ("Junior Security Analyst", "Cyber Security"),
    "ic3-4-2@oscorp.example.com": ("Security Analyst", "Cyber Security"),
    "ic3-4-3@oscorp.example.com": ("Security Engineer", "Cyber Security"),
    "ic3-4-4@oscorp.example.com": ("Security Analyst", "Cyber Security"),
    "ic3-4-5@oscorp.example.com": ("Junior Security Analyst", "Cyber Security"),
    "ic3-4-6@oscorp.example.com": ("Security Engineer", "Cyber Security"),
    "ic3-4-7@oscorp.example.com": ("Junior Security Analyst", "Cyber Security"),
    # ICs under MoM4 — Quality Assurance (ic4-x-x)
    "ic4-1-1@oscorp.example.com": ("QA Engineer", "Quality Assurance"),
    "ic4-1-2@oscorp.example.com": ("Senior QA Engineer", "Quality Assurance"),
    "ic4-1-3@oscorp.example.com": ("QA Analyst", "Quality Assurance"),
    "ic4-1-4@oscorp.example.com": ("QA Engineer", "Quality Assurance"),
    "ic4-2-1@oscorp.example.com": ("Junior QA Engineer", "Quality Assurance"),
    "ic4-2-2@oscorp.example.com": ("QA Engineer", "Quality Assurance"),
    "ic4-2-3@oscorp.example.com": ("Senior QA Engineer", "Quality Assurance"),
    "ic4-2-4@oscorp.example.com": ("QA Analyst", "Quality Assurance"),
    "ic4-2-5@oscorp.example.com": ("QA Engineer", "Quality Assurance"),
    "ic4-3-1@oscorp.example.com": ("QA Lead", "Quality Assurance"),
    "ic4-3-2@oscorp.example.com": ("QA Engineer", "Quality Assurance"),
    "ic4-3-3@oscorp.example.com": ("Junior QA Engineer", "Quality Assurance"),
    "ic4-3-4@oscorp.example.com": ("QA Analyst", "Quality Assurance"),
    "ic4-3-5@oscorp.example.com": ("QA Engineer", "Quality Assurance"),
    "ic4-3-6@oscorp.example.com": ("Senior QA Engineer", "Quality Assurance"),
    "ic4-4-1@oscorp.example.com": ("QA Analyst", "Quality Assurance"),
    "ic4-4-2@oscorp.example.com": ("QA Engineer", "Quality Assurance"),
    "ic4-4-3@oscorp.example.com": ("Senior QA Engineer", "Quality Assurance"),
    "ic4-4-4@oscorp.example.com": ("QA Lead", "Quality Assurance"),
    "ic4-4-5@oscorp.example.com": ("Junior QA Engineer", "Quality Assurance"),
    "ic4-4-6@oscorp.example.com": ("QA Analyst", "Quality Assurance"),
    "ic4-4-7@oscorp.example.com": ("QA Engineer", "Quality Assurance"),
}


async def main() -> None:
    async with AsyncSessionLocal() as db:
        # Resolve the demo tenant by code — fails loudly if seed_demo_tenant
        # hasn't run yet (intentional, see seed_departments.py for the
        # same pattern).
        tenant_row = await db.execute(
            text("SELECT id FROM tenants WHERE code = :code"),
            {"code": TENANT_CODE},
        )
        tenant_id_obj = tenant_row.scalar_one_or_none()
        if tenant_id_obj is None:
            raise RuntimeError(
                f"Tenant code={TENANT_CODE!r} not found. "
                "Run `uv run python -m scripts.seed_demo_tenant` first."
            )
        tenant_id = str(tenant_id_obj)

        updated = 0
        not_found = 0
        for email, (title, _dept) in USERS.items():
            result = await db.execute(
                text(
                    "UPDATE users SET job_title = :title "
                    "WHERE email = :email AND tenant_id = :tenant_id"
                ),
                {"title": title, "email": email, "tenant_id": tenant_id},
            )
            if result.rowcount > 0:
                print(f"✓ {email} → {title}")
                updated += 1
            else:
                print(f"✗ {email} not found")
                not_found += 1
        await db.commit()
        print(f"\nDone! Updated: {updated}, Not found: {not_found}")


if __name__ == "__main__":
    asyncio.run(main())
