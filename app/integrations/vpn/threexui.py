from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, SecretStr, model_validator

from app.integrations.vpn.base import Client, ServerSpec
from app.integrations.vpn.errors import Unavailable


class ThreeXUIConfig(BaseModel):
    base_url: str = ""
    username: str = ""
    password: SecretStr = SecretStr("")
    version: str = ""

    @model_validator(mode="after")
    def complete_and_secure(self) -> "ThreeXUIConfig":
        values = (
            self.base_url,
            self.version,
            self.username,
            self.password.get_secret_value(),
        )
        if not any(values):
            return self
        if not all(values):
            raise ValueError("3x-ui version, HTTPS URL and credentials must be configured together")
        url = urlsplit(self.base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("3x-ui base URL must be HTTPS without credentials, query or fragment")
        return self


class ThreeXUIVlessProvider:
    """Intentionally fails closed until the operator supplies the actual API contract.

    TODO: docs/integrations.md lists required version/inbound/TLS/expiry semantics.
    Do not guess endpoints or silently fall back to mock in production.
    """

    def __init__(self, config: ThreeXUIConfig) -> None:
        self.config = config

    async def authenticate(self) -> None:
        raise Unavailable()

    async def get_inbound(self, server: ServerSpec) -> None:
        raise Unavailable()

    async def create_client(self, server: ServerSpec, client_id: str) -> Client:
        raise Unavailable()

    async def disable_client(self, server: ServerSpec, client_id: str) -> None:
        raise Unavailable()

    async def enable_client(self, server: ServerSpec, client_id: str, expires_at: datetime) -> None:
        raise Unavailable()

    async def delete_client(self, server: ServerSpec, client_id: str) -> None:
        raise Unavailable()

    async def get_client(self, server: ServerSpec, client_id: str) -> Client | None:
        raise Unavailable()

    async def get_connection_data(self, server: ServerSpec, client_id: str) -> Client:
        raise Unavailable()

    async def healthcheck(self, server: ServerSpec) -> bool:
        raise Unavailable()
