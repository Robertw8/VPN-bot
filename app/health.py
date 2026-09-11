"""Local process liveness plus DB connectivity; never claims VPN/payment readiness."""

import asyncio
import json
import os
import time
from pathlib import Path

from sqlalchemy import text

from app.config import Settings
from app.db.session import database

HEALTH_FILE = Path("/tmp/vpn-bot-health.json")


async def heartbeat() -> None:
    while True:
        HEALTH_FILE.write_text(json.dumps({"pid": os.getpid(), "updated": time.time()}))
        await asyncio.sleep(5)


async def check() -> None:
    try:
        state = json.loads(HEALTH_FILE.read_text())
        if time.time() - state["updated"] > 20:
            raise ValueError
        os.kill(state["pid"], 0)
    except (OSError, ValueError, KeyError):
        raise SystemExit(1) from None
    sessions = database(Settings().database_url)
    try:
        async with asyncio.timeout(5), sessions() as session:
            await session.execute(text("SELECT 1"))
    finally:
        await sessions.kw["bind"].dispose()


if __name__ == "__main__":
    asyncio.run(check())
