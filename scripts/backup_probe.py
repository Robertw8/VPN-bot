"""Destructive only to two newly-created UUID-named audit databases; never application DB.

Run with the isolated vpn-bot-audit-postgres-1 container healthy on localhost:55433.
Dump contains synthetic data only. No Telegram/payment/VPN network calls are made.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.config import Settings
from app.db.base import Base
from app.db.models import Server, Tariff, User, VpnConfig
from app.db.session import database
from app.services.ledger import post
from app.services.users import register
from app.services.vpn import VpnService

CONTAINER = "vpn-bot-audit-postgres-1"
PREFIX = "vpn_backup_" + uuid.uuid4().hex[:12]
SOURCE, RESTORED = PREFIX + "_source", PREFIX + "_restored"
DUMP = Path("/private/tmp") / (PREFIX + ".dump")
PYTHON = sys.executable


def db_url(name):
    return f"postgresql+asyncpg://vpn:vpn@127.0.0.1:55433/{name}"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def environment(name):
    return {
        **os.environ,
        "DATABASE_URL": db_url(name),
        "APP_ENV": "development",
        "VLESS_PROVIDER": "mock",
    }


async def entities():
    sessions = database(db_url(SOURCE))
    try:
        async with sessions.begin() as session:
            user = await register(session, 810000001, username=None)
            await post(session, user.id, 12345, "backup:credit", "admin_adjustment")
            user_id = user.id
            tariff = await session.scalar(select(Tariff).where(Tariff.type == "PAYG"))
        vpn = VpnService(sessions, Settings(_env_file=None, database_url=db_url(SOURCE)))
        await vpn.create(user_id, tariff.id, None, "backup:vpn")
    finally:
        await sessions.kw["bind"].dispose()


async def snapshot(name):
    sessions = database(db_url(name))
    try:
        async with sessions() as session:
            snapshot = {}
            for table in sorted(Base.metadata.tables):
                rows = (
                    (await session.execute(text(f'SELECT row_to_json(t)::text FROM "{table}" t')))
                    .scalars()
                    .all()
                )
                snapshot[table] = sorted(rows)
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        payload = json.dumps(snapshot, sort_keys=True).encode()
        return {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "counts": {k: len(v) for k, v in snapshot.items()},
            "revision": revision,
        }
    finally:
        await sessions.kw["bind"].dispose()


async def restored_checks():
    sessions = database(db_url(RESTORED))
    try:
        async with sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == 810000001))
            assert user.balance >= 12345
            vpn = await session.scalar(select(VpnConfig))
            assert vpn.provisioned and vpn.connection_uri
            assert await session.scalar(select(Server.id))
            # Verify restored sequence advances beyond old records.
            other = await register(session, 810000002)
            assert other.id > user.id
    finally:
        await sessions.kw["bind"].dispose()


if __name__ == "__main__":
    created = []
    try:
        for name in (SOURCE, RESTORED):
            run("docker", "exec", CONTAINER, "createdb", "-U", "vpn", name)
            created.append(name)
        run(PYTHON, "-m", "alembic", "upgrade", "head", env=environment(SOURCE))
        run(PYTHON, "-m", "app.cli", "seed", "--demo-server", env=environment(SOURCE))
        asyncio.run(entities())
        before = asyncio.run(snapshot(SOURCE))
        with DUMP.open("wb") as output:
            run(
                "docker",
                "exec",
                CONTAINER,
                "pg_dump",
                "-U",
                "vpn",
                "-Fc",
                "--no-owner",
                "--no-acl",
                SOURCE,
                stdout=output,
            )
        DUMP.chmod(0o600)
        with DUMP.open("rb") as source:
            run(
                "docker",
                "exec",
                "-i",
                CONTAINER,
                "pg_restore",
                "-U",
                "vpn",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "-d",
                RESTORED,
                stdin=source,
            )
        run(PYTHON, "-m", "alembic", "upgrade", "head", env=environment(RESTORED))
        run(PYTHON, "-m", "alembic", "check", env=environment(RESTORED))
        after = asyncio.run(snapshot(RESTORED))
        assert before == after, "Restored rows differ"
        asyncio.run(restored_checks())
        print(
            json.dumps(
                {
                    "backup_restore": "passed",
                    "before": before,
                    "after": after,
                    "sequence_check": True,
                    "dump_bytes": DUMP.stat().st_size,
                    "dump_path": str(DUMP),
                }
            )
        )
    finally:
        for name in reversed(created):
            run("docker", "exec", CONTAINER, "dropdb", "-U", "vpn", name)
