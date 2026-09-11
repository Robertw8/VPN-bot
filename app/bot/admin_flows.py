"""Guided Telegram administration; business writes remain in AdminService."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import keyboard
from app.bot.security import require_admin
from app.config import Settings
from app.db.models import PromoCode, Server, Sponsor, Tariff, User, VpnConfig
from app.domain.admin_inputs import PromoInput, ServerInput, SponsorInput, TariffInput
from app.domain.errors import DomainError
from app.domain.money import amount, percent_text, rub
from app.services.admin import AdminService
from app.services.settings import BusinessSettings, get_settings

router = Router()
MODELS: dict[str, Any] = {
    "servers": Server,
    "tariffs": Tariff,
    "sponsors": Sponsor,
    "promos": PromoCode,
}
SCHEMAS: dict[str, Any] = {
    "servers": ServerInput,
    "tariffs": TariffInput,
    "sponsors": SponsorInput,
    "promos": PromoInput,
}
TITLES = {
    "servers": "🌐 VPN-серверы",
    "tariffs": "💰 Тарифы",
    "sponsors": "📢 Каналы",
    "promos": "🎁 Промокоды",
    "referrals": "👥 Реферальная программа",
    "topup": "🎁 Бонус пополнения",
    "settings": "⚙️ Настройки",
}


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    kind: str = "text"
    choices: tuple[tuple[str, Any], ...] = ()


BOOL = (("✅ Да", True), ("⛔ Нет", False))


def steps(section: str, values: dict[str, Any]) -> list[Step]:
    if section == "servers":
        return [
            Step("name", "Название сервера"),
            Step("country", "Страна"),
            Step("host", "Публичный hostname или IP (без https:// и порта)"),
            Step("port", "Порт подключения (1–65535)", "int"),
            Step("external_inbound_id", "Inbound ID. Для mock можно пропустить", "optional"),
            Step("max_clients", "Максимум клиентов. Пропустить — без ограничения", "optional_int"),
            Step("priority", "Приоритет: меньшее число выбирается первым (0–1000000)", "int"),
        ]
    if section == "tariffs":
        pricing = (
            [Step("daily_price", "Цена за сутки в рублях", "money")]
            if values.get("type", "PAYG") == "PAYG"
            else [
                Step("duration_days", "Длительность пакета в днях", "int"),
                Step("fixed_price", "Цена пакета в рублях", "money"),
            ]
        )
        return [
            Step("name", "Название тарифа"),
            Step(
                "type",
                "Тип тарифа",
                "choice",
                (("Посуточный", "PAYG"), ("Пакет дней", "SUBSCRIPTION")),
            ),
        ] + pricing
    if section == "sponsors":
        return [
            Step("title", "Название канала"),
            Step("chat_id", "Числовой ID канала, например -1001234567890", "chat_id"),
            Step("invite_url", "Ссылка на канал https://t.me/…"),
        ]
    if section == "promos":
        return [
            Step("code", "Промокод: латинские буквы, цифры, _ или -"),
            Step("value", "Бонус на баланс в рублях", "money"),
            Step("max_activations", "Общий лимит активаций", "int"),
            Step("per_user_limit", "Лимит на одного пользователя", "int"),
            Step(
                "starts_at",
                "Начало действия: ДД.ММ.ГГГГ ЧЧ:ММ (UTC). Пропустить — сейчас",
                "date_start",
            ),
            Step(
                "expires_at",
                "Конец действия: ДД.ММ.ГГГГ ЧЧ:ММ (UTC). Пропустить — бессрочно",
                "date_end",
            ),
        ]
    if section == "referrals":
        return [
            Step("referral_enabled", "Начислять реферальные бонусы?", "choice", BOOL),
            Step(
                "referral_invitee_bonus",
                "Бонус приглашённому в рублях (0 — выключить)",
                "zero_money",
            ),
            Step(
                "referral_inviter_bonus",
                "Бонус пригласившему в рублях (0 — выключить)",
                "zero_money",
            ),
            Step("referral_payment_bps", "Процент от оплаченных пополнений (0–100)", "percent"),
            Step("minimum_withdrawal", "Минимальная сумма вывода в рублях", "money"),
        ]
    if section == "topup":
        return [
            Step("topup_bonus_enabled", "Начислять бонус за пополнение?", "choice", BOOL),
            Step(
                "topup_bonus_minimum",
                "Минимальное пополнение для бонуса в рублях (0 — любая сумма)",
                "zero_money",
            ),
            Step("topup_bonus_bps", "Размер бонуса в процентах (0–100)", "percent"),
        ]
    return [
        Step("support_username", "Username поддержки без @. Можно пропустить", "optional_text"),
        Step("information", "Текст раздела «Информация» (до 3000 символов)"),
        Step("payment_presets", "Суммы пополнения в рублях через пробел (до 8 сумм)", "presets"),
        Step("maintenance", "Включить технические работы?", "choice", BOOL),
    ]


class Wizard(StatesGroup):
    editing = State()
    preview = State()


def display(value: Any, step: Step) -> str:
    if value is None or value == "":
        return "не задано"
    if step.kind in ("money", "zero_money"):
        return rub(value)
    if step.kind == "percent":
        return percent_text(value)
    if step.kind == "presets":
        return ", ".join(rub(c) for c in value)
    if step.choices:
        return next((label for label, v in step.choices if v == value), str(value))
    return str(value)


def parse_step(step: Step, text: str) -> Any:
    text = text.strip()
    if step.kind == "choice":
        raise DomainError("Выберите вариант кнопкой.")
    if step.kind in ("money", "zero_money", "percent"):
        cents = 0 if text in ("0", "0.00", "0,00") and step.kind != "money" else amount(text)
        if step.kind == "percent" and cents > 10000:
            raise DomainError("Процент должен быть от 0 до 100.")
        return cents
    if step.kind in ("int", "optional_int", "chat_id"):
        if len(text) > 16 or not text.lstrip("-").isdigit():
            raise DomainError("Введите целое число.")
        number = int(text)
        if step.kind == "chat_id":
            if number >= 0 or number < -(10**15):
                raise DomainError("ID канала должен быть отрицательным числом.")
        elif number < 0 or number > 1_000_000 or (number == 0 and step.key != "priority"):
            raise DomainError("Введите допустимое положительное число (для приоритета можно 0).")
        if step.key == "port" and number > 65535:
            raise DomainError("Порт должен быть от 1 до 65535.")
        return number
    if step.kind.startswith("date_"):
        from datetime import UTC

        try:
            return datetime.strptime(text, "%d.%m.%Y %H:%M").replace(tzinfo=UTC).isoformat()
        except ValueError:
            raise DomainError("Формат даты: ДД.ММ.ГГГГ ЧЧ:ММ (UTC).") from None
    if step.kind == "presets":
        return [amount(part) for part in text.split()]
    if not text or len(text) > (3000 if step.key == "information" else 500):
        raise DomainError("Проверьте длину введённого текста.")
    return text


def validate_values(section: str, values: dict[str, Any]) -> dict[str, Any]:
    if section == "tariffs":
        values = {**values}
        if values.get("type") == "PAYG":
            values["duration_days"], values["fixed_price"] = None, None
        else:
            values["daily_price"] = None
    try:
        if section in SCHEMAS:
            return cast(
                dict[str, Any], SCHEMAS[section].model_validate(values).model_dump(mode="json")
            )
        checked = BusinessSettings.model_validate(values).model_dump(mode="json")
        return checked
    except ValidationError as exc:
        fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
        labels = {s.key: s.label for s in steps(section, values)}
        for key, label in labels.items():
            fields = fields.replace(key, label)
        raise DomainError(
            "Проверьте поля: "
            + (fields or "параметры формы")
            + ". Нажмите «Назад», чтобы исправить."
        ) from None


async def render(message: Message, state: FSMContext) -> None:
    saved = await state.get_data()
    section, values, index, nonce = (
        saved["wizard_section"],
        saved["wizard_values"],
        saved["wizard_index"],
        saved["wizard_nonce"],
    )
    nonce = uuid.uuid4().hex[:16]
    await state.update_data(wizard_nonce=nonce)
    fields = steps(section, values)
    navigation = [[("↩️ Назад", f"wiz:back:{nonce}"), ("❌ Отмена", f"wiz:cancel:{nonce}")]]
    if index >= len(fields):
        await state.set_state(Wizard.preview)
        try:
            validate_values(section, values)
        except DomainError as exc:
            await message.answer(str(exc), reply_markup=keyboard(navigation))
            return
        summary = "Проверьте перед сохранением:\n\n" + "\n".join(
            f"{s.label.split(':')[0]}: {display(values.get(s.key), s)}" for s in fields
        )
        await message.answer(
            summary[:3500],
            reply_markup=keyboard([[("✅ Сохранить", f"wiz:save:{nonce}")]] + navigation),
        )
        return
    await state.set_state(Wizard.editing)
    field = fields[index]
    buttons = [[(label, f"wiz:pick:{nonce}:{i}")] for i, (label, _) in enumerate(field.choices)]
    if field.kind in ("optional", "optional_int", "optional_text", "date_start", "date_end"):
        buttons += [[("Пропустить", f"wiz:skip:{nonce}")]]
    if field.key in values:
        buttons += [[("Оставить текущее", f"wiz:keep:{nonce}")]]
    text = f"Шаг {index + 1} из {len(fields)}\n{field.label}"
    if field.key in values:
        text += "\nСейчас: " + display(values[field.key], field)
    await message.answer(text, reply_markup=keyboard(buttons + navigation))


async def begin(
    message: Message,
    state: FSMContext,
    section: str,
    values: dict[str, Any],
    row_id: int | None = None,
    version: str | None = None,
) -> None:
    await state.clear()
    await state.update_data(
        wizard_section=section,
        wizard_values=values,
        wizard_original=values.copy(),
        wizard_index=0,
        wizard_nonce=uuid.uuid4().hex[:16],
        wizard_id=row_id,
        wizard_version=version,
    )
    await render(message, state)


@router.callback_query(
    F.data.regexp(r"^admin:(servers|tariffs|sponsors|promos|referrals|settings)$")
)
@router.callback_query(F.data.regexp(r"^manage:list:(servers|tariffs|sponsors|promos):[0-9]+$"))
async def listing(
    query: CallbackQuery,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    require_admin(settings, query.from_user.id)
    await state.clear()
    if not isinstance(query.message, Message):
        return
    parts = (query.data or "").split(":")
    section = parts[1] if parts[0] == "admin" else parts[2]
    before = int(parts[-1]) if parts[0] == "manage" else 0
    buttons: list[list[tuple[str, str]]] = []
    async with sessions() as session:
        if section in MODELS:
            model = MODELS[section]
            rows = list(
                await session.scalars(
                    select(model)
                    .where(*([model.id < before] if before else []))
                    .order_by(model.id.desc())
                    .limit(21)
                )
            )
            for row in rows[:20]:
                title = getattr(row, "name", getattr(row, "title", getattr(row, "code", "")))
                buttons.append(
                    [
                        (
                            f"{'✅' if row.active else '⛔'} {title} · #{row.id}",
                            f"manage:card:{section}:{row.id}",
                        )
                    ]
                )
            if len(rows) > 20:
                buttons.append([("Ещё", f"manage:list:{section}:{rows[19].id}")])
            buttons.append([("➕ Добавить", f"manage:new:{section}")])
        else:
            buttons = [
                [("👥 Реферальная программа", "manage:new:referrals")],
                [("🎁 Бонус пополнения", "manage:new:topup")],
                [("⚙️ Поддержка и общие настройки", "manage:new:settings")],
            ]
            cfg = await get_settings(session)
            if section == "referrals":
                total = await session.scalar(
                    select(func.coalesce(func.sum(User.referral_earned), 0))
                )
                await query.message.answer(
                    f"Начислено по программе: {rub(total or 0)}\nПроцент: {percent_text(cfg.referral_payment_bps)}"
                )
    buttons.append([("↩️ Панель администратора", "manage:home")])
    await query.message.answer(TITLES[section], reply_markup=keyboard(buttons))
    await query.answer()


@router.callback_query(F.data == "manage:home")
async def home(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    from app.bot.admin import admin_keyboard

    require_admin(settings, query.from_user.id)
    await state.clear()
    if isinstance(query.message, Message):
        await query.message.answer("Панель администратора", reply_markup=admin_keyboard())
    await query.answer()


@router.callback_query(
    F.data.regexp(r"^manage:new:(servers|tariffs|sponsors|promos|referrals|topup|settings)$")
)
async def new(
    query: CallbackQuery,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    require_admin(settings, query.from_user.id)
    section = (query.data or "").split(":")[-1]
    values: dict[str, Any] = {"active": True} if section in MODELS else {}
    if section == "servers":
        values.update(
            provider_type=settings.vless_provider,
            port=443,
            priority=100,
            external_inbound_id=None,
            max_clients=None,
        )
    if section == "tariffs":
        values["type"] = "PAYG"
    if section == "promos":
        values.update(type="BALANCE_BONUS", per_user_limit=1)
    if section not in MODELS:
        async with sessions() as session:
            values = (await get_settings(session)).model_dump(mode="json")
    if isinstance(query.message, Message):
        await begin(query.message, state, section, values)
    await query.answer()


@router.callback_query(
    F.data.regexp(
        r"^manage:(card|edit|toggle|delete|remove):(servers|tariffs|sponsors|promos):[0-9]+$"
    )
)
async def card(
    query: CallbackQuery,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    require_admin(settings, query.from_user.id)
    _, action, section, raw_id = (query.data or "").split(":")
    row_id, model = int(raw_id), MODELS[section]
    async with sessions() as session:
        row = await session.get(model, row_id)
        if not row:
            raise DomainError("Запись уже удалена. Вернитесь к списку.")
        values = {key: getattr(row, key) for key in SCHEMAS[section].model_fields}
        version = row.updated_at.isoformat()
        count = (
            await session.scalar(
                select(func.count())
                .select_from(VpnConfig)
                .where(VpnConfig.server_id == row_id, VpnConfig.status != "DELETED")
            )
            if section == "servers"
            else None
        )
    if not isinstance(query.message, Message):
        return
    if action == "edit":
        await begin(
            query.message,
            state,
            section,
            SCHEMAS[section].model_validate(values).model_dump(mode="json"),
            row_id,
            version,
        )
    elif action in ("toggle", "delete"):
        # Store a one-use confirmation, so old keyboards cannot reverse later changes.
        await state.clear()
        nonce = uuid.uuid4().hex[:16]
        await state.set_state(Wizard.preview)
        await state.update_data(
            wizard_section=section,
            wizard_id=row_id,
            wizard_values={
                **SCHEMAS[section].model_validate(values).model_dump(mode="json"),
                "active": not values["active"],
            }
            if action == "toggle"
            else values,
            wizard_nonce=nonce,
            wizard_index=0,
            wizard_version=version,
            wizard_delete=action == "delete",
        )
        await query.message.answer(
            "Удалить сервер? Это разрешено только без истории конфигураций."
            if action == "delete"
            else ("Выключить запись?" if values["active"] else "Включить запись?"),
            reply_markup=keyboard(
                [[("✅ Подтвердить", f"wiz:save:{nonce}")], [("❌ Отмена", f"wiz:cancel:{nonce}")]]
            ),
        )
    elif action == "remove":
        raise DomainError("Откройте карточку и подтвердите удаление.")
    else:
        text = f"{values.get('name', values.get('title', values.get('code')))} · #{row_id}\nСтатус: {'включено' if row.active else 'выключено'}"
        if section == "servers":
            text += f"\nДоступность: {dict(HEALTHY='Доступен', DEGRADED='Требует проверки', OFFLINE='Недоступен', UNKNOWN='Не проверен')[row.health]}\nСтрана: {row.country}\nКлиенты: {count} / {row.max_clients or '∞'}\nПриоритет: {row.priority}\nАдрес: {row.host}:{row.port}"
        elif section == "tariffs":
            text += f"\nЦена: {rub(row.daily_price or row.fixed_price)}" + (
                " / сутки" if row.type == "PAYG" else f" / {row.duration_days} дней"
            )
        elif section == "promos":
            text += f"\nБонус: {rub(row.value)}\nАктивации: {row.activations_count} / {row.max_activations}"
        else:
            text += f"\n{row.invite_url}"
        buttons = [
            [
                (
                    "⛔ Выключить" if row.active else "✅ Включить",
                    f"manage:toggle:{section}:{row_id}",
                )
            ],
            [("✏️ Редактировать", f"manage:edit:{section}:{row_id}")],
        ]
        if section == "servers":
            buttons.append([("🗑 Удалить", f"manage:delete:{section}:{row_id}")])
        buttons.append([("↩️ Назад", f"admin:{section}")])
        await query.message.answer(text, reply_markup=keyboard(buttons))
    await query.answer()


@router.message(Wizard.editing, ~F.text.startswith("/"))
async def input_value(message: Message, state: FSMContext, settings: Settings) -> None:
    assert message.from_user
    require_admin(settings, message.from_user.id)
    saved = await state.get_data()
    if message.message_id <= saved.get("wizard_last_message", 0):
        return
    fields = steps(saved["wizard_section"], saved["wizard_values"])
    if saved["wizard_index"] >= len(fields):
        raise DomainError("Нажмите «Назад», чтобы исправить форму.")
    field = fields[saved["wizard_index"]]
    value = parse_step(field, message.text or "")
    values = {**saved["wizard_values"], field.key: value}
    await state.update_data(
        wizard_values=values,
        wizard_index=saved["wizard_index"] + 1,
        wizard_last_message=message.message_id,
    )
    await render(message, state)


@router.callback_query(
    F.data.regexp(r"^wiz:(back|cancel|save|skip|keep|pick):[a-f0-9]{16}(?::[0-9]+)?$")
)
async def control(
    query: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    require_admin(settings, query.from_user.id)
    parts = (query.data or "").split(":")
    action, nonce = parts[1:3]
    saved = await state.get_data()
    if saved.get("wizard_nonce") != nonce or await state.get_state() not in (
        Wizard.editing.state,
        Wizard.preview.state,
    ):
        raise DomainError("Эта форма уже закрыта. Откройте новую через /admin.")
    if not isinstance(query.message, Message):
        return
    if action == "cancel":
        await home(query, state, settings)
        return
    section = saved["wizard_section"]
    if action == "save":
        if await state.get_state() != Wizard.preview.state:
            raise DomainError("Сначала заполните форму.")
        service = AdminService(sessions, settings)
        row_id = saved.get("wizard_id")
        if saved.get("wizard_delete"):
            if row_id is None:
                raise DomainError("Запись не найдена.")
            await service.server_delete(
                query.from_user.id, row_id, expected_version=saved.get("wizard_version")
            )
        elif section in MODELS:
            checked = validate_values(section, saved["wizard_values"])
            await service.save_entity(
                query.from_user.id,
                section,
                checked,
                row_id,
                expected_version=saved.get("wizard_version"),
            )
        else:
            checked = validate_values(section, saved["wizard_values"])
            patch = {s.key: checked[s.key] for s in steps(section, checked)}
            await service.settings_patch(
                query.from_user.id,
                patch,
                expected={key: saved["wizard_original"].get(key) for key in patch},
            )
        await state.clear()
        await query.message.answer(
            "Сохранено.",
            reply_markup=keyboard(
                [[("↩️ К списку", f"admin:{section}" if section in MODELS else "admin:settings")]]
            ),
        )
        await query.answer()
        return
    if action == "back":
        index = max(0, saved["wizard_index"] - 1)
        await state.update_data(wizard_index=index)
    else:
        if await state.get_state() != Wizard.editing.state:
            raise DomainError("Откройте нужный шаг кнопкой «Назад».")
        fields = steps(section, saved["wizard_values"])
        field = fields[saved["wizard_index"]]
        values = saved["wizard_values"]
        if action == "pick":
            choice = int(parts[3]) if len(parts) == 4 else -1
            if not 0 <= choice < len(field.choices):
                raise DomainError("Недопустимый вариант.")
            values[field.key] = field.choices[choice][1]
        elif action == "skip":
            if field.kind not in (
                "optional",
                "optional_int",
                "optional_text",
                "date_start",
                "date_end",
            ):
                raise DomainError("Это поле обязательно.")
            from app.db.base import now

            values[field.key] = (
                now().isoformat()
                if field.kind == "date_start"
                else ("" if field.kind == "optional_text" else None)
            )
        elif action == "keep" and field.key not in values:
            raise DomainError("Сначала задайте значение.")
        await state.update_data(wizard_values=values, wizard_index=saved["wizard_index"] + 1)
    await render(query.message, state)
    await query.answer()
