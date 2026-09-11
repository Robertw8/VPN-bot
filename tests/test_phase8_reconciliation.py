from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.base import now
from app.db.models import Server, Tariff, VpnConfig
from app.domain.errors import DomainError
from app.integrations.vpn.base import Client
from app.integrations.vpn.errors import Timeout
from app.services.billing import BillingService
from app.services.users import register
from app.services.vpn import VpnService


class Remote:
    def __init__(self):
        self.client = None
        self.failure = False
        self.enabled, self.disabled, self.created = [], 0, 0

    async def get_client(self, server, client_id):
        if self.failure:
            raise Timeout()
        return self.client

    async def disable_client(self, server, client_id):
        self.disabled += 1
        self.client = Client(client_id, False, "vless://fixture", expires_at=self.client.expires_at)

    async def enable_client(self, server, client_id, expires_at):
        self.enabled.append(expires_at)
        self.client = Client(client_id, True, "vless://fixture", expires_at=expires_at)

    async def create_client(self, server, client_id):
        self.created += 1
        raise AssertionError("Observation must never create")

    async def healthcheck(self, server):
        if self.failure:
            raise Timeout()
        return True


async def setup(sessions):
    async with sessions.begin() as session:
        user = await register(session, 100)
        server = Server(name="A", country="NL", host="example.org")
        tariff = Tariff(name="Daily", type="PAYG", daily_price=100)
        session.add_all([server, tariff])
        await session.flush()
        row = VpnConfig(
            user_id=user.id,
            server_id=server.id,
            tariff_id=tariff.id,
            request_key="existing",
            mode="PAYG",
            price=100,
            provisioned=True,
            status="ACTIVE",
            desired_enabled=True,
            paid_until=now() + timedelta(hours=2),
        )
        session.add(row)
        await session.flush()
    remote = Remote()
    remote.client = Client(
        row.external_client_id, True, "vless://fixture", expires_at=row.paid_until
    )
    return VpnService(sessions, Settings(_env_file=None), {"mock": remote}), remote, row


@pytest.mark.parametrize(
    "scenario",
    [
        "ok",
        "absent",
        "unknown",
        "disabled",
        "restorable",
        "unpaid",
        "expiry_unknown",
        "expiry_excess",
    ],
)
async def test_observe_remote_states(sessions, scenario):
    vpn, remote, row = await setup(sessions)
    if scenario == "absent":
        remote.client = None
    elif scenario == "unknown":
        remote.failure = True
    elif scenario in ("disabled", "restorable"):
        remote.client = Client(
            row.external_client_id,
            False,
            "vless://fixture",
            expires_at=row.paid_until,
            restorable=scenario == "restorable",
        )
    elif scenario == "expiry_unknown":
        remote.client = Client(row.external_client_id, True, "vless://fixture")
    elif scenario == "expiry_excess":
        remote.client = Client(
            row.external_client_id,
            True,
            "vless://fixture",
            expires_at=row.paid_until + timedelta(days=90),
        )
    elif scenario == "unpaid":
        async with sessions.begin() as session:
            local = await session.get(VpnConfig, row.id)
            local.desired_enabled = False
    await vpn.observe(row.id)
    async with sessions() as session:
        actual = await session.get(VpnConfig, row.id)
    assert remote.created == 0
    if scenario in ("absent", "unknown", "disabled", "expiry_unknown"):
        assert actual.status == "ERROR" and actual.reconcile_issue
        with pytest.raises(DomainError):
            await BillingService(sessions, vpn).activate(row.user_id, row.id)
    elif scenario == "unpaid":
        assert actual.status == "DISABLED" and remote.disabled == 1
        await vpn.observe(row.id)
        assert remote.disabled == 1
    else:
        assert actual.status == "ACTIVE" and actual.reconcile_issue is None
        assert remote.enabled == (
            [row.paid_until] if scenario in ("restorable", "expiry_excess") else []
        )


async def test_health_and_capacity_selection(sessions):
    vpn, remote, row = await setup(sessions)
    async with sessions.begin() as session:
        first = await session.get(Server, row.server_id)
        first.health = "OFFLINE"
        first.priority = 0
        second = Server(name="B", country="DE", host="b.example", priority=1, max_clients=1)
        third = Server(name="C", country="FI", host="c.example", priority=1)
        session.add_all([second, third])
        await session.flush()
        assert (await vpn.select_server(session, None)).id == second.id
        with pytest.raises(DomainError):
            await vpn.select_server(session, first.id)
        session.add(
            VpnConfig(
                user_id=row.user_id,
                server_id=second.id,
                tariff_id=row.tariff_id,
                request_key="reserved",
                mode="PAYG",
                price=100,
                status="DISABLED",
            )
        )
        await session.flush()
        assert (await vpn.select_server(session, None)).id == third.id
    remote.failure = True
    await vpn.refresh_health()
    async with sessions() as session:
        assert all(s.health == "OFFLINE" for s in await session.scalars(select(Server)))
        assert (await session.get(VpnConfig, row.id)).status == "ACTIVE"
    remote.failure = False
    await vpn.refresh_health()
    async with sessions() as session:
        assert all(s.health == "HEALTHY" for s in await session.scalars(select(Server)))


async def test_real_server_requires_healthy_observation(sessions):
    vpn, remote, row = await setup(sessions)
    vpn.settings = Settings(_env_file=None, vless_provider="threexui")
    async with sessions.begin() as session:
        server = await session.get(Server, row.server_id)
        server.provider_type = "threexui"
        server.health = "UNKNOWN"
        with pytest.raises(DomainError):
            await vpn.select_server(session, server.id)
        server.health = "DEGRADED"
        with pytest.raises(DomainError):
            await vpn.select_server(session, server.id)
        server.health = "HEALTHY"
        assert (await vpn.select_server(session, server.id)).id == server.id


async def test_health_refresh_preserves_admin_revision(sessions):
    vpn, remote, row = await setup(sessions)
    async with sessions() as session:
        before = (await session.get(Server, row.server_id)).updated_at
    await vpn.refresh_health()
    async with sessions() as session:
        server = await session.get(Server, row.server_id)
        assert server.updated_at == before
        assert server.health == "HEALTHY" and server.health_checked_at is not None


async def test_observed_connection_config_cannot_change_after_use(sessions):
    from app.domain.admin_inputs import ServerInput
    from app.services.admin import AdminService

    vpn, remote, row = await setup(sessions)
    async with sessions() as session:
        server = await session.get(Server, row.server_id)
        values = {key: getattr(server, key) for key in ServerInput.model_fields}
    values["connection_config"] = {"network": "tcp", "security": "tls", "sni": "observed.example"}
    with pytest.raises(DomainError):
        await AdminService(sessions, Settings(_env_file=None, admin_telegram_ids=[1])).server_save(
            1, ServerInput(**values), server.id
        )
