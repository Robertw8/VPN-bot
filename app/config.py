from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    bot_token: SecretStr = SecretStr("")
    admin_telegram_ids: list[int] = []
    database_url: str = "postgresql+asyncpg://vpn:vpn@localhost:5432/vpn"
    app_env: Literal["development", "test", "production"] = "development"
    vless_provider: Literal["mock", "threexui"] = "mock"
    encryption_key: SecretStr = SecretStr("")
    threexui_base_url: str = ""
    threexui_version: str = ""
    threexui_username: str = ""
    threexui_password: SecretStr = SecretStr("")
    cryptobot_token: SecretStr = SecretStr("")
    cryptobot_enabled: bool = False
    cryptobot_testnet: bool = True
    billing_interval_seconds: int = 60

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        if self.billing_interval_seconds < 10:
            raise ValueError("Billing interval must be at least 10 seconds")
        if self.cryptobot_enabled and not self.cryptobot_token.get_secret_value():
            raise ValueError("CRYPTOBOT_TOKEN is required")
        if self.app_env == "production" and self.vless_provider == "mock":
            raise ValueError("Mock VPN is forbidden in production")
        return self
