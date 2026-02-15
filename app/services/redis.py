import redis.asyncio as redis
from app.core.config import get_settings


async def check_redis():
    settings = get_settings()
    r = redis.from_url(settings.redis_url)
    await r.ping()
    await r.close()
