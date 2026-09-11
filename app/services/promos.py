from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import now
from app.db.models import PromoActivation, PromoCode, User
from app.domain.errors import DomainError, Forbidden
from app.services.ledger import post


async def activate(session: AsyncSession, user_id: int, code: str, key: str) -> int:
    promo = await session.scalar(
        select(PromoCode).where(PromoCode.code == code.strip().upper()).with_for_update()
    )
    if not promo:
        raise DomainError("Промокод не найден.")
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or user.status != "ACTIVE":
        raise Forbidden()
    existing = await session.scalar(
        select(PromoActivation).where(PromoActivation.request_key == key)
    )
    if existing:
        if existing.user_id != user_id or existing.promo_id != promo.id:
            raise DomainError("Операция уже использована.")
        return promo.value
    at = now()
    if not promo.active or promo.starts_at > at or (promo.expires_at and promo.expires_at <= at):
        raise DomainError("Промокод не действует.")
    if promo.type != "BALANCE_BONUS":
        raise DomainError("Этот тип промокода пока не поддерживается.")
    used = (
        await session.scalar(
            select(func.count())
            .select_from(PromoActivation)
            .where(PromoActivation.promo_id == promo.id, PromoActivation.user_id == user_id)
        )
        or 0
    )
    if used >= promo.per_user_limit or promo.activations_count >= promo.max_activations:
        raise DomainError("Лимит активаций промокода исчерпан.")
    ordinal = used + 1
    session.add(
        PromoActivation(promo_id=promo.id, user_id=user_id, ordinal=ordinal, request_key=key)
    )
    promo.activations_count += 1
    await post(
        session, user_id, promo.value, f"promo:{promo.id}:{user_id}:{ordinal}", "promo_activation"
    )
    return promo.value
