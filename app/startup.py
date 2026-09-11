from pathlib import Path

from aiogram.utils.token import TokenValidationError, validate_token
from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.errors import DomainError


def validate_config(settings: Settings, require_token: bool = True) -> None:
    token = settings.bot_token.get_secret_value()
    if require_token:
        if not token:
            raise DomainError("BOT_TOKEN отсутствует. Укажите токен BotFather в .env.")
        try:
            validate_token(token)
        except TokenValidationError:
            raise DomainError("BOT_TOKEN имеет неверный формат. Проверьте .env.") from None
    key = settings.encryption_key.get_secret_value()
    if key:
        try:
            Fernet(key.encode())
        except (ValueError, TypeError):
            raise DomainError("ENCRYPTION_KEY некорректен. Используйте ключ Fernet.") from None
    if settings.vless_provider != "mock" and not key:
        raise DomainError("Для реального VPN нужен ENCRYPTION_KEY.")
    if settings.vless_provider == "threexui":
        raise DomainError(
            "Адаптер 3x-ui ещё не подключён. Выполните checklist из docs/integrations.md."
        )
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise DomainError("DATABASE_URL должен использовать postgresql+asyncpg.")


async def validate_database(sessions: async_sessionmaker[AsyncSession]) -> None:
    expected = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    ).get_current_head()
    async with sessions() as session:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != expected:
            raise DomainError("База данных требует миграций. Выполните alembic upgrade head.")
