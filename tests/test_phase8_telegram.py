from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

from app.bot.keyboards import keyboard, menu
from app.bot.transport import TelegramTransport, text_chunks


def test_callback_utf8_limits_and_menu():
    keyboard([[("ok", "я" * 32)]])
    with pytest.raises(ValueError):
        keyboard([[("invalid", "я" * 33)]])
    with pytest.raises(ValueError):
        keyboard([[("invalid", "")]])
    data = {button.callback_data for row in menu().inline_keyboard for button in row}
    assert {
        "vpn:list",
        "tariffs:list",
        "balance:menu",
        "promo:enter",
        "ref:menu",
        "support",
    } <= data


async def test_plain_text_length_and_keyboard_last():
    text = "<b>&_Тест 😀</b>" * 1000
    chunks = text_chunks(text)
    assert "".join(chunks) == text
    assert all(len(c.encode("utf-16-le")) // 2 <= 4096 for c in chunks)
    request = AsyncMock(return_value=True)
    markup = menu()
    await TelegramTransport()(request, None, SendMessage(chat_id=1, text=text, reply_markup=markup))
    methods = [call.args[1] for call in request.await_args_list]
    assert all(m.parse_mode is None for m in methods)
    assert methods[-1].reply_markup == markup
    assert all(m.reply_markup is None for m in methods[:-1])


@pytest.mark.parametrize(
    "message",
    [
        "Bad Request: query is too old and response timeout expired",
        "Bad Request: query ID is invalid",
    ],
)
async def test_expired_callback_no_second_failure(message):
    method = AnswerCallbackQuery(callback_query_id="old")
    request = AsyncMock(side_effect=TelegramBadRequest(method=method, message=message))
    assert await TelegramTransport()(request, None, method) is True
    assert request.await_count == 1


async def test_stale_edit_replaced_and_duplicate_edit_ignored():
    method = EditMessageText(chat_id=1, message_id=123, text="Меню")
    request = AsyncMock(
        side_effect=[
            TelegramBadRequest(method=method, message="Bad Request: message to edit not found"),
            True,
        ]
    )
    await TelegramTransport()(request, None, method)
    assert isinstance(request.await_args_list[1].args[1], SendMessage)
    request = AsyncMock(
        side_effect=TelegramBadRequest(
            method=method, message="Bad Request: message is not modified"
        )
    )
    assert await TelegramTransport()(request, None, method) is True


@pytest.mark.parametrize(
    "error", [TelegramForbiddenError, TelegramNetworkError, TelegramBadRequest]
)
async def test_other_infrastructure_failures_not_swallowed(error):
    method = SendMessage(chat_id=1, text="Меню")
    request = AsyncMock(side_effect=error(method=method, message="diagnostic must be sanitized"))
    with pytest.raises(error):
        await TelegramTransport()(request, None, method)
    assert request.await_count == 1


async def test_admin_selected_user_needs_no_internal_id_input(sessions):
    from datetime import UTC, datetime

    from aiogram import Bot
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import CallbackQuery, Chat, Message, User

    from app.bot.admin_operations import selected
    from app.config import Settings
    from app.services.users import register

    async with sessions.begin() as session:
        user = await register(session, 8100)
    bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    bot.session = AsyncMock(return_value=True)
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=bot.id, chat_id=1, user_id=1))
    query = CallbackQuery(
        id="1",
        from_user=User(id=1, first_name="A", is_bot=False),
        chat_instance="x",
        message=Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private")).as_(
            bot
        ),
        data=f"ops:target:adjust:{user.id}",
    ).as_(bot)
    await selected(query, state, Settings(_env_file=None, admin_telegram_ids=[1]), sessions)
    saved = await state.get_data()
    assert saved["operation_index"] == 1
    assert saved["operation_values"]["user_id"] == user.id
