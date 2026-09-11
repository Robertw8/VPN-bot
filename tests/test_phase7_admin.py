from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from app.bot.admin_flows import Wizard, begin, control, input_value, new
from app.bot.middleware import ContextMiddleware
from app.config import Settings
from app.db.models import Server, User
from app.domain.admin_inputs import ServerInput
from app.domain.errors import DomainError
from app.services.admin import AdminService
from app.services.users import register


def ui():
    bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock(return_value=True)
    user = TelegramUser(id=111, is_bot=False, first_name="Admin", username="new_name")
    chat = Chat(id=111, type="private")

    def message(text="", mid=1):
        return Message(
            message_id=mid, date=datetime.now(UTC), chat=chat, from_user=user, text=text
        ).as_(bot)

    def query(data):
        return CallbackQuery(
            id="callback", from_user=user, chat_instance="chat", message=message(), data=data
        ).as_(bot)

    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=bot.id, chat_id=111, user_id=111))
    return bot, state, message, query


async def test_server_wizard_preview_save_duplicate_cancel(sessions):
    bot, state, message, query = ui()
    cfg = Settings(_env_file=None, admin_telegram_ids=[111])
    await new(query("manage:new:servers"), cfg, sessions, state)
    first_nonce = (await state.get_data())["wizard_nonce"]
    for mid, value in enumerate(
        ["Амстердам", "Нидерланды", "mock.example", "443", "1", "100", "10"], 10
    ):
        await input_value(message(value, mid), state, cfg)
    assert await state.get_state() == Wizard.preview.state
    async with sessions() as s:
        assert await s.scalar(select(Server)) is None
    with pytest.raises(DomainError):
        await control(query(f"wiz:keep:{first_nonce}"), state, cfg, sessions)
    nonce = (await state.get_data())["wizard_nonce"]
    await control(query(f"wiz:save:{nonce}"), state, cfg, sessions)
    async with sessions() as s:
        assert (await s.scalar(select(Server))).name == "Амстердам"
    with pytest.raises(DomainError):
        await control(query(f"wiz:save:{nonce}"), state, cfg, sessions)
    await new(query("manage:new:servers"), cfg, sessions, state)
    nonce = (await state.get_data())["wizard_nonce"]
    await control(query(f"wiz:cancel:{nonce}"), state, cfg, sessions)
    assert await state.get_state() is None
    await state.storage.close()


async def test_invalid_preview_retains_working_back_button(sessions):
    _, state, message, query = ui()
    cfg = Settings(_env_file=None, admin_telegram_ids=[111])
    await begin(message(), state, "sponsors", {})
    await input_value(message("Channel", 2), state, cfg)
    await input_value(message("-1001234", 3), state, cfg)
    await input_value(message("https://wrong.example", 4), state, cfg)
    assert await state.get_state() == Wizard.preview.state
    nonce = (await state.get_data())["wizard_nonce"]
    await control(query(f"wiz:back:{nonce}"), state, cfg, sessions)
    assert await state.get_state() == Wizard.editing.state
    assert (await state.get_data())["wizard_index"] == 2
    await input_value(message("https://t.me/channel", 5), state, cfg)
    nonce = (await state.get_data())["wizard_nonce"]
    await control(query(f"wiz:save:{nonce}"), state, cfg, sessions)
    await state.storage.close()


async def test_admin_stale_entity_update(sessions):
    cfg = Settings(_env_file=None, admin_telegram_ids=[111])
    service = AdminService(sessions, cfg)
    original = ServerInput(name="Original", country="NL", host="mock.example")
    row_id = await service.server_save(111, original)
    async with sessions() as s:
        version = (await s.get(Server, row_id)).updated_at.isoformat()
    await service.server_save(111, original.model_copy(update={"name": "New"}), row_id)
    with pytest.raises(DomainError):
        await service.server_save(111, original, row_id, expected_version=version)


async def test_blocked_middleware_and_username_refresh(sessions):
    bot, state, message, _ = ui()
    cfg = Settings(_env_file=None)
    async with sessions.begin() as s:
        user = await register(s, 111, username="old_name")
        user.status = "BLOCKED"
    middleware = ContextMiddleware(sessions, cfg)
    handler = AsyncMock()
    await middleware(handler, message("action"), {"bot": bot, "state": state})
    handler.assert_not_called()
    async with sessions() as s:
        assert (await s.get(User, user.id)).username == "new_name"
    assert any("заблокирован" in str(call.args[1]) for call in bot.session.call_args_list)
    await state.storage.close()


async def test_promo_bruteforce_throttle(sessions):
    bot, state, message, _ = ui()
    async with sessions.begin() as s:
        await register(s, 111)
    await state.set_state("CommunityState:promo")
    middleware = ContextMiddleware(sessions, Settings(_env_file=None))
    handler = AsyncMock()
    for mid in range(1, 8):
        middleware.last.clear()
        await middleware(handler, message("GUESS", mid), {"bot": bot, "state": state})
    assert handler.call_count == 5
    await state.storage.close()
