import asyncio

from app.core.database import AsyncSessionLocal, engine
from sqlalchemy import text


async def go():
    print("Engine URL :", engine.url)
    async with AsyncSessionLocal() as s:
        r = await s.execute(text(
            "SELECT current_database(), current_user, "
            "inet_server_addr()::text, inet_server_port()::text"
        ))
        print("Connection :", r.first())
        r = await s.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name"
        ))
        names = [row[0] for row in r.all()]
        print(f"Tables ({len(names)}):", names)

asyncio.run(go())
