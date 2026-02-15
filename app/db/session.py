from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import os

DATABASE_URL = os.getenv("POSTGRES_URL")

ASYNC_DATABASE_URL = DATABASE_URL.replace(
    "postgresql://", "postgresql+asyncpg://"
)

engine = create_async_engine(ASYNC_DATABASE_URL, echo=True)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
