from datetime import datetime
from uuid import UUID

from app.integrations.vpn.base import Client, ServerSpec


class MockVlessProvider:
    """Deterministic fake credentials. Never provisions a real network service."""

    async def create_client(self, server: ServerSpec, client_id: str) -> Client:
        return await self.get_connection_data(server, client_id)

    async def disable_client(self, server: ServerSpec, client_id: str) -> None:
        UUID(client_id)

    async def enable_client(self, server: ServerSpec, client_id: str, expires_at: datetime) -> None:
        UUID(client_id)

    async def delete_client(self, server: ServerSpec, client_id: str) -> None:
        UUID(client_id)

    async def get_client(self, server: ServerSpec, client_id: str) -> Client | None:
        # Mock has no external persistent service. Creating the same UUID is a pure operation.
        return None

    async def get_connection_data(self, server: ServerSpec, client_id: str) -> Client:
        UUID(client_id)
        return Client(
            client_id,
            False,
            f"vless://{client_id}@mock-server.example:443?encryption=none&security=tls&type=tcp#MOCK-{server.id}",
        )

    async def healthcheck(self, server: ServerSpec) -> bool:
        return True
