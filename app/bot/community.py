import uuid

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import keyboard, menu
from app.bot.navigation import controls
from app.db.models import User
from app.domain.errors import DomainError
from app.domain.money import amount, percent_text, rub
from app.services.promos import activate
from app.services.referrals import transfer, withdraw
from app.services.settings import get_settings
from app.services.vpn import VpnService

router = Router()


class CommunityState(StatesGroup):
    promo = State()
    withdrawal = State()


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=menu())


@router.callback_query(F.data.in_({"support", "info"}))
async def information(query: CallbackQuery, sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as s:
        cfg = await get_settings(s)
    text = (
        cfg.information
        if query.data == "info"
        else (
            f"Поддержка: @{cfg.support_username.lstrip('@')}"
            if cfg.support_username
            else "Контакт поддержки пока не указан администратором."
        )
    )
    if isinstance(query.message, Message):
        await query.message.answer(text, reply_markup=keyboard([[("Главное меню", "menu")]]))
    await query.answer()


@router.callback_query(F.data == "promo:enter")
async def promo_form(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CommunityState.promo)
    if isinstance(query.message, Message):
        await query.message.answer("Введите промокод. /cancel — отмена.", reply_markup=controls())
    await query.answer()


@router.message(CommunityState.promo, ~F.text.startswith("/"))
async def promo(
    message: Message, db_user: User, state: FSMContext, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions.begin() as s:
        cents = await activate(
            s, db_user.id, message.text or "", f"promo:{db_user.id}:{message.message_id}"
        )
    await state.clear()
    await message.answer(f"Промокод активирован: +{rub(cents)}", reply_markup=menu())


@router.callback_query(F.data == "ref:menu")
async def referral(
    query: CallbackQuery, db_user: User, bot: Bot, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as s:
        cfg = await get_settings(s)
        user = await s.get(User, db_user.id)
        assert user
        invited = await s.scalar(
            select(func.count()).select_from(User).where(User.referrer_id == user.id)
        )
    me = await bot.get_me()
    text = f"Реферальная программа\nhttps://t.me/{me.username}?start={user.referral_code}\nПриглашено: {invited}\nБаланс: {rub(user.referral_balance)}\nЗаработано всего: {rub(user.referral_earned)}\nПроцент от пополнений: {percent_text(cfg.referral_payment_bps)}\nМинимальный вывод: {rub(cfg.minimum_withdrawal)}"
    if not cfg.referral_enabled:
        text += "\nНачисление новых бонусов сейчас отключено."
    buttons = [
        [("Перевести на основной баланс", f"ref:transfer:{uuid.uuid4().hex[:16]}")],
        [("Заявка на вывод", "ref:withdraw")],
        [("Главное меню", "menu")],
    ]
    if isinstance(query.message, Message):
        await query.message.answer(text, reply_markup=keyboard(buttons))
    await query.answer()


@router.callback_query(F.data.regexp(r"^ref:transfer:[a-f0-9]{16}$"))
async def transfer_balance(
    query: CallbackQuery, db_user: User, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions.begin() as s:
        cents = await transfer(s, db_user.id, f"{db_user.id}:{query.data}")
    await query.answer(f"Переведено: {rub(cents)}", show_alert=True)


@router.callback_query(F.data == "ref:withdraw")
async def withdrawal_form(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CommunityState.withdrawal)
    await state.update_data(withdrawal_key=uuid.uuid4().hex)
    if isinstance(query.message, Message):
        await query.message.answer(
            "Введите сумму в рублях и реквизиты через |\nНапример: 200 | СБП, номер телефона, банк\nЗаявка резервирует средства. /cancel — отмена.",
            reply_markup=controls("referral"),
        )
    await query.answer()


@router.message(CommunityState.withdrawal, ~F.text.startswith("/"))
async def withdrawal(
    message: Message,
    db_user: User,
    state: FSMContext,
    sessions: async_sessionmaker[AsyncSession],
    vpn_service: VpnService,
) -> None:
    parts = (message.text or "").split("|", 1)
    if len(parts) != 2:
        raise DomainError("Используйте формат: сумма | реквизиты.")
    data = await state.get_data()
    async with sessions.begin() as s:
        row = await withdraw(
            s,
            db_user.id,
            amount(parts[0].strip()),
            parts[1].strip(),
            data["withdrawal_key"],
            vpn_service,
        )
    await state.clear()
    await message.answer(
        f"Заявка #{row.id} создана. Средства зарезервированы.", reply_markup=menu()
    )
