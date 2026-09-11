from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Invoice:
    external_id: str
    amount: int
    currency: str
    status: str
    payload: str
    url: str | None = None


class PaymentProvider(Protocol):
    async def create_invoice(self, amount: int, key: str) -> Invoice: ...
    async def get_payment(self, external_id: str) -> Invoice: ...
    def verify_payment(self, invoice: Invoice, amount: int, key: str) -> bool: ...
    async def handle_webhook(self, body: bytes, signature: str) -> Invoice: ...
