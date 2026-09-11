from app.domain.errors import DomainError
from app.integrations.payments.base import Invoice


class CardSbpPaymentProvider:
    async def create_invoice(self, amount: int, key: str) -> Invoice:
        raise DomainError("Оплата картой / СБП пока не настроена. Выберите другой способ.")

    async def get_payment(self, external_id: str) -> Invoice:
        raise DomainError("Провайдер карты / СБП пока не настроен.")

    def verify_payment(self, invoice: Invoice, amount: int, key: str) -> bool:
        return False

    async def handle_webhook(self, body: bytes, signature: str) -> Invoice:
        raise DomainError("Провайдер карты / СБП пока не настроен.")
