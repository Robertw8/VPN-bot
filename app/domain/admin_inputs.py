import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.base import now
from app.integrations.vpn.config import InboundConfig


class ServerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    country: str = Field(min_length=1, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=443, ge=1, le=65535)
    provider_type: Literal["mock", "threexui"] = "mock"
    external_inbound_id: str | None = None
    subscription_base_url: str | None = None
    max_clients: int | None = Field(default=None, gt=0)
    active: bool = True
    priority: int = Field(default=100, ge=0, le=1000000)
    connection_config: InboundConfig | None = None

    @field_validator("host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9.:-]+", value):
            raise ValueError("Host must be a hostname or IP, without scheme/path")
        return value


class TariffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    type: Literal["PAYG", "SUBSCRIPTION"]
    duration_days: int | None = Field(default=None, gt=0, le=3650)
    daily_price: int | None = Field(default=None, gt=0, le=100_000_000)
    fixed_price: int | None = Field(default=None, gt=0, le=100_000_000)
    active: bool = True

    @model_validator(mode="after")
    def pricing(self) -> "TariffInput":
        if self.type == "PAYG" and (
            self.daily_price is None
            or self.duration_days is not None
            or self.fixed_price is not None
        ):
            raise ValueError("PAYG requires daily_price only")
        if self.type == "SUBSCRIPTION" and (
            self.duration_days is None or self.fixed_price is None or self.daily_price is not None
        ):
            raise ValueError("Subscription requires duration_days and fixed_price")
        return self


class PromoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal["BALANCE_BONUS"] = "BALANCE_BONUS"
    value: int = Field(gt=0, le=100_000_000)
    max_activations: int = Field(gt=0, le=1_000_000)
    per_user_limit: int = Field(default=1, gt=0, le=100)
    active: bool = True
    starts_at: datetime = Field(default_factory=now)
    expires_at: datetime | None = None

    @field_validator("starts_at", "expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Timezone required")
        return value

    @model_validator(mode="after")
    def dates(self) -> "PromoInput":
        self.code = self.code.upper()
        if self.expires_at and self.expires_at <= self.starts_at:
            raise ValueError("Expiry must be after start")
        return self


class SponsorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: int
    title: str = Field(min_length=1, max_length=100)
    invite_url: str
    active: bool = True

    @field_validator("invite_url")
    @classmethod
    def telegram_url(cls, value: str) -> str:
        if not value.startswith("https://t.me/") or len(value) > 500:
            raise ValueError("Use a Telegram HTTPS invite URL")
        return value
