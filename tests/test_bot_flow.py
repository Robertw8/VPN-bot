from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.methods import AnswerCallbackQuery, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update
from aiogram.types import User as TgUser
from sqlalchemy import select
from test_vpn import seed

from app.bot import (
    admin,
    admin_flows,
    admin_operations,
    community,
    navigation,
    payments,
    start,
    vpn,
)
from app.bot.middleware import ContextMiddleware
from app.config import Settings
from app.db.models import Server, User, VpnConfig
from app.services.billing import BillingService
from app.services.payments import PaymentService
from app.services.vpn import VpnService


async def test_telegram_flow_and_forged_admin(sessions):
    await seed(sessions)
    settings = Settings(_env_file=None, admin_telegram_ids=[111])
    bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
    transport = AsyncMock(return_value=True)
    bot.session = transport
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    middleware = ContextMiddleware(sessions, settings)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    service = VpnService(sessions, settings)
    dp["vpn_service"] = service
    dp["billing_service"] = BillingService(sessions, service)
    dp["payment_service"] = PaymentService(sessions, settings)
    # Modules contain singleton routers. This integration test attaches them once.
    dp.include_routers(
        navigation.router,
        admin_flows.router,
        admin_operations.router,
        community.router,
        admin.router,
        start.router,
        vpn.router,
        payments.router,
    )
    counter = 100
    chat = Chat(id=111, type="private")
    sender = TgUser(id=111, is_bot=False, first_name="Тест")

    async def message(text):
        nonlocal counter
        counter += 1
        middleware.last.clear()
        entities = (
            [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
            if text.startswith("/")
            else None
        )
        msg = Message(
            message_id=counter,
            date=datetime.now(UTC),
            chat=chat,
            from_user=sender,
            text=text,
            entities=entities,
        )
        await dp.feed_update(bot, Update(update_id=counter, message=msg))

    async def callback(data, user=None):
        nonlocal counter
        counter += 1
        middleware.last.clear()
        msg = Message(
            message_id=1, date=datetime.now(UTC), chat=chat, from_user=sender, text="menu"
        )
        cb = CallbackQuery(
            id=str(counter), from_user=user or sender, chat_instance="chat", data=data, message=msg
        )
        await dp.feed_update(bot, Update(update_id=counter, callback_query=cb))

    await message("/start")
    async with sessions() as s:
        user = await s.scalar(select(User).where(User.telegram_id == 111))
        assert user is not None
    await message("/admin")
    await callback("admin:servers")
    await callback("adm:form:servers:add")
    await message('{"name":"Москва","country":"Россия","host":"mock.example"}')
    await callback("manage:new:servers")
    for value in ["Guided server", "Нидерланды", "mock.example", "443", "1", "50", "25"]:
        await message(value)
    context = dp.fsm.get_context(bot=bot, chat_id=111, user_id=111)
    nonce = (await context.get_data())["wizard_nonce"]
    await callback(f"wiz:save:{nonce}")
    async with sessions() as s:
        assert await s.scalar(select(Server).where(Server.name == "Guided server")) is not None
    await callback("vpn:list")
    await callback("vpn:tariff:1")
    await callback("vpn:new:1:0:123456789abcdef0")
    calls = [call.args[1] for call in transport.call_args_list]
    assert any(isinstance(method, SendPhoto) for method in calls)
    assert any(
        isinstance(method, SendMessage) and method.text.startswith("vless://") for method in calls
    )
    assert not any(
        isinstance(method, SendMessage) and "Ошибка обработки" in method.text for method in calls
    )
    async with sessions() as s:
        config = await s.scalar(select(VpnConfig).where(VpnConfig.user_id == user.id))
    await callback(f"vpn:delete:{config.id}")
    async with sessions() as s:
        assert (await s.get(VpnConfig, config.id)).status == "DELETED"
    forged = TgUser(id=1, is_bot=False, first_name="Other")
    await callback("adm:form:settings:edit", forged)
    calls = [call.args[1] for call in transport.call_args_list]
    assert any(
        isinstance(method, AnswerCallbackQuery) and method.text == "Доступ запрещён."
        for method in calls
    )
    await dp.storage.close()
