from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import now
from app.db.models import Ledger, User, Withdrawal
from app.domain.errors import DomainError, Forbidden
from app.services.ledger import post
from app.services.settings import get_settings
from app.services.vpn import VpnService


async def transfer(session: AsyncSession, user_id: int, key: str) -> int:
    user = (await session.scalars(select(User).where(User.id == user_id).with_for_update())).one()
    if user.status != "ACTIVE":
        raise Forbidden()
    existing = await session.scalar(select(Ledger).where(Ledger.key == f"transfer:out:{key}"))
    if existing:
        if existing.user_id != user_id:
            raise Forbidden()
        return -existing.amount
    cents = user.referral_balance
    if cents <= 0:
        raise DomainError("На реферальном балансе нет средств.")
    await post(session, user_id, -cents, f"transfer:out:{key}", "wallet_transfer", "referral")
    await post(session, user_id, cents, f"transfer:in:{key}", "wallet_transfer")
    return cents


async def withdraw(
    session: AsyncSession, user_id: int, cents: int, details: str, key: str, vault: VpnService
) -> Withdrawal:
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or user.status != "ACTIVE":
        raise Forbidden()
    existing = await session.scalar(select(Withdrawal).where(Withdrawal.request_key == key))
    if existing:
        if existing.user_id != user_id:
            raise Forbidden()
        return existing
    settings = await get_settings(session)
    if cents < settings.minimum_withdrawal or cents > 100_000_000 or not 3 <= len(details) <= 1000:
        raise DomainError(
            "Проверьте сумму и реквизиты. Сумма ниже минимальной или реквизиты некорректны."
        )
    if not vault.cipher:
        raise DomainError("Приём заявок на вывод пока не настроен.")
    row = Withdrawal(
        user_id=user_id,
        amount=cents,
        request_key=key,
        payment_details=vault.encrypt(details, "private"),
    )
    session.add(row)
    await session.flush()
    await post(session, user_id, -cents, f"withdrawal:{row.id}", "withdrawal_hold", "referral")
    return row


async def process_withdrawal(
    session: AsyncSession, withdrawal_id: int, status: str, actor: int
) -> None:
    row = await session.scalar(
        select(Withdrawal).where(Withdrawal.id == withdrawal_id).with_for_update()
    )
    if not row:
        raise DomainError("Заявка не найдена.")
    if row.status == status:
        return
    transitions = {"PENDING": {"APPROVED", "REJECTED"}, "APPROVED": {"PAID", "REJECTED"}}
    if status not in transitions.get(row.status, set()):
        raise DomainError("Недопустимый переход статуса заявки.")
    if status == "REJECTED":
        await post(
            session,
            row.user_id,
            row.amount,
            f"withdrawal_refund:{row.id}",
            "withdrawal_refund",
            "referral",
        )
    row.status, row.processed_at, row.processed_by = status, now(), actor
