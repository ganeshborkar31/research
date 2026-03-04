import os
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:15") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container():
    with DockerContainer("redis:7-alpine").with_exposed_ports(6379) as container:
        yield container


@pytest.fixture(scope="session")
def qdrant_container():
    with DockerContainer("qdrant/qdrant:v1.8.4").with_exposed_ports(6333) as container:
        yield container


@pytest.fixture(scope="session")
def sync_db_url(postgres_container):
    url = make_url(postgres_container.get_connection_url())
    return url.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )


@pytest.fixture(scope="session")
def async_db_url(postgres_container):
    url = make_url(postgres_container.get_connection_url())
    return url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


@pytest.fixture(scope="session")
def redis_url(redis_container):
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def qdrant_url(qdrant_container):
    host = qdrant_container.get_container_host_ip()
    port = qdrant_container.get_exposed_port(6333)
    return f"http://{host}:{port}"


@pytest.fixture(scope="session", autouse=True)
def integration_env(sync_db_url, async_db_url, redis_url, qdrant_url):
    os.environ["SYNC_POSTGRES_URL"] = sync_db_url
    os.environ["ASYNC_POSTGRES_URL"] = async_db_url
    os.environ["REDIS_URL"] = redis_url
    os.environ["QDRANT_URL"] = qdrant_url
    os.environ["RABBITMQ_URL"] = "amqp://guest:guest@localhost:5672/"

    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrated_db(sync_db_url):
    alembic_cfg = Config(str(ROOT_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", sync_db_url)
    command.upgrade(alembic_cfg, "head")
    yield
