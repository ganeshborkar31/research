import asyncpg
from app.core.config import get_settings


async def check_postgres():
    settings = get_settings()
    conn = await asyncpg.connect(settings.async_postgres_url)
    await conn.execute("SELECT 1")
    await conn.close()
