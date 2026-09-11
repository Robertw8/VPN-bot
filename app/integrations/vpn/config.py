"""Explicit observed inbound settings; no panel API or transport defaults are inferred."""

import ipaddress
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InboundConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    network: Literal["tcp", "ws", "grpc"]
    security: Literal["none", "tls", "reality"]
    sni: str | None = Field(default=None, max_length=253)
    fingerprint: str | None = Field(default=None, max_length=64)
    reality_public_key: str | None = Field(default=None, max_length=128)
    short_id: str | None = Field(default=None, max_length=16, pattern=r"^(?:[0-9a-fA-F]{2})*$")
    flow: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=512)
    transport_host: str | None = Field(default=None, max_length=253)
    service_name: str | None = Field(default=None, max_length=253)
    subscription_base_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def consistent(self) -> "InboundConfig":
        if self.security == "reality" and not (self.reality_public_key and self.sni):
            raise ValueError("REALITY requires observed public key and server name")
        if self.security != "reality" and (
            self.reality_public_key is not None or self.short_id is not None
        ):
            raise ValueError("REALITY fields require REALITY security")
        if self.network != "ws" and (self.path is not None or self.transport_host is not None):
            raise ValueError("WebSocket fields require ws")
        if self.network != "grpc" and self.service_name is not None:
            raise ValueError("service_name requires grpc")
        if self.subscription_base_url:
            url = urlsplit(self.subscription_base_url)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError("Subscription URL must be HTTPS without credentials")
        return self


class VlessConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    uuid: UUID
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    inbound: InboundConfig
    label: str = Field(default="", max_length=100)

    @field_validator("host")
    @classmethod
    def hostname(cls, value: str) -> str:
        if ":" in value:
            ipaddress.IPv6Address(value)
        elif any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for c in value
        ):
            raise ValueError("Invalid public hostname")
        return value

    def uri(self) -> str:
        config = self.inbound
        params: dict[str, str] = {
            "encryption": "none",
            "type": config.network,
            "security": config.security,
        }
        for key, value in {
            "sni": config.sni,
            "fp": config.fingerprint,
            "pbk": config.reality_public_key,
            "sid": config.short_id,
            "flow": config.flow,
            "path": config.path,
            "host": config.transport_host,
            "serviceName": config.service_name,
        }.items():
            if value is not None:
                params[key] = value
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"vless://{self.uuid}@{host}:{self.port}?{urlencode(params, quote_via=quote)}#{quote(self.label, safe='')}"

    @classmethod
    def parse(cls, uri: str) -> "VlessConnection":
        from urllib.parse import unquote

        parsed = urlsplit(uri)
        if (
            parsed.scheme != "vless"
            or parsed.password
            or parsed.path
            or not parsed.hostname
            or not parsed.port
        ):
            raise ValueError("Invalid VLESS URI")
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        params = dict(pairs)
        mapping = {
            "type": "network",
            "security": "security",
            "sni": "sni",
            "fp": "fingerprint",
            "pbk": "reality_public_key",
            "sid": "short_id",
            "flow": "flow",
            "path": "path",
            "host": "transport_host",
            "serviceName": "service_name",
        }
        if (
            len(params) != len(pairs)
            or params.pop("encryption", None) != "none"
            or params.keys() - mapping.keys()
        ):
            raise ValueError("Unsupported or duplicate VLESS fields")
        return cls(
            uuid=UUID(parsed.username or ""),
            host=parsed.hostname,
            port=parsed.port,
            inbound=InboundConfig.model_validate({mapping[k]: v for k, v in params.items()}),
            label=unquote(parsed.fragment),
        )


def remote_expiry_ms(paid_until: datetime, current: datetime) -> int:
    """Absolute UTC milliseconds, rounded DOWN; zero (unlimited) is never returned."""
    if paid_until.utcoffset() is None or current.utcoffset() is None or paid_until <= current:
        raise ValueError("A future timezone-aware paid entitlement is required")
    delta = paid_until.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    result = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
    if result <= 0:
        raise ValueError("Unlimited expiry is forbidden")
    return result
