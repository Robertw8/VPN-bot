from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

WORKER_LOCK = 863471923


@asynccontextmanager
async def worker_lock(engine: AsyncEngine) -> AsyncIterator[bool]:
    """One active job batch per database. Connection loss/rollback releases the lock."""
    async with engine.begin() as connection:
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": WORKER_LOCK}
            )
        )
        yield acquired
