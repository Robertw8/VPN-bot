import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_vpn import seed

from app.config import Settings
from app.db.models import Ledger, Notification, User, VpnConfig
from app.domain.errors import DomainError
from app.integrations.vpn.base import Client
from app.integrations.vpn.mock import MockVlessProvider
from app.services.billing import BillingService
from app.services.ledger import post
from app.services.vpn import VpnService, config_revision


class StatefulProvider(MockVlessProvider):
    def __init__(self):
        self.clients = {}
        self.creates = self.disables = 0
        self.fail_create = self.fail_disable = False
        self.fail_lookup = False

    async def get_client(self, server, client_id):
        if self.fail_lookup:
            raise TimeoutError
        return self.clients.get(client_id)

    async def create_client(self, server, client_id):
        self.creates += 1
        self.clients[client_id] = Client(
            client_id, False, (await self.get_connection_data(server, client_id)).uri
        )
        if self.fail_create:
            self.fail_create = False
            raise TimeoutError
        return self.clients[client_id]

    async def enable_client(self, server, client_id, expires_at):
        client = self.clients[client_id]
        self.clients[client_id] = Client(client.id, True, client.uri)

    async def disable_client(self, server, client_id):
        self.disables += 1
        client = self.clients[client_id]
        self.clients[client_id] = Client(client.id, False, client.uri)
        if self.fail_disable:
            self.fail_disable = False
            raise TimeoutError


async def test_create_timeout_lookup_does_not_create_second_client(sessions):
    uid, tid = await seed(sessions)
    provider = StatefulProvider()
    provider.fail_create = True
    vpn = VpnService(sessions, Settings(_env_file=None), {"mock": provider})
    row = await vpn.create(uid, tid, None, "unknown")
    assert row.status == "ERROR" and provider.creates == 1
    await VpnService(sessions, Settings(_env_file=None), {"mock": provider}).reconcile_pending()
    assert provider.creates == 1 and len(provider.clients) == 1
    async with sessions() as s:
        assert (await s.get(VpnConfig, row.id)).provisioned


async def test_unknown_lookup_never_assumed_absent(sessions):
    uid, tid = await seed(sessions)
    provider = StatefulProvider()
    provider.fail_lookup = True
    vpn = VpnService(sessions, Settings(_env_file=None), {"mock": provider})
    row = await vpn.create(uid, tid, None, "lookup")
    assert row.status == "ERROR" and provider.creates == 0


async def test_disable_timeout_finance_consistent_and_no_repeat_disable(sessions):
    uid, tid = await seed(sessions)
    provider = StatefulProvider()
    vpn = VpnService(sessions, Settings(_env_file=None), {"mock": provider})
    billing = BillingService(sessions, vpn)
    row = await vpn.create(uid, tid, None, "disable")
    async with sessions.begin() as s:
        await post(s, uid, 666, "credit", "admin_adjustment")
    at = datetime.now(UTC)
    await billing.activate(uid, row.id, at)
    provider.fail_disable = True
    later = at + timedelta(days=1, seconds=1)
    await asyncio.gather(billing.cycle(row.id, later), billing.cycle(row.id, later))
    await vpn.reconcile_pending()
    assert provider.disables == 1
    async with sessions() as s:
        assert (await s.get(VpnConfig, row.id)).status == "DISABLED"
        assert (await s.get(User, uid)).balance == 0
        assert await s.scalar(select(func.count()).select_from(Notification)) == 1
        assert (
            await s.scalar(
                select(func.count()).select_from(Ledger).where(Ledger.kind == "billing_charge")
            )
            == 1
        )


async def test_billing_restart_catchup_dst_and_stale_keyboard(sessions, monkeypatch):
    from zoneinfo import ZoneInfo

    uid, tid = await seed(sessions)
    vpn = VpnService(sessions, Settings(_env_file=None))
    billing = BillingService(sessions, vpn)
    row = await vpn.create(uid, tid, None, "restart")
    original_revision = config_revision(row)
    async with sessions.begin() as s:
        await post(s, uid, 10000, "credit", "admin_adjustment")
    at = datetime(2026, 3, 28, 12, tzinfo=ZoneInfo("Europe/Warsaw"))
    monkeypatch.setattr("app.services.vpn.now", lambda: at.astimezone(UTC))
    await billing.activate(uid, row.id, at)
    with pytest.raises(DomainError):
        await billing.activate(uid, row.id, expected_revision=original_revision)
    later = at + timedelta(days=5)
    monkeypatch.setattr("app.services.vpn.now", lambda: later.astimezone(UTC))
    restarted = BillingService(sessions, VpnService(sessions, Settings(_env_file=None)))
    await asyncio.gather(restarted.run_once(later), restarted.run_once(later.astimezone(UTC)))
    async with sessions() as s:
        config = await s.get(VpnConfig, row.id)
        assert config.paid_until == later.astimezone(UTC) + timedelta(days=1)
        assert (await s.get(User, uid)).balance == 8668
    with pytest.raises(ValueError):
        await restarted.run_once(datetime(2026, 1, 1))
