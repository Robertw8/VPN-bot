from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemSetting


class BusinessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    payment_presets: list[int] = [20000, 45000, 80000, 160000]
    topup_bonus_enabled: bool = False
    topup_bonus_minimum: int = Field(default=45000, ge=0, le=100_000_000)
    topup_bonus_bps: int = Field(default=2000, ge=0, le=10000)
    referral_enabled: bool = False
    referral_invitee_bonus: int = Field(default=2500, ge=0, le=100_000_000)
    referral_inviter_bonus: int = Field(default=0, ge=0, le=100_000_000)
    referral_payment_bps: int = Field(default=2000, ge=0, le=10000)
    minimum_withdrawal: int = Field(default=10000, gt=0, le=100_000_000)
    support_username: str = Field(default="", max_length=32, pattern=r"^@?[A-Za-z0-9_]*$")
    information: str = Field(
        default="Информация о сервисе пока не заполнена. Обратитесь в поддержку.", max_length=3000
    )
    maintenance: bool = False

    @field_validator("payment_presets")
    @classmethod
    def presets_valid(cls, values: list[int]) -> list[int]:
        if not 1 <= len(values) <= 8 or any(v <= 0 or v > 100_000_000 for v in values):
            raise ValueError("Invalid payment presets")
        return values


async def get_settings(session: AsyncSession) -> BusinessSettings:
    row = await session.get(SystemSetting, "business")
    return BusinessSettings.model_validate(row.value) if row else BusinessSettings()
