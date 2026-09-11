import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_vpn import seed

from app.config import Settings
from app.db.base import now
from app.db.models import Ledger, Tariff, User, VpnConfig
from app.domain.errors import InsufficientFunds
from app.services.billing import BillingService
from app.services.ledger import post
from app.services.vpn import VpnService


async def test_payg_concurrent_and_insufficient(sessions):
    uid, tid = await seed(sessions)
    vpn = VpnService(sessions, Settings(_env_file=None))
    billing = BillingService(sessions, vpn)
    row = await vpn.create(uid, tid, None, "vpn")
    async with sessions.begin() as s:
        await post(s, uid, 1332, "deposit", "admin_adjustment")
    at = now()
    await asyncio.gather(billing.activate(uid, row.id, at), billing.activate(uid, row.id, at))
    async with sessions() as s:
        assert (await s.get(User, uid)).balance == 666
        assert (await s.get(VpnConfig, row.id)).status == "ACTIVE"
    tomorrow = at + timedelta(days=1, seconds=1)
    await asyncio.gather(billing.cycle(row.id, tomorrow), billing.cycle(row.id, tomorrow))
    async with sessions() as s:
        assert (await s.get(User, uid)).balance == 0
        assert (
            await s.scalar(
                select(func.count()).select_from(Ledger).where(Ledger.kind == "billing_charge")
            )
            == 2
        )
    await billing.cycle(row.id, tomorrow + timedelta(days=1, seconds=1))
    async with sessions() as s:
        cfg = await s.get(VpnConfig, row.id)
        assert cfg.status == "DISABLED" and not cfg.desired_enabled
    with pytest.raises(InsufficientFunds):
        await billing.activate(uid, row.id, tomorrow + timedelta(days=2))


async def test_subscription_expiry_no_daily_charge(sessions):
    uid, _ = await seed(sessions)
    async with sessions.begin() as s:
        tariff = Tariff(name="Month", type="SUBSCRIPTION", duration_days=30, fixed_price=10000)
        s.add(tariff)
        await post(s, uid, 20000, "deposit", "admin_adjustment")
    vpn = VpnService(sessions, Settings(_env_file=None))
    billing = BillingService(sessions, vpn)
    row = await vpn.create(uid, tariff.id, None, "sub")
    at = now()
    await billing.activate(uid, row.id, at)
    await billing.cycle(row.id, at + timedelta(days=1))
    await billing.cycle(row.id, at + timedelta(days=31))
    async with sessions() as s:
        assert (await s.get(User, uid)).balance == 10000
        assert (await s.get(VpnConfig, row.id)).status == "DISABLED"
    await billing.activate(uid, row.id, at + timedelta(days=31))
    async with sessions() as s:
        assert (await s.get(User, uid)).balance == 0
