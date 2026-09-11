import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.base import now
from app.db.models import Notification, Payment, User
from app.domain.errors import AmbiguousResult, DomainError, Forbidden, IntegrationUnavailable
from app.domain.money import MAX_AMOUNT, percentage
from app.integrations.payments.base import Invoice, PaymentProvider
from app.integrations.payments.card_sbp_stub import CardSbpPaymentProvider
from app.integrations.payments.cryptobot import CryptoBotProvider
from app.logging import error_details
from app.services.ledger import post
from app.services.settings import get_settings

log = structlog.get_logger()


class PaymentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        providers: dict[str, PaymentProvider] | None = None,
    ) -> None:
        self.sessions, self.settings = sessions, settings
        self.providers: dict[str, PaymentProvider] = providers or {
            "card_sbp": CardSbpPaymentProvider()
        }
        if providers is None and settings.cryptobot_enabled:
            self.providers["cryptobot"] = CryptoBotProvider(
                settings.cryptobot_token.get_secret_value(), settings.cryptobot_testnet
            )

    async def create(self, user_id: int, provider: str, cents: int, key: str) -> Payment:
        if type(cents) is not int or not 0 < cents <= MAX_AMOUNT:
            raise DomainError("Недопустимая сумма.")
        adapter = self.providers.get(provider)
        if not adapter:
            raise IntegrationUnavailable()
        if provider == "card_sbp":
            await adapter.create_invoice(cents, key)
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if not user or user.status != "ACTIVE":
                raise Forbidden()
            existing = await session.scalar(select(Payment).where(Payment.idempotency_key == key))
            if existing:
                if (existing.user_id, existing.amount, existing.provider) != (
                    user_id,
                    cents,
                    provider,
                ):
                    raise Forbidden()
                return existing
            row = Payment(
                user_id=user_id,
                provider=provider,
                amount=cents,
                idempotency_key=key,
                raw_metadata={"invoice_creation": "started"},
            )
            session.add(row)
            await session.flush()
            payment_id = row.id
        # Intent committed before HTTP. An uncertain result never triggers a second invoice.
        try:
            invoice = await adapter.create_invoice(cents, key)
            if invoice.amount != cents or invoice.currency != "RUB" or invoice.payload != key:
                raise AmbiguousResult()
        except Exception as exc:
            async with self.sessions.begin() as session:
                payment = await session.get(Payment, payment_id)
                assert payment
                payment.raw_metadata = {"invoice_creation": "unknown"}
            log.error("integration_failure", payment_id=payment_id, **error_details(exc))
            raise AmbiguousResult() from None
        async with self.sessions.begin() as session:
            payment = await session.get(Payment, payment_id)
            assert payment
            payment.external_payment_id, payment.payment_url = invoice.external_id, invoice.url
            payment.raw_metadata = {"invoice_creation": "confirmed"}
            log.info("payment_created", payment_id=payment.id, user_id=user_id)
            return payment

    async def check(self, user_id: int, payment_id: int) -> Payment:
        async with self.sessions() as session:
            payment = await session.scalar(
                select(Payment).where(Payment.id == payment_id, Payment.user_id == user_id)
            )
            if not payment:
                raise Forbidden()
            if payment.status == "PAID":
                return payment
            if not payment.external_payment_id:
                raise AmbiguousResult()
            adapter = self.providers.get(payment.provider)
            if not adapter:
                raise IntegrationUnavailable()
        invoice = await adapter.get_payment(payment.external_payment_id)
        return await self.settle(payment_id, invoice)

    async def settle(self, payment_id: int, invoice: Invoice) -> Payment:
        async with self.sessions.begin() as session:
            payment = await session.scalar(
                select(Payment).where(Payment.id == payment_id).with_for_update()
            )
            if not payment or payment.external_payment_id != invoice.external_id:
                raise DomainError("Платёж не найден.")
            adapter = self.providers.get(payment.provider)
            if not adapter:
                raise IntegrationUnavailable()
            if (
                invoice.amount != payment.amount
                or invoice.currency != payment.currency
                or invoice.payload != payment.idempotency_key
            ):
                raise DomainError("Параметры платежа не совпадают.")
            if payment.status == "PAID":
                return payment
            if invoice.status != "PAID":
                if invoice.status == "EXPIRED":
                    payment.status = "EXPIRED"
                return payment
            if not adapter.verify_payment(invoice, payment.amount, payment.idempotency_key):
                raise DomainError("Платёж не подтверждён.")
            user = await session.get(User, payment.user_id)
            assert user
            # Every multi-wallet transaction locks all users in ascending ID order.
            ids = sorted({user.id, *([user.referrer_id] if user.referrer_id else [])})
            await session.execute(
                select(User).where(User.id.in_(ids)).order_by(User.id).with_for_update()
            )
            cfg = await get_settings(session)
            await post(
                session,
                user.id,
                payment.amount,
                f"payment:{payment.id}",
                "payment_paid",
                payment_id=payment.id,
            )
            if cfg.topup_bonus_enabled and payment.amount >= cfg.topup_bonus_minimum:
                bonus = percentage(payment.amount, cfg.topup_bonus_bps)
                if bonus:
                    await post(session, user.id, bonus, f"topup_bonus:{payment.id}", "topup_bonus")
            if cfg.referral_enabled and user.referrer_id:
                reward = percentage(payment.amount, cfg.referral_payment_bps)
                if reward:
                    await post(
                        session,
                        user.referrer_id,
                        reward,
                        f"referral:{payment.id}",
                        "referral_payment",
                        "referral",
                    )
            payment.status, payment.paid_at = "PAID", now()
            session.add(
                Notification(
                    user_id=user.id,
                    key=f"paid:{payment.id}",
                    text="Оплата подтверждена. Баланс пополнен. Для отключённого VPN нажмите «Включить».",
                )
            )
            return payment

    async def recover(self, actor: int, payment_id: int, external_id: str) -> Payment:
        """Bind an ambiguous invoice only after an authenticated provider lookup."""
        from app.bot.security import require_admin

        require_admin(self.settings, actor)
        async with self.sessions() as session:
            row = await session.get(Payment, payment_id)
            if not row:
                raise DomainError("Платёж не найден.")
            adapter = self.providers.get(row.provider)
            if not adapter:
                raise IntegrationUnavailable()
        invoice = await adapter.get_payment(external_id)
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(Payment).where(Payment.id == payment_id).with_for_update()
            )
            assert row
            if row.external_payment_id not in (None, invoice.external_id):
                raise DomainError("У платежа уже есть другой внешний счёт.")
            if (
                invoice.payload != row.idempotency_key
                or invoice.amount != row.amount
                or invoice.currency != row.currency
            ):
                raise DomainError("Внешний счёт не соответствует этому платежу.")
            row.external_payment_id, row.payment_url = invoice.external_id, invoice.url
            row.raw_metadata = {"invoice_creation": "recovered", "actor_id": actor}
        return await self.settle(payment_id, invoice)

    async def webhook(self, provider: str, body: bytes, signature: str) -> Payment:
        """Transport-independent signed-event entry point; never exposed as a Telegram action."""
        adapter = self.providers.get(provider)
        if not adapter:
            raise IntegrationUnavailable()
        invoice = await adapter.handle_webhook(body, signature)
        async with self.sessions() as session:
            payment_id = await session.scalar(
                select(Payment.id).where(
                    Payment.provider == provider, Payment.external_payment_id == invoice.external_id
                )
            )
        if payment_id is None:
            raise DomainError(
                "Платёж из уведомления не найден. Требуется сверка со счётом провайдера."
            )
        return await self.settle(payment_id, invoice)
