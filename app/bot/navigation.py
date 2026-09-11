from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import keyboard, menu
from app.config import Settings
from app.db.models import User

router = Router()


def controls(back: str = "menu") -> InlineKeyboardMarkup:
    return keyboard([[("↩️ Назад", "flow:back:" + back), ("❌ Отмена", "flow:cancel")]])


@router.callback_query(F.data == "flow:cancel")
@router.callback_query(F.data.regexp(r"^flow:back:(menu|balance|referral|admin)$"))
async def cancel(
    query: CallbackQuery,
    state: FSMContext,
    db_user: User | None,
    settings: Settings,
    bot: Bot,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await state.clear()
    target = (query.data or "").split(":")[-1]
    if target == "admin":
        from app.bot.admin import admin_keyboard
        from app.bot.security import require_admin

        require_admin(settings, query.from_user.id)
        if isinstance(query.message, Message):
            await query.message.answer("Панель администратора", reply_markup=admin_keyboard())
    elif target == "balance" and db_user:
        from app.bot.payments import balance

        await balance(query, db_user, sessions)
        return
    elif target == "referral" and db_user:
        from app.bot.community import referral

        await referral(query, db_user, bot, sessions)
        return
    elif isinstance(query.message, Message):
        await query.message.answer("Ввод отменён. Выберите действие.", reply_markup=menu())
    await query.answer()
