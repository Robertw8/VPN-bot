"""Bounded plain-text output and narrow recovery of known Telegram UI failures."""

from typing import Any

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

from app.logging import error_details

log = structlog.get_logger()


def text_chunks(text: str, limit: int = 4096) -> list[str]:
    chunks, current, size = [], "", 0
    for char in text:
        width = len(char.encode("utf-16-le")) // 2
        if size + width > limit:
            chunks.append(current)
            current, size = "", 0
        current += char
        size += width
    if current:
        chunks.append(current)
    return chunks


class TelegramTransport:
    async def __call__(self, make_request: Any, bot: Bot, method: Any) -> Any:
        if isinstance(method, SendMessage | EditMessageText):
            # All current product messages are plain text. No inferred parse mode.
            method = method.model_copy(update={"parse_mode": None})
        if isinstance(method, SendMessage) and not method.entities:
            chunks = text_chunks(method.text)
            if len(chunks) > 1:
                result = None
                for index, chunk in enumerate(chunks):
                    part = method.model_copy(
                        update={
                            "text": chunk,
                            "reply_markup": method.reply_markup
                            if index == len(chunks) - 1
                            else None,
                        }
                    )
                    result = await self(make_request, bot, part)
                return result
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            description = exc.message.lower()
            if isinstance(method, AnswerCallbackQuery) and (
                "query is too old" in description or "query id is invalid" in description
            ):
                log.info("telegram_callback_expired")
                return True
            if isinstance(method, EditMessageText):
                if "message is not modified" in description:
                    return True
                if (
                    "message to edit not found" in description
                    or "message can't be edited" in description
                ) and method.chat_id:
                    log.info("telegram_edit_replaced")
                    return await self(
                        make_request,
                        bot,
                        SendMessage(
                            chat_id=method.chat_id,
                            text=method.text or "",
                            reply_markup=method.reply_markup,
                            parse_mode=None,
                        ),
                    )
            log.error("telegram_request_failed", method=type(method).__name__, **error_details(exc))
            raise
        except TelegramAPIError as exc:
            log.error("telegram_request_failed", method=type(method).__name__, **error_details(exc))
            raise


async def notify_error(event: Any, text: str) -> None:
    from aiogram.types import CallbackQuery

    try:
        if isinstance(event, CallbackQuery):
            await event.answer(text[:190], show_alert=True)
        else:
            await event.answer(text)
    except TelegramAPIError as exc:
        # Do not recursively try to report a failed response to a blocked/deleted chat.
        log.error("telegram_error_delivery_failed", **error_details(exc))
