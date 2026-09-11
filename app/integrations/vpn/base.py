from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.integrations.vpn.config import InboundConfig


@dataclass(frozen=True)
class ServerSpec:
    id: int
    host: str
    inbound_id: str | None
    port: int = 443
    connection: InboundConfig | None = None


@dataclass(frozen=True)
class Client:
    id: str
    enabled: bool
    uri: str
    subscription_url: str | None = None
    expires_at: datetime | None = None
    # False means traffic/quota/admin restrictions require operator review.
    restorable: bool = False


class VlessProvider(Protocol):
    """Mutations MUST be idempotent by client UUID; creation MUST be disabled.

    enable_client must enforce expires_at remotely, so worker outages cannot
    provide unbounded unpaid access. get_client must distinguish absent/unknown.
    """

    async def create_client(self, server: ServerSpec, client_id: str) -> Client: ...
    async def disable_client(self, server: ServerSpec, client_id: str) -> None: ...
    async def enable_client(
        self, server: ServerSpec, client_id: str, expires_at: datetime
    ) -> None: ...
    async def delete_client(self, server: ServerSpec, client_id: str) -> None: ...
    async def get_client(self, server: ServerSpec, client_id: str) -> Client | None: ...
    async def get_connection_data(self, server: ServerSpec, client_id: str) -> Client: ...
    async def healthcheck(self, server: ServerSpec) -> bool: ...
