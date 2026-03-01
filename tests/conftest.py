# import os
# import asyncio
# import pytest
# from testcontainers.postgres import PostgresContainer
# from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
# from sqlalchemy import create_engine
# from alembic.config import Config
# from alembic import command
# from httpx import AsyncClient

# from app.main import app
# from app.db.session import get_db



# import pytest
# from testcontainers.postgres import PostgresContainer


# @pytest.fixture(scope="session")
# def postgres_container():
#     container = PostgresContainer("postgres:15")

#     container.start()
#     yield container
#     container.stop()


# @pytest.fixture(scope="session")
# def apply_migrations(postgres_container):
    
#     sync_url = postgres_container.get_connection_url()
#     print(sync_url)

#     sync_url = postgres_container.get_connection_url().replace(
#         "postgresql://",
#         "postgresql+psycopg://",
#     )

#     # run alembic here using sync_url

#     yield

# # # -----------------------------
# # # 1. Start PostgreSQL container
# # # -----------------------------
# # @pytest.fixture(scope="session")
# # def postgres_container():
# #     with PostgresContainer("postgres:16") as postgres:
# #         yield postgres


# # # --------------------------------
# # # 2. Apply Alembic migrations
# # # --------------------------------
# # @pytest.fixture(scope="session")
# # def apply_migrations(postgres_container):
# #     sync_url = postgres_container.get_connection_url()
# #     sync_url = sync_url.replace(
# #         "postgresql://", "postgresql+psycopg://"
# #     )

# #     os.environ["POSTGRES_URL"] = sync_url

# #     alembic_cfg = Config("alembic.ini")
# #     command.upgrade(alembic_cfg, "head")

# #     yield


# # --------------------------------
# # 3. Create async engine for app
# # --------------------------------
# @pytest.fixture(scope="session")
# def async_engine(postgres_container, apply_migrations):
#     async_url = postgres_container.get_connection_url()
#     async_url = postgres_container.get_connection_url().replace(
#         "postgresql://",
#         "postgresql+asyncpg://",
#     )


#     engine = create_async_engine(async_url, future=True)

#     yield engine

#     engine.sync_engine.dispose()


# # --------------------------------
# # 4. Override FastAPI DB dependency
# # --------------------------------
# @pytest.fixture
# async def client(async_engine):
#     async_session = async_sessionmaker(
#         async_engine, expire_on_commit=False
#     )

#     async def override_get_db():
#         async with async_session() as session:
#             yield session

#     app.dependency_overrides[get_db] = override_get_db

#     async with AsyncClient(app=app, base_url="http://test") as ac:
#         yield ac


import os
import pytest

from testcontainers.postgres import PostgresContainer
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)
from alembic.config import Config
from alembic import command
from httpx import AsyncClient

from app.main import app
from app.db.session import get_db


# --------------------------------------------------
# 1️⃣ Start PostgreSQL container (once per session)
# --------------------------------------------------
@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:15") as container:
        yield container


# --------------------------------------------------
# 2️⃣ Run Alembic migrations (sync engine)
# --------------------------------------------------

# @pytest.fixture(scope="session")
# def apply_migrations(postgres_container):
#     sync_url = postgres_container.get_connection_url().replace(
#         "postgresql://",
#         "postgresql+psycopg://",  # psycopg v3 (modern)
#     )

#     alembic_cfg = Config("alembic.ini")
#     alembic_cfg.set_main_option("sqlalchemy.url", sync_url)

#     command.upgrade(alembic_cfg, "head")

#     yield

@pytest.fixture(scope="session")
def apply_migrations(postgres_container):
    sync_url = postgres_container.get_connection_url().replace(
        "postgresql://",
        "postgresql+psycopg://",
    )

    # ✅ Required because env.py expects this
    os.environ["SYNC_POSTGRES_URL"] = sync_url

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    yield

# --------------------------------------------------
# 3️⃣ Create async engine for tests
# --------------------------------------------------
@pytest.fixture(scope="session")
def async_engine(postgres_container, apply_migrations):
    async_url = postgres_container.get_connection_url().replace(
        "postgresql://",
        "postgresql+asyncpg://",  # async runtime driver
    )

    engine = create_async_engine(
        async_url,
        future=True,
    )

    yield engine

    engine.sync_engine.dispose()


# --------------------------------------------------
# 4️⃣ Override FastAPI DB dependency
# --------------------------------------------------
@pytest.fixture
async def client(async_engine):
    async_session = async_sessionmaker(
        async_engine,
        expire_on_commit=False,
    )

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        app=app,
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
