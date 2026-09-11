import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from test_payments import FakeCrypto

from app.config import Settings
from app.db.base import now
from app.db.models import Ledger, Payment, User
from app.domain.errors import DomainError
from app.domain.money import amount
from app.integrations.payments.cryptobot import CryptoBotProvider
from app.jobs.lock import worker_lock
from app.services.payments import PaymentService
from app.services.users import register


@pytest.mark.parametrize(
    "value",
    ["1e2", "1.00000000000000000000000000000000001", "9" * 1000, "0.001", "-50", "1000000.01"],
)
def test_strict_decimal_input(value):
    with pytest.raises(DomainError):
        amount(value)


def signed(body, token="token"):
    return hmac.new(hashlib.sha256(token.encode()).digest(), body, hashlib.sha256).hexdigest()


@pytest.mark.parametrize(
    "value",
    [
        [],
        None,
        1,
        {"request_date": []},
        {"request_date": "invalid"},
        {"request_date": now().isoformat(), "update_type": "invoice_paid", "payload": []},
    ],
)
async def test_malformed_signed_webhook(value):
    body = json.dumps(value).encode()
    with pytest.raises(DomainError):
        await CryptoBotProvider("token").handle_webhook(body, signed(body))


async def test_delayed_concurrent_webhook(sessions):
    async with sessions.begin() as s:
        user = await register(s, 100)
    provider = FakeCrypto("token")
    service = PaymentService(sessions, Settings(_env_file=None), {"cryptobot": provider})
    p = await service.create(user.id, "cryptobot", 10000, "signed")
    update = {
        "update_type": "invoice_paid",
        "request_date": (now() - timedelta(days=2)).isoformat(),
        "payload": {
            "invoice_id": 123,
            "amount": "100.00",
            "currency_type": "fiat",
            "fiat": "RUB",
            "status": "paid",
            "payload": "signed",
        },
    }
    body = json.dumps(update).encode()
    await asyncio.gather(*(service.webhook("cryptobot", body, signed(body)) for _ in range(4)))
    async with sessions() as s:
        assert (await s.get(User, user.id)).balance == 10000
        assert (
            await s.scalar(
                select(func.count()).select_from(Ledger).where(Ledger.payment_id == p.id)
            )
            == 1
        )
    update["payload"]["invoice_id"] = 999
    unknown = json.dumps(update).encode()
    with pytest.raises(DomainError):
        await service.webhook("cryptobot", unknown, signed(unknown))


async def test_external_invoice_unique_constraint(sessions):
    async with sessions.begin() as s:
        user = await register(s, 1)
        s.add(
            Payment(
                user_id=user.id,
                amount=100,
                provider="cryptobot",
                external_payment_id="5",
                idempotency_key="a",
            )
        )
    with pytest.raises(IntegrityError):
        async with sessions.begin() as s:
            s.add(
                Payment(
                    user_id=user.id,
                    amount=100,
                    provider="cryptobot",
                    external_payment_id="5",
                    idempotency_key="b",
                )
            )


async def test_referrer_immutable_in_db(sessions):
    async with sessions.begin() as s:
        ref = await register(s, 1)
        user = await register(s, 2)
    with pytest.raises(IntegrityError):
        async with sessions.begin() as s:
            await s.execute(
                text("UPDATE users SET referrer_id=:ref WHERE id=:id"),
                {"ref": ref.id, "id": user.id},
            )
    with pytest.raises(IntegrityError):
        async with sessions.begin() as s:
            await s.execute(
                text("UPDATE users SET referrer_id=:ref WHERE id=:id"),
                {"ref": user.id, "id": ref.id},
            )


async def test_duplicate_worker_lock_and_release(sessions):
    engine = sessions.kw["bind"]
    async with worker_lock(engine) as first:
        assert first
        async with worker_lock(engine) as second:
            assert not second
    async with worker_lock(engine) as next_run:
        assert next_run
