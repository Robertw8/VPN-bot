import asyncio
import hashlib
import hmac
import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.base import now
from app.db.models import Ledger, Payment, SystemSetting, User
from app.domain.errors import AmbiguousResult, DomainError
from app.integrations.payments.base import Invoice
from app.integrations.payments.cryptobot import CryptoBotProvider
from app.services.payments import PaymentService
from app.services.users import register


class FakeCrypto(CryptoBotProvider):
    async def create_invoice(self, amount, key):
        return Invoice("123", amount, "RUB", "PENDING", key, "https://t.me/CryptoBot?start=test")


async def test_payment_duplicate_bonus_referral(sessions):
    async with sessions.begin() as s:
        s.add(
            SystemSetting(
                key="business",
                value={
                    "referral_enabled": True,
                    "topup_bonus_enabled": True,
                    "referral_invitee_bonus": 0,
                },
            )
        )
        ref = await register(s, 1)
        user = await register(s, 2, referral_code=ref.referral_code)
    service = PaymentService(sessions, Settings(_env_file=None), {"cryptobot": FakeCrypto("token")})
    payment = await service.create(user.id, "cryptobot", 45000, "p1")
    assert (await service.create(user.id, "cryptobot", 45000, "p1")).id == payment.id
    event = Invoice("123", 45000, "RUB", "PAID", "p1")
    await asyncio.gather(service.settle(payment.id, event), service.settle(payment.id, event))
    async with sessions() as s:
        assert (await s.get(User, user.id)).balance == 54000
        assert (await s.get(User, ref.id)).referral_balance == 9000
        assert await s.scalar(select(func.count()).select_from(Ledger)) == 3
        assert (await s.get(Payment, payment.id)).status == "PAID"
    with pytest.raises(DomainError):
        await service.settle(payment.id, replace(event, amount=1))


async def test_crypto_contract_and_signature():
    invoice = {
        "invoice_id": 123,
        "amount": "450.00",
        "currency_type": "fiat",
        "fiat": "RUB",
        "status": "paid",
        "payload": "key",
        "bot_invoice_url": "https://t.me/CryptoBot?start=x",
    }

    def handler(request):
        assert request.headers["Crypto-Pay-API-Token"] == "secret"
        data = json.loads(request.content)
        if request.url.path.endswith("createInvoice"):
            assert data["amount"] == "450.00" and data["fiat"] == "RUB"
            return httpx.Response(200, json={"ok": True, "result": invoice})
        assert data["invoice_ids"] == "123"
        return httpx.Response(200, json={"ok": True, "result": {"items": [invoice]}})

    adapter = CryptoBotProvider("secret", transport=httpx.MockTransport(handler))
    assert (await adapter.create_invoice(45000, "key")).amount == 45000
    assert (await adapter.get_payment("123")).status == "PAID"
    body = json.dumps(
        {"update_type": "invoice_paid", "request_date": now().isoformat(), "payload": invoice}
    ).encode()
    signature = hmac.new(hashlib.sha256(b"secret").digest(), body, hashlib.sha256).hexdigest()
    assert (await adapter.handle_webhook(body, signature)).external_id == "123"
    with pytest.raises(DomainError):
        await adapter.handle_webhook(body + b" ", signature)


async def test_ambiguous_invoice_not_retried(sessions):
    calls = 0

    class TimeoutCrypto(FakeCrypto):
        async def create_invoice(self, amount, key):
            nonlocal calls
            calls += 1
            raise TimeoutError

    async with sessions.begin() as s:
        user = await register(s, 1)
    service = PaymentService(
        sessions, Settings(_env_file=None), {"cryptobot": TimeoutCrypto("token")}
    )
    with pytest.raises(AmbiguousResult):
        await service.create(user.id, "cryptobot", 100, "uncertain")
    pending = await service.create(user.id, "cryptobot", 100, "uncertain")
    assert pending.external_payment_id is None and calls == 1


async def test_recover_ambiguous_invoice(sessions):
    class RecoverCrypto(FakeCrypto):
        async def create_invoice(self, amount, key):
            raise TimeoutError

        async def get_payment(self, external_id):
            return Invoice(external_id, 10000, "RUB", "PAID", "recovery")

    cfg = Settings(_env_file=None, admin_telegram_ids=[123])
    async with sessions.begin() as s:
        user = await register(s, 1)
    service = PaymentService(sessions, cfg, {"cryptobot": RecoverCrypto("token")})
    with pytest.raises(AmbiguousResult):
        await service.create(user.id, "cryptobot", 10000, "recovery")
    async with sessions() as s:
        payment = await s.scalar(select(Payment))
    with pytest.raises(DomainError):
        await service.recover(999, payment.id, "888")
    await service.recover(123, payment.id, "888")
    await service.recover(123, payment.id, "888")
    async with sessions() as s:
        assert (await s.get(User, user.id)).balance == 10000
        assert await s.scalar(select(func.count()).select_from(Ledger)) == 1


async def test_paid_event_mismatch_is_atomic(sessions):
    async with sessions.begin() as s:
        user = await register(s, 1)
    service = PaymentService(sessions, Settings(_env_file=None), {"cryptobot": FakeCrypto("token")})
    payment = await service.create(user.id, "cryptobot", 10000, "original")
    for event in [
        Invoice("123", 1, "RUB", "PAID", "original"),
        Invoice("123", 10000, "USD", "PAID", "original"),
        Invoice("123", 10000, "RUB", "PAID", "wrong"),
        Invoice("999", 10000, "RUB", "PAID", "original"),
    ]:
        with pytest.raises(DomainError):
            await service.settle(payment.id, event)
    async with sessions() as s:
        assert (await s.get(User, user.id)).balance == 0
        assert await s.scalar(select(func.count()).select_from(Ledger)) == 0
