from functools import lru_cache
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine():
    settings = get_settings()
    if not settings.async_postgres_url:
        raise RuntimeError("ASYNC_POSTGRES_URL is not configured.")
    return create_async_engine(
        settings.async_postgres_url,
        echo=False,
    )


@lru_cache(maxsize=1)
def get_sessionmaker():
    return async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_sessionmaker()() as session:
        yield session
