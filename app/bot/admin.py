import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import keyboard
from app.bot.navigation import controls
from app.bot.security import require_admin
from app.bot.texts import status_label
from app.config import Settings
from app.db.models import Payment, PromoCode, Server, Sponsor, Tariff, User, VpnConfig, Withdrawal
from app.domain.errors import DomainError
from app.domain.money import rub
from app.services.admin import AdminService
from app.services.settings import get_settings
from app.services.vpn import VpnService

router = Router()
SECTIONS = {
    "stats": "📊 Статистика",
    "users": "👤 Пользователи",
    "servers": "🌐 VPN-серверы",
    "tariffs": "💰 Тарифы",
    "payments": "💳 Платежи",
    "promos": "🎁 Промокоды",
    "referrals": "👥 Рефералы",
    "sponsors": "📢 Спонсоры",
    "settings": "⚙️ Настройки",
    "withdrawals": "💸 Выводы",
}
MODELS: dict[str, type[Server] | type[Tariff] | type[PromoCode] | type[Sponsor]] = {
    "servers": Server,
    "tariffs": Tariff,
    "promos": PromoCode,
    "sponsors": Sponsor,
}
EXAMPLES = {
    "servers": '{"name":"Амстердам","country":"Нидерланды","host":"mock.example","provider_type":"mock","max_clients":100,"priority":10}',
    "tariffs": '{"name":"Посуточный","type":"PAYG","daily_price":666}\nПакет: {"name":"30 дней","type":"SUBSCRIPTION","duration_days":30,"fixed_price":19900}',
    "promos": '{"code":"WELCOME","value":2500,"max_activations":100,"per_user_limit":1}',
    "sponsors": '{"chat_id":-1001234567890,"title":"Наш канал","invite_url":"https://t.me/channel"}',
    "payments": '{"id":1,"external_id":"12345"}. Восстановление неопределённого счёта: ID invoice из Crypto Pay. Параметры будут проверены через API.',
    "settings": '{"support_username":"support","referral_enabled":true,"referral_payment_bps":2000}',
    "users": '{"telegram_id":123456789} или {"username":"username"} или {"id":1}',
    "adjust": '{"id":1,"amount":10000,"comment":"Компенсация"}. Списание: отрицательная сумма.',
    "withdrawals": '{"id":1,"status":"APPROVED"}. Статусы: APPROVED (одобрить), REJECTED (вернуть резерв), PAID (выплачено вручную).',
}


class AdminState(StatesGroup):
    input = State()


def admin_keyboard() -> InlineKeyboardMarkup:
    return keyboard([[(label, f"admin:{name}")] for name, label in SECTIONS.items()])


async def send_long(message: Message, text: str) -> None:
    for offset in range(0, len(text), 3500):
        await message.answer(text[offset : offset + 3500])


@router.message(Command("admin"))
async def dashboard(message: Message, settings: Settings, state: FSMContext) -> None:
    assert message.from_user
    require_admin(settings, message.from_user.id)
    await state.clear()
    await message.answer("Панель администратора", reply_markup=admin_keyboard())


