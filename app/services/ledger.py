import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ledger, User
from app.domain.errors import DomainError, InsufficientFunds

log = structlog.get_logger()


async def post(
    session: AsyncSession,
    user_id: int,
    cents: int,
    key: str,
    kind: str,
    wallet: str = "main",
    comment: str = "",
    actor_id: int | None = None,
    payment_id: int | None = None,
    billing_config_id: int | None = None,
    billing_period: int | None = None,
) -> Ledger:
    if type(cents) is not int or cents == 0:
        raise DomainError("Некорректная сумма операции.")
    user = (
        await session.scalars(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    existing = await session.scalar(select(Ledger).where(Ledger.key == key))
    if existing:
        if (existing.user_id, existing.amount, existing.wallet, existing.kind) != (
            user_id,
            cents,
            wallet,
            kind,
        ):
            raise DomainError("Ключ операции уже использован.")
        return existing
    if wallet not in ("main", "referral"):
        raise ValueError("Invalid wallet")
    attr = "balance" if wallet == "main" else "referral_balance"
    updated = getattr(user, attr) + cents
    if updated < 0:
        raise InsufficientFunds()
    setattr(user, attr, updated)
    if wallet == "referral" and cents > 0 and kind.startswith("referral_"):
        user.referral_earned += cents
    row = Ledger(
        user_id=user_id,
        wallet=wallet,
        amount=cents,
        balance_after=updated,
        key=key,
        kind=kind,
        comment=comment,
        actor_id=actor_id,
        payment_id=payment_id,
        billing_config_id=billing_config_id,
        billing_period=billing_period,
    )
    session.add(row)
    await session.flush()
    log.info(
        kind, user_id=user_id, amount=cents, ledger_id=row.id, transaction_state="pending_commit"
    )
    return row
