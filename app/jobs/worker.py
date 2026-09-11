import asyncio

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Notification, User
from app.jobs.lock import worker_lock
from app.logging import error_details
from app.services.billing import BillingService
from app.services.vpn import VpnService

log = structlog.get_logger()


async def notifications(sessions: async_sessionmaker[AsyncSession], bot: Bot) -> None:
    async with sessions.begin() as session:
        rows = list(
            await session.scalars(
                select(Notification)
                .where(Notification.sent.is_(False))
                .order_by(Notification.id)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            user = await session.get(User, row.user_id)
            if not user:
                continue
            try:
                await bot.send_message(user.telegram_id, row.text)
                row.sent = True
            except TelegramForbiddenError:
                row.sent = True
            except Exception as exc:
                log.warning("notification_failure", **error_details(exc))
                break


async def run(
    sessions: async_sessionmaker[AsyncSession],
    vpn: VpnService,
    billing: BillingService,
    bot: Bot,
    interval: int,
) -> None:
    while True:
        try:
            async with worker_lock(sessions.kw["bind"]) as acquired:
                if acquired:
                    await vpn.refresh_health()
                    await vpn.observe_all()
                    await billing.run_once()
                    await vpn.reconcile_pending()
                    await notifications(sessions, bot)
        except Exception as exc:
            log.error("worker_failure", **error_details(exc))
        await asyncio.sleep(interval)


async def main() -> None:
    from app.main import launch

    await launch(worker_only=True)


if __name__ == "__main__":
    asyncio.run(main())