@router.callback_query(F.data.regexp(r"^admin:[a-z]+$"))
async def section(
    query: CallbackQuery,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    require_admin(settings, query.from_user.id)
    if not isinstance(query.message, Message):
        return
    name = (query.data or "").split(":")[-1]
    if name not in SECTIONS:
        raise DomainError("Раздел не найден.")
    await state.clear()
    buttons: list[list[tuple[str, str]]] = []
    async with sessions() as session:
        if name == "stats":
            today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            counts = []
            stats_specs: list[tuple[str, Any, list[Any]]] = [
                ("Всего пользователей", User, []),
                ("Новых сегодня", User, [User.created_at >= today]),
                ("Активных VPN", VpnConfig, [VpnConfig.status == "ACTIVE"]),
                ("Активных серверов", Server, [Server.active.is_(True)]),
                ("Платежей создано сегодня", Payment, [Payment.created_at >= today]),
                ("Ожидающих выводов", Withdrawal, [Withdrawal.status == "PENDING"]),
            ]
            for label, model, criteria in stats_specs:
                value = await session.scalar(
                    select(func.count()).select_from(model).where(*criteria)
                )
                counts.append(f"{label}: {value}")
            paid = await session.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.status == "PAID", Payment.paid_at >= today
                )
            )
            text = (
                "Статистика за сегодня (UTC)\n"
                + "\n".join(counts)
                + f"\nОплачено сегодня: {rub(paid or 0)}"
            )
        elif name in MODELS:
            model = MODELS[name]
            rows = cast(
                list[Server | Tariff | PromoCode | Sponsor],
                list(await session.scalars(select(model).order_by(model.id.desc()).limit(30))),
            )
            text = (
                SECTIONS[name]
                + " (последние 30)\n"
                + "\n".join(
                    f"#{r.id} {getattr(r, 'name', getattr(r, 'code', getattr(r, 'title', '')))} · {'включено' if r.active else 'выключено'}"
                    for r in rows
                )
            )
            buttons = [
                [("Добавить", f"adm:form:{name}:add"), ("Изменить", f"adm:form:{name}:edit")],
                [("Посмотреть поля по ID", f"adm:form:{name}:view")],
            ]
            if name == "servers":
                buttons += [[("Удалить неиспользованный", "adm:form:servers:delete")]]
        elif name == "settings":
            cfg = await get_settings(session)
            text = (
                "Настройки. Деньги — в копейках, процент — в базисных пунктах (2000 = 20%).\n"
                + json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2)
            )
            buttons = [[("Изменить настройки", "adm:form:settings:edit")]]
        elif name == "users":
            text = "Найти пользователя по Telegram ID, username или внутреннему ID."
            buttons = [
                [("Найти", "adm:form:users:view")],
                [("Начислить / списать", "ops:new:adjust")],
            ]
        elif name == "referrals":
            invited = await session.scalar(
                select(func.count()).select_from(User).where(User.referrer_id.is_not(None))
            )
            total = await session.scalar(select(func.coalesce(func.sum(User.referral_earned), 0)))
            text = f"Приглашено: {invited}\nНачислено всего: {rub(total or 0)}"
            buttons = [[("Настроить программу", "admin:settings")]]
        elif name == "payments":
            payments = list(
                await session.scalars(select(Payment).order_by(Payment.id.desc()).limit(30))
            )
            text = "Последние платежи:\n" + "\n".join(
                f"#{p.id} · пользователь #{p.user_id} · {rub(p.amount)} · {status_label(p.status)}"
                for p in payments
            )
            buttons = [
                [("Проверить по ID", "adm:form:payments:view")],
                [("Восстановить счёт", "adm:form:payments:edit")],
            ]
        else:
            withdrawals = list(
                await session.scalars(
                    select(Withdrawal)
                    .where(Withdrawal.status.in_(["PENDING", "APPROVED"]))
                    .order_by(Withdrawal.id)
                    .limit(30)
                )
            )
            text = "Выводы (первые 30 незавершённых):\n" + "\n".join(
                f"#{w.id} · пользователь #{w.user_id} · {rub(w.amount)} · {status_label(w.status)}"
                for w in withdrawals
            )
            buttons = [
                [("Реквизиты по ID", "adm:form:withdrawals:view")],
                [("Обработать заявку", "adm:form:withdrawals:edit")],
            ]
    await send_long(query.message, text)
    if buttons:
        await query.message.answer("Действия", reply_markup=keyboard(buttons))
    await query.answer()


