"""Seed Oscorp departments and link users to them.

Run with:
    uv run python3 scripts/seed_departments.py
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionLocal
from sqlalchemy import text

# Identify the demo tenant by its stable ``code`` rather than a hardcoded
# UUID — every ``seed_demo_tenant.py`` run creates a fresh UUID for the
# tenant, so a hardcoded value only works on the machine that first
# generated it. The lookup happens once at the top of ``main()``.
TENANT_CODE = "oscorp"

DEPARTMENTS = [
    {"code": "ENG",  "name": "Engineering",       "description": "Software and platform engineering"},
    {"code": "PROD", "name": "Product / Biotech",  "description": "Product management and biotech research"},
    {"code": "SEC",  "name": "Cyber Security",     "description": "Information and cyber security"},
    {"code": "QA",   "name": "Quality Assurance",  "description": "Quality assurance and testing"},
    {"code": "FIN",  "name": "Finance",            "description": "Financial planning and analysis"},
    {"code": "HR",   "name": "Human Resources",    "description": "People and culture"},
]

# email → department code
USER_DEPARTMENTS = {
    "cfo@oscorp.example.com":    "FIN",
    "chro@oscorp.example.com":   "HR",
    "mom1@oscorp.example.com":   "ENG",
    "mom2@oscorp.example.com":   "PROD",
    "mom3@oscorp.example.com":   "SEC",
    "mom4@oscorp.example.com":   "QA",
    "mop1-1@oscorp.example.com": "ENG",
    "mop1-2@oscorp.example.com": "ENG",
    "mop1-3@oscorp.example.com": "ENG",
    "mop1-4@oscorp.example.com": "ENG",
    "mop2-1@oscorp.example.com": "PROD",
    "mop2-2@oscorp.example.com": "PROD",
    "mop2-3@oscorp.example.com": "PROD",
    "mop2-4@oscorp.example.com": "PROD",
    "mop3-1@oscorp.example.com": "SEC",
    "mop3-2@oscorp.example.com": "SEC",
    "mop3-3@oscorp.example.com": "SEC",
    "mop3-4@oscorp.example.com": "SEC",
    "mop4-1@oscorp.example.com": "QA",
    "mop4-2@oscorp.example.com": "QA",
    "mop4-3@oscorp.example.com": "QA",
    "mop4-4@oscorp.example.com": "QA",
}

# All ic1-x-x → ENG, ic2-x-x → PROD, ic3-x-x → SEC, ic4-x-x → QA
for mom_num, dept_code in [("1", "ENG"), ("2", "PROD"), ("3", "SEC"), ("4", "QA")]:
    for mop_num in range(1, 5):
        for ic_num in range(1, 8):
            email = f"ic{mom_num}-{mop_num}-{ic_num}@oscorp.example.com"
            USER_DEPARTMENTS[email] = dept_code


async def main() -> None:
    async with AsyncSessionLocal() as db:
        # Resolve the demo tenant by code. Fails loudly if seed_demo_tenant
        # hasn't run yet — that's intentional: we'd rather error here than
        # silently insert against a stale UUID.
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

        # Step 1 — seed departments
        print("Seeding departments...")
        dept_id_map: dict[str, str] = {}
        for dept in DEPARTMENTS:
            existing = await db.execute(
                text("SELECT id FROM departments WHERE tenant_id = :tid AND code = :code"),
                {"tid": tenant_id, "code": dept["code"]},
            )
            row = existing.fetchone()
            if row:
                dept_id_map[dept["code"]] = str(row.id)
                print(f"  ↷ {dept['code']} already exists")
            else:
                new_id = str(uuid.uuid4())
                await db.execute(
                    text(
                        "INSERT INTO departments (id, tenant_id, code, name, description) "
                        "VALUES (:id, :tid, :code, :name, :desc)"
                    ),
                    {"id": new_id, "tid": tenant_id, "code": dept["code"],
                     "name": dept["name"], "desc": dept["description"]},
                )
                dept_id_map[dept["code"]] = new_id
                print(f"  ✓ Created {dept['code']} — {dept['name']}")

        await db.commit()

        # Step 2 — link users to departments
        print("\nLinking users to departments...")
        updated = 0
        skipped = 0
        for email, dept_code in USER_DEPARTMENTS.items():
            dept_id = dept_id_map.get(dept_code)
            if not dept_id:
                print(f"  ✗ Dept {dept_code} not found for {email}")
                continue
            result = await db.execute(
                text(
                    "UPDATE users SET department_id = :dept_id "
                    "WHERE email = :email AND tenant_id = :tid"
                ),
                {"dept_id": dept_id, "email": email, "tid": tenant_id},
            )
            if result.rowcount > 0:
                print(f"  ✓ {email} → {dept_code}")
                updated += 1
            else:
                skipped += 1

        await db.commit()
        print(f"\nDone! Departments: {len(dept_id_map)}, Users linked: {updated}, Skipped: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
