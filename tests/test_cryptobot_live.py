"""Explicit TESTNET invoice creation only. Never transfers or pays funds."""

import os
import uuid

import pytest

from app.config import Settings
from app.integrations.payments.cryptobot import CryptoBotProvider


@pytest.mark.cryptobot_live
async def test_cryptobot_testnet_create_and_status():
    if os.getenv("CRYPTOBOT_LIVE_CONFIRM") != "TESTNET":
        pytest.skip("Set CRYPTOBOT_LIVE_CONFIRM=TESTNET to create one testnet invoice")
    settings = Settings()
    if not settings.cryptobot_testnet:
        pytest.fail("Live test refuses mainnet configuration")
    token = settings.cryptobot_token.get_secret_value()
    if not token:
        pytest.skip("CRYPTOBOT_TOKEN is absent")
    provider = CryptoBotProvider(token, testnet=True)
    key = "phase8-testnet-" + uuid.uuid4().hex
    invoice = await provider.create_invoice(100, key)
    assert invoice.amount == 100 and invoice.currency == "RUB"
    assert invoice.url and invoice.url.startswith("https://")
    checked = await provider.get_payment(invoice.external_id)
    assert checked.external_id == invoice.external_id
    assert checked.payload == key and checked.amount == 100
