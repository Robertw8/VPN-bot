import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from test_vpn import seed

from app.config import Settings
from app.db.base import now
from app.db.models import Ledger, Tariff, User, VpnConfig
from app.domain.errors import DomainError
from app.integrations.vpn.mock import MockVlessProvider
from app.services.admin import AdminService
from app.services.billing import BillingService
from app.services.ledger import post
from app.services.vpn import VpnService


async def test_vpn_timeout_persists_uuid_and_recovers(sessions):
    uid, tid = await seed(sessions)
    seen = []

    class Uncertain(MockVlessProvider):
        async def create_client(self, server, client_id):
            seen.append(client_id)
            if len(seen) == 1:
                raise TimeoutError("secret-value-never-logged")
            return await super().create_client(server, client_id)

    provider = Uncertain()
    service = VpnService(sessions, Settings(_env_file=None), {"mock": provider})
    row = await service.create(uid, tid, None, "create")
    assert row.status == "ERROR" and row.operation == "CREATE"
    # Simulate worker process replacement; persisted intent survives.
    replacement = VpnService(sessions, Settings(_env_file=None), {"mock": provider})
    await replacement.reconcile_pending()
    async with sessions() as s:
        row = await s.get(VpnConfig, row.id)
        assert row.status == "DISABLED" and row.operation is None
    assert len(seen) == 2 and seen[0] == seen[1]


async def test_concurrent_wallets_no_lost_update(sessions):
    uid, _ = await seed(sessions)

    async def credit(index):
        async with sessions.begin() as s:
            # Deliberately preload stale identity-map state before taking wallet lock.
            await s.get(User, uid)
            await asyncio.sleep(0)
            await post(s, uid, 100, f"credit:{index}", "admin_adjustment")

    await asyncio.gather(*(credit(i) for i in range(10)))
    async with sessions() as s:
        assert (await s.get(User, uid)).balance == 1000
        assert await s.scalar(select(func.sum(Ledger.amount))) == 1000


async def test_db_rejects_missing_payg_price(sessions):
    with pytest.raises(IntegrityError):
        async with sessions.begin() as s:
            s.add(Tariff(name="bad", type="PAYG"))


async def test_deleted_config_never_billed_or_reenabled(sessions):
    uid, tid = await seed(sessions)
    vpn = VpnService(sessions, Settings(_env_file=None))
    billing = BillingService(sessions, vpn)
    async with sessions.begin() as s:
        await post(s, uid, 10000, "credit", "admin_adjustment")
    row = await vpn.create(uid, tid, None, "cfg")
    await billing.activate(uid, row.id)
    await vpn.request(uid, row.id, "DELETE")
    await billing.run_once(now() + timedelta(days=2))
    with pytest.raises(DomainError):
        await billing.activate(uid, row.id)
    async with sessions() as s:
        assert (await s.get(User, uid)).balance == 9334


async def test_block_disables_existing_vpn(sessions):
    uid, tid = await seed(sessions)
    cfg = Settings(_env_file=None, admin_telegram_ids=[123])
    vpn = VpnService(sessions, cfg)
    async with sessions.begin() as s:
        await post(s, uid, 10000, "credit", "admin_adjustment")
    row = await vpn.create(uid, tid, None, "cfg")
    billing = BillingService(sessions, vpn)
    await billing.activate(uid, row.id)
    await AdminService(sessions, cfg).block(123, uid, True)
    await vpn.reconcile_pending()
    async with sessions() as s:
        assert (await s.get(VpnConfig, row.id)).status == "DISABLED"
    with pytest.raises(DomainError):
        await billing.activate(uid, row.id)
