import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Ledger, PromoCode, Sponsor, User, Withdrawal
from app.domain.errors import DomainError, Forbidden
from app.services.admin import AdminService
from app.services.ledger import post
from app.services.promos import activate
from app.services.referrals import process_withdrawal, transfer, withdraw
from app.services.sponsors import missing_sponsors
from app.services.users import register
from app.services.vpn import VpnService


async def test_promo_limits_concurrency(sessions):
    async with sessions.begin() as s:
        one, two = await register(s, 1), await register(s, 2)
        s.add(PromoCode(code="ONE", value=100, max_activations=1))

    async def use(uid, key):
        async with sessions.begin() as s:
            return await activate(s, uid, "one", key)

    results = await asyncio.gather(use(one.id, "one"), use(two.id, "two"), return_exceptions=True)
    assert sum(r == 100 for r in results) == 1
    assert sum(isinstance(r, DomainError) for r in results) == 1
    async with sessions() as s:
        assert await s.scalar(select(func.sum(User.balance))) == 100
        winner = await s.scalar(select(User).where(User.balance == 100))
    with pytest.raises(DomainError):
        await use(winner.id, "again")


async def test_withdrawal_and_transfer_idempotence(sessions):
    cfg = Settings(_env_file=None, encryption_key=Fernet.generate_key().decode())
    vault = VpnService(sessions, cfg)
    async with sessions.begin() as s:
        user = await register(s, 1)
        await post(s, user.id, 30000, "referral:initial", "referral_payment", "referral")
    async with sessions.begin() as s:
        w = await withdraw(s, user.id, 10000, "СБП test details", "w1", vault)
        assert "details" not in w.payment_details
    async with sessions.begin() as s:
        assert (await withdraw(s, user.id, 10000, "СБП test details", "w1", vault)).id == w.id
    async with sessions.begin() as s:
        await process_withdrawal(s, w.id, "REJECTED", 123)
        await process_withdrawal(s, w.id, "REJECTED", 123)
    async with sessions.begin() as s:
        assert await transfer(s, user.id, "t1") == 30000
        assert await transfer(s, user.id, "t1") == 30000
    async with sessions() as s:
        u = await s.get(User, user.id)
        assert (u.balance, u.referral_balance, u.referral_earned) == (30000, 0, 30000)
        assert (await s.get(Withdrawal, w.id)).status == "REJECTED"
    async with sessions.begin() as s:
        with pytest.raises(DomainError):
            await process_withdrawal(s, w.id, "PAID", 123)


async def test_admin_adjustment_permissions(sessions):
    cfg = Settings(_env_file=None, admin_telegram_ids=[123])
    admin = AdminService(sessions, cfg)
    async with sessions.begin() as s:
        user = await register(s, 1)
    with pytest.raises(Forbidden):
        await admin.adjust(999, user.id, 100, "test", "a")
    with pytest.raises(DomainError):
        await admin.adjust(123, user.id, 100, "", "a")
    await admin.adjust(123, user.id, 100, "test", "a")
    await admin.adjust(123, user.id, 100, "test", "a")
    async with sessions() as s:
        assert (await s.get(User, user.id)).balance == 100
        assert await s.scalar(select(func.count()).select_from(Ledger)) == 1


async def test_sponsor_gate_fail_closed(sessions):
    async with sessions.begin() as s:
        s.add(Sponsor(chat_id=-1001, title="Channel", invite_url="https://t.me/test"))
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))
    async with sessions() as s:
        missing, error = await missing_sponsors(s, bot, 1)
        assert len(missing) == 1 and not error
        bot.get_chat_member.return_value = SimpleNamespace(status="restricted", is_member=True)
        assert await missing_sponsors(s, bot, 1) == ([], False)
        bot.get_chat_member.side_effect = TelegramBadRequest(
            method=GetChatMember(chat_id=-1001, user_id=1), message="no access"
        )
        missing, error = await missing_sponsors(s, bot, 1)
        assert len(missing) == 1 and error
