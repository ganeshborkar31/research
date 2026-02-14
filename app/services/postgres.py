import asyncpg
from app.core.config import settings


async def check_postgres():
    conn = await asyncpg.connect(settings.POSTGRES_URL)
    await conn.execute("SELECT 1")
    await conn.close()
