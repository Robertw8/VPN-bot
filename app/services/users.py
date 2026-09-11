from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.ledger import post
from app.services.settings import get_settings


async def register(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    referral_code: str | None = None,
) -> User:
    referrer = (
        await session.scalar(
            select(User).where(User.referral_code == referral_code, User.telegram_id != telegram_id)
        )
        if referral_code
        else None
    )
    # ON CONFLICT serializes concurrent first /start for the same Telegram account.
    user_id = await session.scalar(
        insert(User)
        .values(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            referrer_id=referrer.id if referrer else None,
        )
        .on_conflict_do_nothing(index_elements=[User.telegram_id])
        .returning(User.id)
    )
    user = (await session.scalars(select(User).where(User.telegram_id == telegram_id))).one()
    if user_id is None:
        user.username, user.first_name, user.last_name = username, first_name, last_name
        return user
    # Attribution only on insertion: a new node cannot introduce a referral cycle.
    if referrer and referrer.telegram_id != telegram_id:
        cfg = await get_settings(session)
        if cfg.referral_enabled:
            if cfg.referral_invitee_bonus:
                await post(
                    session,
                    user.id,
                    cfg.referral_invitee_bonus,
                    f"registration:{user.id}",
                    "registration_bonus",
                )
            if cfg.referral_inviter_bonus:
                await post(
                    session,
                    referrer.id,
                    cfg.referral_inviter_bonus,
                    f"invite:{user.id}",
                    "referral_registration",
                    "referral",
                )
    return user
