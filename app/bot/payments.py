import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import keyboard
from app.bot.navigation import controls
from app.db.models import Payment, User
from app.domain.money import amount, rub
from app.services.payments import PaymentService
from app.services.settings import get_settings

router = Router()


class Topup(StatesGroup):
    amount = State()


def methods(cents: int) -> InlineKeyboardMarkup:
    token = uuid.uuid4().hex[:16]
    return keyboard(
        [
            [("CryptoBot", f"pay:new:cryptobot:{cents}:{token}")],
            [("Карта / СБП", f"pay:new:card_sbp:{cents}:{token}")],
        ]
    )


@router.callback_query(F.data == "balance:menu")
async def balance(
    query: CallbackQuery, db_user: User, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as s:
        cfg = await get_settings(s)
        rows = list(
            await s.scalars(
                select(Payment)
                .where(Payment.user_id == db_user.id, Payment.status == "PENDING")
                .order_by(Payment.id.desc())
                .limit(5)
            )
        )
    buttons = [[(rub(cents), f"pay:amount:{cents}")] for cents in cfg.payment_presets]
    buttons += [[("Другая сумма", "pay:custom")]]
    buttons += [[(f"Проверить платёж #{row.id}", f"pay:check:{row.id}")] for row in rows]
    buttons += [[("Главное меню", "menu")]]
    if isinstance(query.message, Message):
        await query.message.answer("Выберите сумму пополнения", reply_markup=keyboard(buttons))
    await query.answer()


@router.callback_query(F.data == "pay:custom")
async def custom(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Topup.amount)
    if isinstance(query.message, Message):
        await query.message.answer(
            "Введите сумму в рублях. /cancel — отмена.", reply_markup=controls("balance")
        )
    await query.answer()


@router.message(Topup.amount, ~F.text.startswith("/"))
async def custom_amount(message: Message, state: FSMContext) -> None:
    cents = amount(message.text or "")
    await state.clear()
    await message.answer(
        f"Пополнение на {rub(cents)}. Выберите способ оплаты.", reply_markup=methods(cents)
    )


@router.callback_query(F.data.regexp(r"^pay:amount:\d+$"))
async def preset(query: CallbackQuery) -> None:
    cents = int((query.data or "").split(":")[-1])
    if isinstance(query.message, Message):
        await query.message.answer(
            f"Пополнение на {rub(cents)}. Выберите способ оплаты.", reply_markup=methods(cents)
        )
    await query.answer()


@router.callback_query(F.data.regexp(r"^pay:new:(cryptobot|card_sbp):\d+:[a-f0-9]{16}$"))
async def create(query: CallbackQuery, db_user: User, payment_service: PaymentService) -> None:
    _, _, provider, raw_amount, token = (query.data or "").split(":")
    payment = await payment_service.create(
        db_user.id, provider, int(raw_amount), f"pay:{db_user.id}:{token}"
    )
    buttons = [[("Проверить оплату", f"pay:check:{payment.id}")]]
    if payment.payment_url:
        buttons.insert(0, [("Оплатить", "url:" + payment.payment_url)])
    if isinstance(query.message, Message):
        await query.message.answer(
            f"Платёж #{payment.id} · {rub(payment.amount)}\nПосле оплаты нажмите «Проверить оплату»."
            if payment.payment_url
            else f"Платёж #{payment.id}: создание счёта уточняется. Обратитесь в поддержку.",
            reply_markup=keyboard(buttons),
        )
    await query.answer()


@router.callback_query(F.data.regexp(r"^pay:check:\d+$"))
async def check(query: CallbackQuery, db_user: User, payment_service: PaymentService) -> None:
    payment = await payment_service.check(db_user.id, int((query.data or "").split(":")[-1]))
    text = {
        "PAID": "Оплачено. Баланс пополнен.",
        "PENDING": "Оплата пока не поступила.",
        "EXPIRED": "Срок счёта истёк.",
    }.get(payment.status, "Платёж не завершён.")
    await query.answer(text, show_alert=True)
