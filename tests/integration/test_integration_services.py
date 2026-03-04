import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine


pytestmark = pytest.mark.integration


def test_postgres_container_reachable(sync_db_url):
    engine = create_engine(sync_db_url)
    try:
        with engine.connect() as connection:
            value = connection.execute(text("SELECT 1")).scalar_one()
            assert value == 1
    finally:
        engine.dispose()


def test_alembic_upgrade_head_runs(migrated_db, sync_db_url):
    engine = create_engine(sync_db_url)
    try:
        with engine.connect() as connection:
            value = connection.execute(text("SELECT 1")).scalar_one()
            assert value == 1
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_async_postgres_connection(async_db_url):
    engine = create_async_engine(async_db_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_redis_check_uses_testcontainer():
    from app.services.redis import check_redis

    await check_redis()


@pytest.mark.asyncio
async def test_qdrant_container_reachable(qdrant_url):
    client = AsyncQdrantClient(url=qdrant_url)
    try:
        response = await client.get_collections()
        assert response is not None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_qdrant_check_uses_testcontainer():
    from app.services.qdrant import check_qdrant

    await check_qdrant()