@router.callback_query(F.data.regexp(r"^adm:form:[a-z]+:(add|edit|view|delete)$"))
async def form(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    require_admin(settings, query.from_user.id)
    _, _, section_name, action = (query.data or "").split(":")
    await state.set_state(AdminState.input)
    await state.update_data(
        admin_section=section_name, admin_action=action, admin_key=uuid.uuid4().hex
    )
    example = EXAMPLES.get(section_name, '{"id":1}')
    if action in ("view", "delete") and section_name != "users":
        example = '{"id":1}'
    if isinstance(query.message, Message):
        await query.message.answer(
            "Отправьте JSON. Денежные значения — целые копейки. Для изменения существующей записи добавьте id и все поля; сначала можно посмотреть её поля. Настройки обновляются частично.\n"
            + example
            + "\n/cancel — отмена.",
            reply_markup=controls("admin"),
        )
    await query.answer()


async def user_card(message: Message, session: AsyncSession, values: dict[str, Any]) -> None:
    if "telegram_id" in values:
        criteria = User.telegram_id == int(values["telegram_id"])
    elif "username" in values:
        criteria = func.lower(User.username) == str(values["username"]).lstrip("@").lower()
    else:
        criteria = User.id == int(values["id"])
    user = await session.scalar(select(User).where(criteria))
    if not user:
        raise DomainError("Пользователь не найден.")
    count = await session.scalar(
        select(func.count())
        .select_from(VpnConfig)
        .where(VpnConfig.user_id == user.id, VpnConfig.status == "ACTIVE")
    )
    text = f"Пользователь #{user.id}\nTelegram ID: {user.telegram_id}\n@{user.username or '—'}\nБаланс: {rub(user.balance)}\nРеферальный: {rub(user.referral_balance)}\nАктивных VPN: {count}\nПригласивший: {user.referrer_id or '—'}\nСтатус: {'активен' if user.status == 'ACTIVE' else 'заблокирован'}\nСоздан: {user.created_at:%d.%m.%Y %H:%M} UTC"
    await message.answer(
        text,
        reply_markup=keyboard(
            [
                [
                    ("Заблокировать", f"adm:user:block:{user.id}"),
                    ("Разблокировать", f"adm:user:unblock:{user.id}"),
                ],
                [("VPN", f"adm:user:vpn:{user.id}"), ("Платежи", f"adm:user:payments:{user.id}")],
                [("Начислить / списать", f"ops:target:adjust:{user.id}")],
            ]
        ),
    )


@router.message(AdminState.input, ~F.text.startswith("/"))
async def admin_input(
    message: Message,
    settings: Settings,
    state: FSMContext,
    sessions: async_sessionmaker[AsyncSession],
    vpn_service: VpnService,
    **data: Any,
) -> None:
    assert message.from_user
    actor = message.from_user.id
    require_admin(settings, actor)
    saved = await state.get_data()
    section_name, action = saved["admin_section"], saved["admin_action"]
    try:
        values = json.loads(message.text or "")
        if not isinstance(values, dict):
            raise ValueError
        service = AdminService(sessions, settings)
        if action == "view":
            async with sessions() as session:
                if section_name == "users":
                    await user_card(message, session, values)
                elif section_name in MODELS:
                    row = await session.get(MODELS[section_name], int(values["id"]))
                    if not row:
                        raise DomainError("Запись не найдена.")
                    fields = {
                        c.name: getattr(row, c.name)
                        for c in row.__table__.columns
                        if c.name not in ("created_at", "updated_at", "activations_count")
                    }
                    await send_long(
                        message, json.dumps(fields, ensure_ascii=False, indent=2, default=str)
                    )
                elif section_name == "withdrawals":
                    withdrawal = await session.get(Withdrawal, int(values["id"]))
                    if not withdrawal:
                        raise DomainError("Заявка не найдена.")
                    await message.answer(
                        f"Заявка #{withdrawal.id}\n"
                        + vpn_service.decrypt(withdrawal.payment_details),
                        protect_content=True,
                    )
                elif section_name == "payments":
                    payment = await session.get(Payment, int(values["id"]))
                    if not payment:
                        raise DomainError("Платёж не найден.")
                    result = await data["payment_service"].check(payment.user_id, payment.id)
                    await message.answer(f"Платёж #{result.id}: {status_label(result.status)}")
                else:
                    raise ValueError
        elif section_name == "payments" and action == "edit":
            result = await data["payment_service"].recover(
                actor, int(values["id"]), str(values["external_id"])
            )
            await message.answer(f"Счёт восстановлен: #{result.id}")
        elif section_name == "settings":
            await service.settings_patch(actor, values)
        elif section_name == "adjust":
            await service.adjust(
                actor,
                int(values["id"]),
                values["amount"],
                str(values["comment"]),
                saved["admin_key"],
            )
        elif section_name == "withdrawals":
            await service.withdrawal(actor, int(values["id"]), str(values["status"]))
        elif section_name == "servers" and action == "delete":
            await service.server_delete(actor, int(values["id"]))
        elif section_name in MODELS and action in ("add", "edit"):
            row_id = values.pop("id", None)
            if action == "edit" and (
                not isinstance(row_id, int) or isinstance(row_id, bool) or row_id <= 0
            ):
                raise ValueError
            if action == "add" and row_id is not None:
                raise ValueError
            await service.save_entity(actor, section_name, values, row_id)
        else:
            raise ValueError
    except (ValueError, TypeError, KeyError, ValidationError):
        await message.answer(
            "Проверьте формат JSON, обязательные поля и значения. /cancel — отмена."
        )
        return
    except IntegrityError:
        await message.answer("Значения конфликтуют с существующей записью или ограничениями БД.")
        return
    await state.clear()
    await message.answer("Готово.", reply_markup=admin_keyboard())


@router.callback_query(F.data.regexp(r"^adm:user:(block|unblock|vpn|payments):\d+$"))
async def user_action(
    query: CallbackQuery,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    vpn_service: VpnService,
) -> None:
    require_admin(settings, query.from_user.id)
    _, _, action, raw_id = (query.data or "").split(":")
    user_id = int(raw_id)
    if action in ("block", "unblock"):
        await AdminService(sessions, settings).block(query.from_user.id, user_id, action == "block")
        await vpn_service.reconcile_pending()
        await query.answer("Статус обновлён.")
        return
    async with sessions() as session:
        if action == "vpn":
            configs = list(
                await session.scalars(
                    select(VpnConfig)
                    .where(VpnConfig.user_id == user_id)
                    .order_by(VpnConfig.id.desc())
                    .limit(30)
                )
            )
            text = "Конфигурации:\n" + "\n".join(
                f"#{c.id} {c.name} · {status_label(c.status)}" for c in configs
            )
        else:
            payments = list(
                await session.scalars(
                    select(Payment)
                    .where(Payment.user_id == user_id)
                    .order_by(Payment.id.desc())
                    .limit(30)
                )
            )
            text = "Платежи:\n" + "\n".join(
                f"#{p.id} {rub(p.amount)} · {status_label(p.status)}" for p in payments
            )
    if isinstance(query.message, Message):
        await send_long(query.message, text)
    await query.answer()
