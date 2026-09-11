from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import keyboard, menu
from app.config import Settings
from app.db.models import User, VpnConfig
from app.domain.money import rub
from app.services.sponsors import missing_sponsors
from app.services.users import register

router = Router()


async def menu_text(sessions: async_sessionmaker[AsyncSession], user_id: int) -> str:
    async with sessions() as session:
        user = await session.get(User, user_id)
        assert user
        count = await session.scalar(
            select(func.count())
            .select_from(VpnConfig)
            .where(VpnConfig.user_id == user_id, VpnConfig.status == "ACTIVE")
        )
        return f"Главное меню\n\nБаланс: {rub(user.balance)}\nАктивных VPN: {count}"


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    sessions: async_sessionmaker[AsyncSession],
    bot: Bot,
    settings: Settings,
    state: FSMContext,
) -> None:
    await state.clear()
    assert message.from_user
    tg = message.from_user
    async with sessions.begin() as session:
        user = await register(
            session, tg.id, tg.username, tg.first_name, tg.last_name, command.args
        )
    if tg.id not in settings.admin_telegram_ids:
        async with sessions() as session:
            missing, unavailable = await missing_sponsors(session, bot, tg.id)
        if missing:
            await message.answer(
                "Не удалось проверить канал. Обратитесь в поддержку."
                if unavailable
                else "Подпишитесь на каналы для продолжения:",
                reply_markup=keyboard(
                    [[(s.title, "url:" + s.invite_url)] for s in missing]
                    + [[("✅ Проверить подписку", "sponsors:check")], [("Поддержка", "support")]]
                ),
            )
            return
    await message.answer(await menu_text(sessions, user.id), reply_markup=menu())


@router.callback_query(F.data.in_({"menu", "sponsors:check"}))
async def home(
    query: CallbackQuery,
    sessions: async_sessionmaker[AsyncSession],
    db_user: User | None,
    state: FSMContext,
) -> None:
    await state.clear()
    if not db_user or not isinstance(query.message, Message):
        await query.answer("Нажмите /start")
        return
    await query.message.answer(await menu_text(sessions, db_user.id), reply_markup=menu())
    await query.answer()
