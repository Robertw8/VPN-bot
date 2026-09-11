import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Sponsor

log = structlog.get_logger()


async def missing_sponsors(
    session: AsyncSession, bot: Bot, telegram_id: int
) -> tuple[list[Sponsor], bool]:
    rows = list(
        await session.scalars(select(Sponsor).where(Sponsor.active.is_(True)).order_by(Sponsor.id))
    )
    missing = []
    unavailable = False
    for sponsor in rows:
        try:
            member = await bot.get_chat_member(sponsor.chat_id, telegram_id)
            allowed = member.status in ("creator", "administrator", "member") or (
                member.status == "restricted" and getattr(member, "is_member", False)
            )
            if not allowed:
                missing.append(sponsor)
        except TelegramAPIError:
            log.warning("sponsor_check_unavailable", sponsor_id=sponsor.id)
            unavailable = True
            missing.append(sponsor)
    return missing, unavailable
