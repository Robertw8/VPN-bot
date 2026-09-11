from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    for row in rows:
        for _, data in row:
            if not data.startswith("url:") and not 1 <= len(data.encode("utf-8")) <= 64:
                raise ValueError("Telegram callback_data must be 1-64 UTF-8 bytes")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=text, url=data[4:])
                if data.startswith("url:")
                else InlineKeyboardButton(text=text, callback_data=data)
                for text, data in row
            ]
            for row in rows
        ]
    )


def menu() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [("🚀 Мои VPN", "vpn:list"), ("➕ Создать VPN", "tariffs:list")],
            [("💳 Баланс и пополнение", "balance:menu"), ("🎟 Тарифы", "tariffs:list")],
            [("🎁 Промокод", "promo:enter"), ("👥 Реферальная программа", "ref:menu")],
            [("🆘 Поддержка", "support"), ("📄 Информация", "info")],
        ]
    )
