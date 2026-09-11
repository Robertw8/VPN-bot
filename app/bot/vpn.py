import io
import uuid
from typing import Any

import qrcode
from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import keyboard
from app.db.models import Server, Tariff, User, VpnConfig
from app.domain.errors import DomainError
from app.domain.money import rub
from app.services.vpn import VpnService, config_revision, paid_until_text

router = Router()
STATUS = {
    "ACTIVE": "Активна",
    "DISABLED": "Отключена",
    "DELETED": "Удалена",
    "ERROR": "Ожидает восстановления",
}


@router.callback_query(F.data.regexp(r"^vpn:list(?::[0-9]+)?$"))
async def configs(
    query: CallbackQuery, db_user: User, sessions: async_sessionmaker[AsyncSession]
) -> None:
    parts = (query.data or "").split(":")
    before = int(parts[2]) if len(parts) == 3 else None
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(VpnConfig)
                .where(
                    VpnConfig.user_id == db_user.id,
                    VpnConfig.status != "DELETED",
                    *([VpnConfig.id < before] if before else []),
                )
                .order_by(VpnConfig.id.desc())
                .limit(21)
            )
        )
    visible = rows[:20]
    buttons = [[(f"{r.name} · {STATUS[r.status]}", f"vpn:view:{r.id}")] for r in visible]
    if len(rows) > 20:
        buttons += [[("Ещё конфигурации", f"vpn:list:{visible[-1].id}")]]
    if before:
        buttons += [[("В начало списка", "vpn:list")]]
    buttons += [[("➕ Создать VPN", "tariffs:list")], [("🏠 Главное меню", "menu")]]
    if isinstance(query.message, Message):
        await query.message.answer("Ваши VPN", reply_markup=keyboard(buttons))
    await query.answer()


@router.callback_query(F.data == "tariffs:list")
async def tariffs(query: CallbackQuery, sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(Tariff).where(Tariff.active.is_(True)).order_by(Tariff.id).limit(50)
            )
        )
    buttons = [
        [
            (
                f"{r.name} · {rub(r.daily_price or r.fixed_price or 0)}"
                + ("/сутки" if r.type == "PAYG" else f" / {r.duration_days} дней"),
                f"vpn:tariff:{r.id}",
            )
        ]
        for r in rows
    ]
    buttons.append([("🏠 Главное меню", "menu")])
    if isinstance(query.message, Message):
        await query.message.answer(
            "Выберите тариф для нового VPN.\nПервый период оплачивается при включении. Продление пакетов — вручную."
            if rows
            else "Активных тарифов пока нет.",
            reply_markup=keyboard(buttons),
        )
    await query.answer()


@router.callback_query(F.data.regexp(r"^vpn:tariff:\d+$"))
async def servers(query: CallbackQuery, sessions: async_sessionmaker[AsyncSession]) -> None:
    tariff_id = int((query.data or "").split(":")[-1])
    token = uuid.uuid4().hex[:16]
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(Server)
                .where(Server.active.is_(True), Server.health != "OFFLINE")
                .order_by(Server.priority)
                .limit(40)
            )
        )
    buttons = [[("⚡ Автоматически", f"vpn:new:{tariff_id}:0:{token}")]]
    buttons += [[(f"{r.country} · {r.name}", f"vpn:new:{tariff_id}:{r.id}:{token}")] for r in rows]
    buttons.append([("⬅️ Назад", "tariffs:list"), ("🏠 Главное меню", "menu")])
    if isinstance(query.message, Message):
        await query.message.answer("Выберите страну и сервер", reply_markup=keyboard(buttons))
    await query.answer()


async def show_config(query: CallbackQuery, row: VpnConfig, vpn_service: VpnService) -> None:
    if not isinstance(query.message, Message):
        return
    text = (
        f"{row.name}\nСтатус: {STATUS[row.status]}\nОплачено до: {paid_until_text(row.paid_until)}\nСтоимость: {rub(row.price)}"
        + ("/сутки" if row.mode == "PAYG" else f" / {row.duration_days} дней")
    )
    if row.status == "DELETED":
        await query.message.answer(text)
        return
    buttons = [
        [("▶️ Включить / оплатить период", f"vpn:enable:{row.id}:{config_revision(row)}")],
        [("⏸ Отключить", f"vpn:disable:{row.id}"), ("🗑 Удалить", f"vpn:confirm:{row.id}")],
        [("🚀 Мои VPN", "vpn:list")],
    ]
    await query.message.answer(text, reply_markup=keyboard(buttons))
    if row.connection_uri:
        value = vpn_service.decrypt(row.subscription_url or row.connection_uri)
        await query.message.answer(value, protect_content=True)
        output = io.BytesIO()
        qrcode.make(value).save(output)
        await query.message.answer_photo(
            BufferedInputFile(output.getvalue(), filename="vpn.png"),
            caption="QR-код подключения. Не передавайте его другим людям.",
            protect_content=True,
        )


@router.callback_query(F.data.regexp(r"^vpn:new:\d+:\d+:[a-f0-9]{16}$"))
async def create(query: CallbackQuery, db_user: User, vpn_service: VpnService) -> None:
    _, _, tariff, server, token = (query.data or "").split(":")
    row = await vpn_service.create(
        db_user.id, int(tariff), int(server) or None, f"vpn:{db_user.id}:{token}"
    )
    await query.answer()
    await show_config(query, row, vpn_service)


@router.callback_query(
    F.data.regexp(r"^vpn:(view|confirm|delete|disable|enable):[0-9]+(?::[a-f0-9]{12})?$")
)
async def action(
    query: CallbackQuery,
    db_user: User,
    vpn_service: VpnService,
    sessions: async_sessionmaker[AsyncSession],
    **data: Any,
) -> None:
    parts = (query.data or "").split(":")
    _, action_name, raw_id = parts[:3]
    config_id = int(raw_id)
    async with sessions() as session:
        row = await vpn_service.owned(session, db_user.id, config_id)
    if action_name == "confirm":
        if isinstance(query.message, Message):
            await query.message.answer(
                "Удалить конфигурацию? Неиспользованное время не возвращается.",
                reply_markup=keyboard(
                    [
                        [
                            ("Да, удалить", f"vpn:delete:{config_id}"),
                            ("Отмена", f"vpn:view:{config_id}"),
                        ]
                    ]
                ),
            )
    elif action_name in ("delete", "disable"):
        await vpn_service.request(db_user.id, config_id, action_name.upper())
        if isinstance(query.message, Message):
            await query.message.answer(
                "Запрос сохранён.", reply_markup=keyboard([[("Мои VPN", "vpn:list")]])
            )
    elif action_name == "enable":
        billing = data.get("billing_service")
        if not billing:
            raise DomainError("Биллинг пока недоступен.")
        if len(parts) != 4:
            raise DomainError("Кнопка устарела. Откройте конфигурацию заново перед оплатой.")
        await billing.activate(db_user.id, config_id, expected_revision=parts[3])
        async with sessions() as session:
            row = await vpn_service.owned(session, db_user.id, config_id)
        await show_config(query, row, vpn_service)
    else:
        await show_config(query, row, vpn_service)
    await query.answer()
