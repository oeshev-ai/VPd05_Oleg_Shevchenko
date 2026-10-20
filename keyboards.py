"""Фабрики inline-клавиатур.

Данные кнопок короче лимита Telegram (64 байта). Сумма расхода передаётся
отдельной строкой без двоеточия, чтобы разбор callback не сломался.
"""

from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import Trip

# Лимит Telegram Bot API на поле callback_data.
_CALLBACK_LIMIT = 64


class MenuCB(CallbackData, prefix="menu"):
    """Кнопки главного меню: new, trips, bal, hist, rate."""

    action: str


class RateCB(CallbackData, prefix="rate"):
    """Подтверждение курса: yes или manual."""

    action: str


class ExpenseCB(CallbackData, prefix="exp"):
    """Подтверждение расхода.

    ``action``: ``ok`` — учесть, ``nf`` — учесть даже в минус, ``no`` — отмена.
    ``amount`` — сумма в валюте назначения, как её ввёл пользователь.
    """

    action: str
    amount: str


class TripCB(CallbackData, prefix="trip"):
    """Переключение активной поездки по её id."""

    id: int


def _pack(data: CallbackData) -> str:
    """Упаковывает callback и проверяет лимит в 64 байта."""
    packed = data.pack()
    if len(packed.encode("utf-8")) > _CALLBACK_LIMIT:
        raise ValueError("Данные кнопки превышают лимит Telegram в 64 байта.")
    return packed


def _amount_token(amount: str) -> str:
    """Оставляет в сумме только текст, который безопасно класть в callback."""
    token = amount.strip().replace(",", ".")
    if not token or ":" in token or any(char.isspace() for char in token):
        raise ValueError("Некорректная сумма для кнопки.")
    return token


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню. Показывается на /start и после основных действий."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🌍 Новое путешествие",
        callback_data=_pack(MenuCB(action="new")),
    )
    builder.button(
        text="🎒 Мои путешествия",
        callback_data=_pack(MenuCB(action="trips")),
    )
    builder.button(
        text="💰 Баланс",
        callback_data=_pack(MenuCB(action="bal")),
    )
    builder.button(
        text="📜 История расходов",
        callback_data=_pack(MenuCB(action="hist")),
    )
    builder.button(
        text="⚙️ Изменить курс",
        callback_data=_pack(MenuCB(action="rate")),
    )
    builder.adjust(1)
    return builder.as_markup()


def rate_choice_keyboard() -> InlineKeyboardMarkup:
    """Согласие с курсом сервиса или переход к ручному вводу."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=_pack(RateCB(action="yes")))
    builder.button(
        text="✏️ Ввести вручную",
        callback_data=_pack(RateCB(action="manual")),
    )
    builder.adjust(2)
    return builder.as_markup()


def expense_confirm_keyboard(amount: str) -> InlineKeyboardMarkup:
    """Спрашивает, записать ли числовое сообщение как расход."""
    token = _amount_token(amount)
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да",
        callback_data=_pack(ExpenseCB(action="ok", amount=token)),
    )
    builder.button(
        text="❌ Нет",
        callback_data=_pack(ExpenseCB(action="no", amount=token)),
    )
    builder.adjust(2)
    return builder.as_markup()


def negative_balance_keyboard(amount: str) -> InlineKeyboardMarkup:
    """Повторное подтверждение, если расход уводит остаток в минус."""
    token = _amount_token(amount)
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, учесть",
        callback_data=_pack(ExpenseCB(action="nf", amount=token)),
    )
    builder.button(
        text="❌ Нет",
        callback_data=_pack(ExpenseCB(action="no", amount=token)),
    )
    builder.adjust(2)
    return builder.as_markup()


def trips_keyboard(trips: Sequence[Trip]) -> InlineKeyboardMarkup:
    """Список поездок. Нажатие делает выбранную поездку активной."""
    builder = InlineKeyboardBuilder()
    for trip in trips:
        mark = "✅ " if trip["is_active"] else ""
        title = f"{mark}{trip['name']}"
        if len(title) > 64:
            title = title[:63] + "…"
        builder.button(
            text=title,
            callback_data=_pack(TripCB(id=trip["id"])),
        )
    builder.adjust(1)
    return builder.as_markup()
