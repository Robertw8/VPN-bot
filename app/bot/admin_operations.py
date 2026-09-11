"""Non-JSON forms for user lookup, wallet adjustment and payment operations."""

import uuid
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.admin import admin_keyboard, user_card
from app.bot.keyboards import keyboard
from app.bot.security import require_admin
from app.bot.texts import status_label
from app.config import Settings
from app.db.models import Payment, User, Withdrawal
from app.domain.errors import DomainError
from app.domain.money import amount, rub
from app.services.admin import AdminService
from app.services.payments import PaymentService
from app.services.vpn import VpnService

router = Router()


class Operation(StatesGroup):
    input = State()
    preview = State()


FIELDS = {
    "find": [("search", "Введите Telegram ID, @username или внутренний ID в виде #123")],
    "adjust": [
        ("user_id", "Введите Telegram ID или @username пользователя"),
        ("amount", "Сумма в рублях. Для списания поставьте минус, например -100"),
        ("comment", "Комментарий к изменению баланса (3–500 символов)"),
    ],
    "recover": [
        ("payment_id", "Внутренний номер платежа из списка"),
        ("external_id", "Номер invoice в Crypto Pay"),
    ],
}


async def prompt(message: Message, state: FSMContext) -> None:
    saved = await state.get_data()
    nonce = uuid.uuid4().hex[:16]
    await state.update_data(operation_nonce=nonce)
    fields = FIELDS[saved["operation"]]
    index = saved["operation_index"]
    nav = [[("↩️ Назад", f"ops:back:{nonce}"), ("❌ Отмена", "flow:back:admin")]]
    if index == len(fields):
        await state.set_state(Operation.preview)
        values = saved["operation_values"]
        summary = (
            f"Пользователь #{values['user_id']}\nИзменение: {rub(values['amount'])}\nКомментарий: {values['comment']}"
            if saved["operation"] == "adjust"
            else f"Восстановить платёж #{values['payment_id']} по invoice {values['external_id']}?"
        )
        await message.answer(
            summary, reply_markup=keyboard([[("✅ Подтвердить", f"ops:save:{nonce}")]] + nav)
        )
    else:
        await state.set_state(Operation.input)
        await message.answer(fields[index][1], reply_markup=keyboard(nav))


