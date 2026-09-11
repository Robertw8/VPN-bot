import asyncio

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import Server, Tariff, VpnConfig
from app.domain.errors import DomainError, Forbidden
from app.services.users import register
from app.services.vpn import VpnService


async def seed(sessions):
    async with sessions.begin() as s:
        user = await register(s, 1)
        s.add_all(
            [
                Server(name="A", country="NL", host="mock.example", priority=10, max_clients=1),
                Server(name="B", country="DE", host="mock.example", priority=20),
                Server(name="Off", country="NL", host="mock.example", priority=0, active=False),
            ]
        )
        tariff = Tariff(name="Daily", type="PAYG", daily_price=666)
        s.add(tariff)
        await s.flush()
        return user.id, tariff.id


async def test_vpn_lifecycle_ownership_selection(sessions):
    user_id, tariff_id = await seed(sessions)
    vpn = VpnService(sessions, Settings(_env_file=None))
    a = await vpn.create(user_id, tariff_id, None, "create:a")
    b = await vpn.create(user_id, tariff_id, None, "create:b")
    assert a.server_id != b.server_id
    assert a.provisioned and a.status == "DISABLED"
    assert vpn.decrypt(a.connection_uri).startswith("vless://")
    assert (await vpn.create(user_id, tariff_id, None, "create:a")).id == a.id
    with pytest.raises(Forbidden):
        await vpn.request(999, a.id, "DELETE")
    await vpn.request(user_id, a.id, "DELETE")
    await vpn.request(user_id, a.id, "DELETE")
    async with sessions() as s:
        assert (await s.get(VpnConfig, a.id)).status == "DELETED"


async def test_capacity_concurrent(sessions):
    user_id, tariff_id = await seed(sessions)
    vpn = VpnService(sessions, Settings(_env_file=None))
    async with sessions.begin() as s:
        other = await register(s, 2)
        server = await s.scalar(select(Server).where(Server.name == "A"))
    results = await asyncio.gather(
        vpn.create(user_id, tariff_id, server.id, "a"),
        vpn.create(other.id, tariff_id, server.id, "b"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, VpnConfig) for r in results) == 1
    assert sum(isinstance(r, DomainError) for r in results) == 1
