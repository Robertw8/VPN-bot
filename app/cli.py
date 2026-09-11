"""Explicit local development helpers. No fake payment endpoint is exposed to users."""

import argparse
import asyncio

from sqlalchemy import select

from app.config import Settings
from app.db.models import Server, SystemSetting, Tariff, User
from app.db.session import database
from app.services.ledger import post
from app.services.settings import BusinessSettings
from app.services.users import register


async def run() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="Create initial editable tariffs and business settings")
    seed.add_argument("--demo-server", action="store_true")
    credit = sub.add_parser("dev-credit", help="Development-only ledger credit")
    credit.add_argument("telegram_id", type=int)
    credit.add_argument("--amount", type=int, default=100000, help="Amount in kopecks")
    credit.add_argument(
        "--key", required=True, help="Stable unique key; reuse to avoid duplicate credit"
    )
    args = parser.parse_args()
    cfg = Settings()
    if (
        args.command == "dev-credit" or getattr(args, "demo_server", False)
    ) and cfg.app_env != "development":
        raise SystemExit("Development helpers are forbidden outside development")
    sessions = database(cfg.database_url)
    try:
        async with sessions.begin() as session:
            if args.command == "seed":
                if not await session.get(SystemSetting, "business"):
                    session.add(
                        SystemSetting(key="business", value=BusinessSettings().model_dump())
                    )
                if not await session.scalar(select(Tariff.id).limit(1)):
                    session.add_all(
                        [
                            Tariff(name="Посуточный", type="PAYG", daily_price=666),
                            Tariff(
                                name="30 дней",
                                type="SUBSCRIPTION",
                                duration_days=30,
                                fixed_price=19900,
                            ),
                            Tariff(
                                name="365 дней",
                                type="SUBSCRIPTION",
                                duration_days=365,
                                fixed_price=199000,
                            ),
                        ]
                    )
                if args.demo_server and not await session.scalar(select(Server.id).limit(1)):
                    session.add(
                        Server(
                            name="Тестовый сервер",
                            country="Нидерланды",
                            host="mock-server.example",
                            provider_type="mock",
                        )
                    )
                print("Initial editable data created; existing data preserved.")
            else:
                if not 0 < args.amount <= 100_000_000:
                    raise SystemExit("Amount must be positive kopecks, at most 100000000")
                user = await session.scalar(
                    select(User).where(User.telegram_id == args.telegram_id)
                )
                if not user:
                    user = await register(session, args.telegram_id)
                await post(
                    session,
                    user.id,
                    args.amount,
                    "dev:" + args.key,
                    "admin_adjustment",
                    comment="Development credit",
                )
                print("Development ledger credit recorded.")
    finally:
        await sessions.kw["bind"].dispose()


if __name__ == "__main__":
    asyncio.run(run())