@router.callback_query(F.data.in_({"admin:users", "admin:payments", "admin:withdrawals"}))
async def section(
    query: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    require_admin(settings, query.from_user.id)
    await state.clear()
    if not isinstance(query.message, Message):
        return
    buttons: list[list[tuple[str, str]]] = []
    if query.data == "admin:users":
        buttons = [
            [("🔎 Найти пользователя", "ops:new:find")],
            [("💰 Начислить / списать", "ops:new:adjust")],
        ]
    else:
        async with sessions() as s:
            if query.data == "admin:payments":
                rows = list(await s.scalars(select(Payment).order_by(Payment.id.desc()).limit(30)))
                buttons = [
                    [
                        (
                            f"#{p.id} · {rub(p.amount)} · {status_label(p.status)}",
                            f"ops:payment:{p.id}",
                        )
                    ]
                    for p in rows
                ]
                buttons.append([("Восстановить неопределённый счёт", "ops:new:recover")])
            else:
                withdrawals = list(
                    await s.scalars(
                        select(Withdrawal)
                        .where(Withdrawal.status.in_(["PENDING", "APPROVED"]))
                        .order_by(Withdrawal.id)
                        .limit(30)
                    )
                )
                buttons = [
                    [
                        (
                            f"#{w.id} · {rub(w.amount)} · {status_label(w.status)}",
                            f"ops:withdraw:{w.id}",
                        )
                    ]
                    for w in withdrawals
                ]
    buttons.append([("↩️ Панель администратора", "manage:home")])
    await query.message.answer("Выберите действие / запись", reply_markup=keyboard(buttons))
    await query.answer()


@router.callback_query(F.data.regexp(r"^ops:new:(find|adjust|recover)$"))
async def new(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    require_admin(settings, query.from_user.id)
    await state.clear()
    await state.update_data(
        operation=(query.data or "").split(":")[-1],
        operation_index=0,
        operation_values={},
        operation_key=uuid.uuid4().hex,
    )
    if isinstance(query.message, Message):
        await prompt(query.message, state)
    await query.answer()


@router.callback_query(F.data.regexp(r"^ops:target:(adjust|recover):[0-9]+$"))
async def selected(
    query: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    require_admin(settings, query.from_user.id)
    _, _, operation, raw_id = (query.data or "").split(":")
    row_id = int(raw_id)
    if not 0 < row_id <= 2147483647:
        raise DomainError("Запись не найдена.")
    async with sessions() as session:
        row = await session.get(User if operation == "adjust" else Payment, row_id)
        if not row:
            raise DomainError("Запись не найдена.")
    await state.clear()
    await state.update_data(
        operation=operation,
        operation_index=1,
        operation_floor=1,
        operation_values={"user_id" if operation == "adjust" else "payment_id": row_id},
        operation_key=uuid.uuid4().hex,
    )
    if isinstance(query.message, Message):
        await prompt(query.message, state)
    await query.answer()


@router.message(Operation.input, ~F.text.startswith("/"))
async def input_value(
    message: Message,
    state: FSMContext,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    assert message.from_user
    require_admin(settings, message.from_user.id)
    saved = await state.get_data()
    if message.message_id <= saved.get("operation_last_message", 0):
        return
    text = (message.text or "").strip()
    key = FIELDS[saved["operation"]][saved["operation_index"]][0]
    value: Any = text
    if key == "search":
        try:
            criteria = (
                {"username": text[1:]}
                if text.startswith("@")
                else ({"id": int(text[1:])} if text.startswith("#") else {"telegram_id": int(text)})
            )
        except ValueError:
            raise DomainError("Введите числовой ID, #внутренний_ID или @username.") from None
        async with sessions() as s:
            await user_card(message, s, criteria)
        await state.clear()
        return
    if key == "user_id":
        async with sessions() as session:
            user = await session.scalar(
                select(User).where(
                    func.lower(User.username) == text[1:].lower()
                    if text.startswith("@")
                    else User.telegram_id
                    == (int(text) if text.isascii() and text.isdigit() and len(text) <= 18 else 0)
                )
            )
        if not user:
            raise DomainError("Пользователь не найден. Проверьте Telegram ID или @username.")
        value = user.id
    elif key in ("payment_id", "external_id"):
        if not text.isascii() or not text.isdigit() or len(text) > 18 or int(text) <= 0:
            raise DomainError("Введите положительный числовой ID.")
        value = text if key == "external_id" else int(text)
    elif key == "amount":
        value = -amount(text[1:]) if text.startswith("-") else amount(text)
    elif not 3 <= len(text) <= 500:
        raise DomainError("Комментарий должен содержать 3–500 символов.")
    await state.update_data(
        operation_values={**saved["operation_values"], key: value},
        operation_index=saved["operation_index"] + 1,
        operation_last_message=message.message_id,
    )
    await prompt(message, state)


@router.callback_query(F.data.regexp(r"^ops:(back|save):[a-f0-9]{16}$"))
async def control(
    query: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    payment_service: PaymentService,
) -> None:
    require_admin(settings, query.from_user.id)
    _, action, nonce = (query.data or "").split(":")
    saved = await state.get_data()
    if saved.get("operation_nonce") != nonce:
        raise DomainError("Эта форма уже закрыта. Откройте её заново.")
    if not isinstance(query.message, Message):
        return
    if action == "back":
        if saved["operation_index"] <= saved.get("operation_floor", 0):
            await state.clear()
            await query.message.answer("Панель администратора", reply_markup=admin_keyboard())
        else:
            await state.update_data(operation_index=saved["operation_index"] - 1)
            await prompt(query.message, state)
    else:
        if await state.get_state() != Operation.preview.state:
            raise DomainError("Сначала заполните форму.")
        values = saved["operation_values"]
        if saved["operation"] == "adjust":
            await AdminService(sessions, settings).adjust(
                query.from_user.id,
                values["user_id"],
                values["amount"],
                values["comment"],
                saved["operation_key"],
            )
        elif saved["operation"] == "withdrawal":
            await AdminService(sessions, settings).withdrawal(
                query.from_user.id, values["id"], values["status"]
            )
        else:
            await payment_service.recover(
                query.from_user.id, values["payment_id"], values["external_id"]
            )
        await state.clear()
        await query.message.answer("Операция выполнена.", reply_markup=admin_keyboard())
    await query.answer()


@router.callback_query(F.data.regexp(r"^ops:(payment|withdraw):[0-9]+$"))
async def inspect(
    query: CallbackQuery,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    payment_service: PaymentService,
    vpn_service: VpnService,
) -> None:
    require_admin(settings, query.from_user.id)
    _, kind, raw_id = (query.data or "").split(":")
    if not isinstance(query.message, Message):
        return
    async with sessions() as s:
        if kind == "payment":
            p = await s.get(Payment, int(raw_id))
            if not p:
                raise DomainError("Платёж не найден.")
            if p.external_payment_id:
                p = await payment_service.check(p.user_id, p.id)
            await query.message.answer(
                f"Платёж #{p.id}: {status_label(p.status)}",
                reply_markup=keyboard(
                    (
                        [[("Восстановить счёт", f"ops:target:recover:{p.id}")]]
                        if not p.external_payment_id
                        else []
                    )
                    + [[("↩️ Назад", "admin:payments")]]
                ),
            )
        else:
            w = await s.get(Withdrawal, int(raw_id))
            if not w:
                raise DomainError("Заявка не найдена.")
            choices = (
                [("Одобрить", "APPROVED"), ("Отклонить и вернуть деньги", "REJECTED")]
                if w.status == "PENDING"
                else (
                    [("Отметить выплаченной", "PAID"), ("Отклонить и вернуть деньги", "REJECTED")]
                    if w.status == "APPROVED"
                    else []
                )
            )
            await query.message.answer(
                f"Заявка #{w.id} · {rub(w.amount)}\n{vpn_service.decrypt(w.payment_details)}",
                protect_content=True,
                reply_markup=keyboard(
                    [[(label, f"ops:decision:{w.id}:{status}")] for label, status in choices]
                    + [[("↩️ Назад", "admin:withdrawals")]]
                ),
            )
    await query.answer()


@router.callback_query(F.data.regexp(r"^ops:decision:[0-9]+:(APPROVED|REJECTED|PAID)$"))
async def decision(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    require_admin(settings, query.from_user.id)
    _, _, raw_id, status = (query.data or "").split(":")
    nonce = uuid.uuid4().hex[:16]
    await state.clear()
    await state.set_state(Operation.preview)
    await state.update_data(
        operation="withdrawal",
        operation_nonce=nonce,
        operation_index=0,
        operation_values={"id": int(raw_id), "status": status},
    )
    if isinstance(query.message, Message):
        await query.message.answer(
            f"Заявка #{raw_id}: {status_label(status)}. Подтвердить?"
            + (
                "\nОтмечайте выплаченной только после реального перевода."
                if status == "PAID"
                else ""
            ),
            reply_markup=keyboard(
                [[("✅ Подтвердить", f"ops:save:{nonce}")], [("❌ Отмена", "flow:back:admin")]]
            ),
        )
    await query.answer()
