import asyncio

from sqlalchemy import func, select

from app.db.models import Ledger, SystemSetting, User
from app.services.users import register


async def test_registration_referral_repeat(sessions):
    async with sessions.begin() as s:
        s.add(SystemSetting(key="business", value={"referral_enabled": True}))
        inviter = await register(s, 1)
    async with sessions.begin() as s:
        user = await register(s, 2, referral_code=inviter.referral_code)
        assert user.referrer_id == inviter.id
        assert user.balance == 2500
    async with sessions.begin() as s:
        again = await register(s, 2, referral_code=user.referral_code)
        assert again.referrer_id == inviter.id
        assert again.balance == 2500
        assert await s.scalar(select(func.count()).select_from(Ledger)) == 1
    async with sessions.begin() as s:
        own = await register(s, 1, referral_code=inviter.referral_code)
        assert own.referrer_id is None


async def test_concurrent_start(sessions):
    async def run():
        async with sessions.begin() as s:
            return (await register(s, 111)).id

    ids = await asyncio.gather(run(), run())
    assert ids[0] == ids[1]
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 1
