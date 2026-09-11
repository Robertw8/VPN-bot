from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import now
from app.db.models import Notification, User, VpnConfig
from app.domain.errors import DomainError, Forbidden, InsufficientFunds
from app.logging import error_details
from app.services.ledger import post
from app.services.vpn import VpnService, config_revision

log = structlog.get_logger()


class BillingService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], vpn: VpnService) -> None:
        self.sessions, self.vpn = sessions, vpn

    async def activate(
        self,
        user_id: int,
        config_id: int,
        at: datetime | None = None,
        expected_revision: str | None = None,
    ) -> None:
        at = at or now()
        if at.tzinfo is None:
            raise ValueError("Timezone-aware timestamp required")
        at = at.astimezone(UTC)
        async with self.sessions.begin() as session:
            row = await self.vpn.owned(session, user_id, config_id, lock=True)
            if expected_revision is not None and expected_revision != config_revision(row):
                raise DomainError("Кнопка устарела. Откройте конфигурацию заново перед оплатой.")
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if not user or user.status != "ACTIVE":
                raise Forbidden()
            if row.reconcile_issue:
                raise DomainError("Состояние VPN уточняется. Обратитесь в поддержку перед оплатой.")
            if row.status == "DELETED" or row.operation == "DELETE":
                raise DomainError("Конфигурация удалена.")
            if not row.provisioned or row.operation == "CREATE":
                raise DomainError("Создание конфигурации ещё не подтверждено.")
            if not row.paid_until or row.paid_until <= at:
                days = 1 if row.mode == "PAYG" else row.duration_days
                assert days
                await self._charge(session, row, at, days)
            row.desired_enabled, row.operation = True, "ENABLE"
        await self.vpn.reconcile(config_id)

    async def _charge(
        self, session: AsyncSession, row: VpnConfig, start: datetime, days: int
    ) -> None:
        sequence = row.billing_sequence + 1
        await post(
            session,
            row.user_id,
            -row.price,
            f"billing:{row.id}:{sequence}",
            "billing_charge",
            billing_config_id=row.id,
            billing_period=sequence,
        )
        row.billing_sequence = sequence
        row.paid_until = start + timedelta(days=days)
        row.expires_at = row.paid_until

    async def cycle(self, config_id: int, at: datetime | None = None) -> None:
        at = at or now()
        if at.tzinfo is None:
            raise ValueError("Timezone-aware timestamp required")
        at = at.astimezone(UTC)
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(VpnConfig).where(VpnConfig.id == config_id).with_for_update()
            )
            if (
                not row
                or row.reconcile_issue is not None
                or not row.desired_enabled
                or row.status == "DELETED"
                or row.operation == "DELETE"
            ):
                return
            if row.paid_until and row.paid_until > at:
                return
            reason = "Срок действия VPN истёк. Оплатите новый период в «Мои конфигурации»."
            if row.mode == "PAYG":
                try:
                    # Prepay rolling 24 hours from recovery time. No back-charging downtime:
                    # providers must enforce paid_until independently of this worker.
                    await self._charge(session, row, at, 1)
                    row.operation = "ENABLE"
                except InsufficientFunds:
                    row.desired_enabled, row.operation = False, "DISABLE"
                    reason = (
                        "VPN отключён: недостаточно средств. Пополните баланс и нажмите «Включить»."
                    )
            else:
                row.desired_enabled, row.operation = False, "DISABLE"
            if not row.desired_enabled:
                session.add(
                    Notification(
                        user_id=row.user_id,
                        key=f"disabled:{row.id}:{row.billing_sequence}",
                        text=reason,
                    )
                )
        await self.vpn.reconcile(config_id)

    async def run_once(self, at: datetime | None = None) -> None:
        at = at or now()
        if at.tzinfo is None:
            raise ValueError("Timezone-aware timestamp required")
        at = at.astimezone(UTC)
        # Keyset pagination avoids starvation on large installations.
        last_id = 0
        while True:
            async with self.sessions() as session:
                ids = list(
                    await session.scalars(
                        select(VpnConfig.id)
                        .where(
                            VpnConfig.id > last_id,
                            VpnConfig.desired_enabled.is_(True),
                            VpnConfig.paid_until <= at,
                            VpnConfig.status != "DELETED",
                        )
                        .order_by(VpnConfig.id)
                        .limit(100)
                    )
                )
            if not ids:
                break
            for config_id in ids:
                try:
                    await self.cycle(config_id, at)
                except Exception as exc:
                    log.error("billing_failure", config_id=config_id, **error_details(exc))
            last_id = ids[-1]
