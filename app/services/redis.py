import redis.asyncio as redis
from app.core.config import settings


async def check_redis():
    r = redis.from_url(settings.REDIS_URL)
    await r.ping()
    await r.close()
