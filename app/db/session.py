from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import get_settings
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
import os

settings = get_settings()
engine = create_async_engine(settings.async_postgres_url, echo=True)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)


def get_engine():
    settings = get_settings()
    return create_async_engine(
        settings.async_postgres_url,
        echo=False,
    )


def get_sessionmaker():
    engine = get_engine()
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session_maker = get_sessionmaker()

    async with session_maker() as session:
        yield session
