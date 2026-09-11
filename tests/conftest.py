import os
import uuid

import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import models  # noqa: F401


def migrate(connection):
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def sessions():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        import pytest

        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    # Isolated schema: never drop shared/public tables, even on a development DB.
    schema = "test_" + uuid.uuid4().hex
    root = create_async_engine(url)
    async with root.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.run_sync(migrate)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
    async with root.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await root.dispose()
