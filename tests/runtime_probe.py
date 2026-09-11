"""Container-only runtime check against a LOCAL Telegram-compatible HTTP fixture.

Never contacts Telegram or sends real messages. Run as documented in verification.md.
"""

import asyncio
import json
import os
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web

import app.main as runtime
from app.config import Settings
from app.db.session import database

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"


async def probe():
    updates = 0

    async def endpoint(request):
        nonlocal updates
        method = request.match_info["method"]
        if method == "getMe":
            result = {
                "id": 123456,
                "is_bot": True,
                "first_name": "Local Probe",
                "username": "local_probe_bot",
            }
        elif method == "getWebhookInfo":
            result = {"url": "", "has_custom_certificate": False, "pending_update_count": 0}
        elif method == "getUpdates":
            updates += 1
            Path("/tmp/runtime-probe-ready").write_text("ready")
            if updates == 1:
                print(
                    json.dumps(
                        {
                            "probe": "polling_started",
                            "transport": "local_http_fixture",
                            "telegram_live": False,
                        }
                    ),
                    flush=True,
                )
            await asyncio.sleep(1)
            result = []
        else:
            raise web.HTTPBadRequest(text="Unexpected method")
        return web.json_response({"ok": True, "result": result})

    server = web.Application()
    server.router.add_post("/bot{token}/{method}", endpoint)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 18081).start()
    client = AiohttpSession(api=TelegramAPIServer.from_base("http://127.0.0.1:18081"))
    bot = Bot(TOKEN, session=client)
    config = Settings(
        _env_file=None,
        bot_token=TOKEN,
        database_url=os.environ["DATABASE_URL"],
        encryption_key=os.getenv("ENCRYPTION_KEY", ""),
    )
    engines = []

    def tracked_database(url):
        sessions = database(url)
        engines.append(sessions.kw["bind"])
        return sessions

    runtime.Settings = lambda: config
    runtime.Bot = lambda token: bot
    runtime.database = tracked_database
    try:
        await runtime.launch()
    finally:
        await runner.cleanup()
    assert updates > 0
    assert client._session is None or client._session.closed
    assert all(engine.pool.checkedout() == 0 for engine in engines)
    assert not Path("/tmp/vpn-bot-health.json").exists()
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert not pending, [t.get_name() for t in pending]
    print(
        json.dumps(
            {
                "probe": "shutdown_passed",
                "http_session_closed": True,
                "db_connections_released": True,
                "pending_tasks": 0,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(probe())
