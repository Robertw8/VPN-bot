from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.security import require_admin
from app.config import Settings
from app.db.models import Audit, PromoCode, Server, Sponsor, SystemSetting, Tariff, User, VpnConfig
from app.domain.admin_inputs import PromoInput, ServerInput, SponsorInput, TariffInput
from app.domain.errors import DomainError
from app.services.ledger import post
from app.services.referrals import process_withdrawal
from app.services.settings import BusinessSettings


class AdminService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings

    async def server_save(
        self,
        actor: int,
        values: ServerInput,
        server_id: int | None = None,
        expected_version: str | None = None,
    ) -> int:
        require_admin(self.settings, actor)
        async with self.sessions.begin() as session:
            if server_id:
                row = await session.scalar(
                    select(Server).where(Server.id == server_id).with_for_update()
                )
                if not row:
                    raise DomainError("Сервер не найден.")
                if expected_version and row.updated_at.isoformat() != expected_version:
                    raise DomainError("Запись уже изменена. Откройте карточку заново.")
                used = await session.scalar(
                    select(func.count())
                    .select_from(VpnConfig)
                    .where(VpnConfig.server_id == server_id)
                )
                if used and (
                    row.connection_config
                    != (values.connection_config.model_dump() if values.connection_config else None)
                    or row.subscription_base_url != values.subscription_base_url
                    or row.port != values.port
                    or row.provider_type != values.provider_type
                    or row.host != values.host
                    or row.external_inbound_id != values.external_inbound_id
                ):
                    raise DomainError(
                        "Нельзя менять подключение сервера с конфигурациями. Добавьте новый сервер."
                    )
                for key, value in values.model_dump().items():
                    setattr(row, key, value)
            else:
                row = Server(**values.model_dump())
                session.add(row)
            await session.flush()
            session.add(Audit(actor_id=actor, action="server_save", target=str(row.id)))
            return row.id

    async def server_delete(
        self, actor: int, server_id: int, expected_version: str | None = None
    ) -> None:
        require_admin(self.settings, actor)
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(Server).where(Server.id == server_id).with_for_update()
            )
            if not row:
                raise DomainError("Сервер не найден.")
            if expected_version and row.updated_at.isoformat() != expected_version:
                raise DomainError("Запись уже изменена. Откройте карточку заново.")
            used = await session.scalar(
                select(func.count()).select_from(VpnConfig).where(VpnConfig.server_id == server_id)
            )
            if used:
                raise DomainError(
                    "Сервер использовался конфигурациями. Отключите его вместо удаления."
                )
            await session.delete(row)
            session.add(Audit(actor_id=actor, action="server_delete", target=str(server_id)))

    async def save_entity(
        self,
        actor: int,
        section: str,
        values: dict[str, Any],
        row_id: int | None = None,
        expected_version: str | None = None,
    ) -> int:
        require_admin(self.settings, actor)
        if section == "servers":
            return await self.server_save(
                actor, ServerInput.model_validate(values), row_id, expected_version
            )
        schemas: dict[
            str, tuple[type[Tariff] | type[PromoCode] | type[Sponsor], type[BaseModel]]
        ] = {
            "tariffs": (Tariff, TariffInput),
            "promos": (PromoCode, PromoInput),
            "sponsors": (Sponsor, SponsorInput),
        }
        if section not in schemas:
            raise DomainError("Раздел недоступен.")
        model, schema = schemas[section]
        checked = schema.model_validate(values).model_dump()
        async with self.sessions.begin() as session:
            if row_id:
                row = await session.scalar(
                    select(model).where(model.id == row_id).with_for_update()
                )
                if not row:
                    raise DomainError("Запись не найдена.")
                if (
                    expected_version
                    and cast(Tariff | PromoCode | Sponsor, row).updated_at.isoformat()
                    != expected_version
                ):
                    raise DomainError("Запись уже изменена. Откройте карточку заново.")
                if isinstance(row, PromoCode) and (
                    checked["max_activations"] < row.activations_count
                    or checked["value"] != row.value
                    and row.activations_count > 0
                ):
                    raise DomainError(
                        "Нельзя менять номинал использованного промокода или уменьшать лимит ниже числа активаций."
                    )
                for key, value in checked.items():
                    setattr(row, key, value)
            else:
                row = model(**checked)
                session.add(row)
            await session.flush()
            entity = cast(Tariff | PromoCode | Sponsor, row)
            session.add(Audit(actor_id=actor, action=section + "_save", target=str(entity.id)))
            return entity.id

    async def settings_patch(
        self, actor: int, patch: dict[str, Any], expected: dict[str, Any] | None = None
    ) -> None:
        require_admin(self.settings, actor)
        if not patch or set(patch) - BusinessSettings.model_fields.keys():
            raise DomainError("Неизвестные настройки.")
        async with self.sessions.begin() as session:
            # Upsert before lock serializes concurrent first edits too.
            await session.execute(
                insert(SystemSetting).values(key="business", value={}).on_conflict_do_nothing()
            )
            row = await session.scalar(
                select(SystemSetting).where(SystemSetting.key == "business").with_for_update()
            )
            assert row
            current = BusinessSettings.model_validate(row.value).model_dump()
            if expected and any(current[key] != value for key, value in expected.items()):
                raise DomainError("Настройки уже изменены. Откройте форму заново.")
            row.value = BusinessSettings.model_validate({**row.value, **patch}).model_dump()
            session.add(Audit(actor_id=actor, action="settings_update", target="business"))

    async def adjust(self, actor: int, user_id: int, cents: int, comment: str, key: str) -> None:
        require_admin(self.settings, actor)
        if (
            not isinstance(cents, int)
            or isinstance(cents, bool)
            or cents == 0
            or abs(cents) > 100_000_000
            or not 3 <= len(comment.strip()) <= 500
        ):
            raise DomainError(
                "Укажите ненулевую сумму в копейках и обязательный комментарий (3–500 символов)."
            )
        async with self.sessions.begin() as session:
            if not await session.get(User, user_id):
                raise DomainError("Пользователь не найден.")
            await post(
                session,
                user_id,
                cents,
                f"admin:{actor}:{key}",
                "admin_adjustment",
                comment=comment,
                actor_id=actor,
            )

    async def block(self, actor: int, user_id: int, blocked: bool) -> None:
        require_admin(self.settings, actor)
        async with self.sessions.begin() as session:
            # Config-before-user order matches billing; reconcile runs after commit.
            configs = list(
                await session.scalars(
                    select(VpnConfig)
                    .where(VpnConfig.user_id == user_id, VpnConfig.status != "DELETED")
                    .order_by(VpnConfig.id)
                    .with_for_update()
                )
            )
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if not user:
                raise DomainError("Пользователь не найден.")
            if user.telegram_id in self.settings.admin_telegram_ids:
                raise DomainError("Нельзя заблокировать администратора.")
            user.status = "BLOCKED" if blocked else "ACTIVE"
            if blocked:
                for row in configs:
                    if row.operation != "DELETE":
                        row.desired_enabled, row.operation = False, "DISABLE"
            session.add(
                Audit(
                    actor_id=actor,
                    action="user_block" if blocked else "user_unblock",
                    target=str(user_id),
                )
            )

    async def withdrawal(self, actor: int, row_id: int, status: str) -> None:
        require_admin(self.settings, actor)
        async with self.sessions.begin() as session:
            await process_withdrawal(session, row_id, status, actor)
            session.add(Audit(actor_id=actor, action="withdrawal_" + status, target=str(row_id)))
