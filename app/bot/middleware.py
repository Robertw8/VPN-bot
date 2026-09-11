import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import keyboard
from app.bot.transport import notify_error
from app.config import Settings
from app.db.models import User
from app.domain.errors import DomainError
from app.logging import error_details
from app.services.settings import get_settings
from app.services.sponsors import missing_sponsors

log = structlog.get_logger()


class ContextMiddleware(BaseMiddleware):
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings
        self.last: OrderedDict[int, float] = OrderedDict()
        self.promo_attempts: OrderedDict[int, deque[float]] = OrderedDict()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message | CallbackQuery) or not event.from_user:
            return None
        tg = event.from_user
        if isinstance(event, Message) and event.chat.type != "private":
            await event.answer("Откройте личный чат с ботом.")
            return None
        if (
            isinstance(event, CallbackQuery)
            and event.message
            and event.message.chat.type != "private"
        ):
            await event.answer("Откройте личный чат с ботом.", show_alert=True)
            return None
        if isinstance(event, CallbackQuery) and event.message and event.message.chat.id != tg.id:
            await event.answer("Доступ запрещён.", show_alert=True)
            return None
        if isinstance(event, CallbackQuery) and not isinstance(event.message, Message):
            await event.answer("Сообщение устарело. Откройте меню заново.", show_alert=True)
            return None
        stamp = time.monotonic()
        if stamp - self.last.get(tg.id, 0) < 0.4:
            if isinstance(event, CallbackQuery):
                await event.answer("Пожалуйста, подождите.")
            return None
        self.last[tg.id] = stamp
        self.last.move_to_end(tg.id)
        if len(self.last) > 10000:
            self.last.popitem(last=False)
        try:
            async with self.sessions.begin() as session:
                user = await session.scalar(select(User).where(User.telegram_id == tg.id))
                if user:
                    user.username, user.first_name, user.last_name = (
                        tg.username,
                        tg.first_name,
                        tg.last_name,
                    )
                business = await get_settings(session)
            is_admin = tg.id in self.settings.admin_telegram_ids
            text = (event.text or "") if isinstance(event, Message) else (event.data or "")
            help_allowed = text in ("support", "info", "/cancel", "flow:cancel") or text.startswith(
                "flow:back:"
            )
            if user and user.status == "BLOCKED" and not is_admin and not help_allowed:
                raise DomainError("Ваш аккаунт заблокирован. Обратитесь в поддержку.")
            if business.maintenance and not is_admin and not help_allowed:
                raise DomainError("Идут технические работы. Попробуйте позже.")
            exempt = help_allowed or text.startswith("/start")
            if not user and not text.startswith(("/start", "/admin")):
                raise DomainError("Сначала нажмите /start.")
            if user and not is_admin and not exempt:
                async with self.sessions() as session:
                    missing, unavailable = await missing_sponsors(session, data["bot"], tg.id)
                if missing:
                    buttons = [[(s.title, "url:" + s.invite_url)] for s in missing]
                    buttons += [
                        [("✅ Проверить подписку", "sponsors:check")],
                        [("Поддержка", "support")],
                    ]
                    message = event if isinstance(event, Message) else event.message
                    if isinstance(message, Message):
                        await message.answer(
                            "Не удалось проверить канал. Администратор должен проверить права бота."
                            if unavailable
                            else "Для продолжения подпишитесь на каналы:",
                            reply_markup=keyboard(buttons),
                        )
                    if isinstance(event, CallbackQuery):
                        await event.answer()
                    return None
            fsm = data.get("state")
            if (
                isinstance(event, Message)
                and fsm
                and await fsm.get_state() == "CommunityState:promo"
            ):
                attempts = self.promo_attempts.setdefault(tg.id, deque())
                while attempts and stamp - attempts[0] > 60:
                    attempts.popleft()
                if len(attempts) >= 5:
                    raise DomainError("Слишком много попыток промокода. Подождите минуту.")
                attempts.append(stamp)
                self.promo_attempts.move_to_end(tg.id)
                if len(self.promo_attempts) > 10000:
                    self.promo_attempts.popitem(last=False)
            data.update(sessions=self.sessions, settings=self.settings, db_user=user)
            return await handler(event, data)
        except DomainError as exc:
            await notify_error(event, str(exc))
            return None
        except Exception as exc:
            log.error("operation_failed", **error_details(exc), telegram_id=tg.id)
            await notify_error(
                event, "Ошибка обработки. Попробуйте позже или обратитесь в поддержку."
            )
            return None
