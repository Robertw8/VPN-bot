import hashlib
import hmac
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db.base import now
from app.domain.errors import AmbiguousResult, DomainError, IntegrationUnavailable
from app.domain.money import amount as parse_amount
from app.integrations.payments.base import Invoice
from app.logging import error_details

log = structlog.get_logger()


class CryptoInvoice(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    invoice_id: int = Field(gt=0)
    amount: str
    currency_type: Literal["fiat"]
    fiat: Literal["RUB"]
    status: Literal["active", "paid", "expired"]
    payload: str
    bot_invoice_url: str | None = None


class CryptoBotProvider:
    """Official Crypto Pay API; no createInvoice retry; bounded signed webhook age."""

    def __init__(
        self, token: str, testnet: bool = True, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.token, self.transport = token, transport
        self.base_url = (
            "https://testnet-pay.crypt.bot/api/" if testnet else "https://pay.crypt.bot/api/"
        )

    async def _request(self, method: str, data: dict[str, Any]) -> Any:
        if not self.token:
            raise IntegrationUnavailable()
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=15, transport=self.transport
            ) as client:
                response = await client.post(
                    method, json=data, headers={"Crypto-Pay-API-Token": self.token}
                )
                response.raise_for_status()
                body = json.loads(response.content, parse_float=Decimal)
            if not isinstance(body, dict) or body.get("ok") is not True or "result" not in body:
                raise IntegrationUnavailable()
            return body["result"]
        except (httpx.HTTPError, ValueError, DomainError) as exc:
            log.warning("crypto_api_failure", operation=method, **error_details(exc))
            if method == "createInvoice":
                raise AmbiguousResult() from None
            raise IntegrationUnavailable() from None

    def _invoice(self, row: Any) -> Invoice:
        try:
            parsed = CryptoInvoice.model_validate(row)
            url = parsed.bot_invoice_url
            if url and (
                urlparse(url).scheme != "https"
                or urlparse(url).hostname not in ("t.me", "pay.crypt.bot", "testnet-pay.crypt.bot")
                or urlparse(url).username
            ):
                raise ValueError
            return Invoice(
                str(parsed.invoice_id),
                parse_amount(parsed.amount),
                "RUB",
                {"active": "PENDING", "paid": "PAID", "expired": "EXPIRED"}[parsed.status],
                parsed.payload,
                url,
            )
        except (ValidationError, ValueError, TypeError):
            raise DomainError(
                "Платёжный сервис вернул некорректные данные. Повторите проверку позже."
            ) from None

    async def create_invoice(self, amount: int, key: str) -> Invoice:
        body = await self._request(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": f"{Decimal(amount) / 100:.2f}",
                "payload": key,
                "description": "Пополнение баланса VPN",
                "allow_comments": False,
                "expires_in": 3600,
            },
        )
        return self._invoice(body)

    async def get_payment(self, external_id: str) -> Invoice:
        if not re.fullmatch(r"[1-9][0-9]{0,19}", external_id):
            raise DomainError("Неверный номер платежа.")
        body = await self._request("getInvoices", {"invoice_ids": external_id, "count": 1})
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("items"), list)
            or len(body["items"]) != 1
        ):
            raise IntegrationUnavailable()
        invoice = self._invoice(body["items"][0])
        if invoice.external_id != external_id:
            raise IntegrationUnavailable()
        return invoice

    def verify_payment(self, invoice: Invoice, amount: int, key: str) -> bool:
        return (
            invoice.status == "PAID"
            and invoice.currency == "RUB"
            and invoice.amount == amount
            and invoice.payload == key
        )

    async def handle_webhook(self, body: bytes, signature: str) -> Invoice:
        if (
            not body
            or len(body) > 65536
            or not self.token
            or not re.fullmatch(r"[a-fA-F0-9]{64}", signature)
        ):
            raise DomainError("Неверное уведомление.")
        expected = hmac.new(
            hashlib.sha256(self.token.encode()).digest(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature.lower()):
            raise DomainError("Неверная подпись уведомления.")
        try:
            update = json.loads(body, parse_float=Decimal)
            if not isinstance(update, dict) or not isinstance(update.get("request_date"), str):
                raise ValueError
            date = datetime.fromisoformat(update["request_date"].replace("Z", "+00:00"))
            if date.tzinfo is None or update.get("update_type") != "invoice_paid":
                raise ValueError
            age = (now() - date).total_seconds()
            # Official delivery retries last 3 days; replay safety is in the financial ledger.
            if not -300 <= age <= 3 * 86400 + 300:
                raise ValueError
            invoice = self._invoice(update.get("payload"))
            if invoice.status != "PAID":
                raise ValueError
            return invoice
        except (ValueError, TypeError):
            raise DomainError("Неверное уведомление.") from None
