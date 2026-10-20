"""Числовое сообщение становится расходом только после подтверждения."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database import add_expense, list_trips
from handlers.cleanup import leave_only_menu
from handlers.common import (
    NO_ACTIVE_TRIP,
    NOT_A_NUMBER,
    NEGATIVE_BALANCE,
    amount_token,
    format_amount,
    format_balance_line,
    overdraws,
    parse_positive_number,
    split_expense,
)
from keyboards import (
    ExpenseCB,
    RateCB,
    expense_confirm_keyboard,
    main_menu_keyboard,
    negative_balance_keyboard,
)

router = Router(name="expense_handler")

# Подтверждение привязано к пользователю и поездке, чтобы повторное нажатие
# не списало ту же сумму второй раз.
_pending: dict[int, tuple[int, str]] = {}


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_free_text(message: Message) -> None:
    """Вне диалога число предлагает записать расход, прочий текст объясняет формат."""
    if message.from_user is None or message.text is None:
        return
    user_id = message.from_user.id
    amount = parse_positive_number(message.text)
    trips = await list_trips(user_id)
    trip = next((item for item in trips if item["is_active"]), None)
    if amount is None:
        if trip is None:
            await message.answer(NO_ACTIVE_TRIP, reply_markup=main_menu_keyboard())
        else:
            await message.answer(NOT_A_NUMBER)
        return
    if trip is None:
        await leave_only_menu(message, NO_ACTIVE_TRIP)
        return

    token = amount_token(amount)
    target_amount, home_amount = split_expense(amount, trip["exchange_rate"])
    try:
        markup = expense_confirm_keyboard(token)
    except ValueError:
        await message.answer(NOT_A_NUMBER)
        return

    _pending[user_id] = (trip["id"], token)
    await message.answer(
        "💸 Расход: "
        f"{format_amount(target_amount)} {trip['target_currency']}"
        f" = {format_amount(home_amount)} {trip['home_currency']}. "
        "Учесть как расход?",
        reply_markup=markup,
    )


@router.callback_query(ExpenseCB.filter())
async def on_expense_callback(callback: CallbackQuery, callback_data: ExpenseCB) -> None:
    """Подтверждение, отказ или повторный вопрос при уходе баланса в минус."""
    if callback.from_user is None:
        return
    user_id = callback.from_user.id
    pending = _pending.get(user_id)
    if pending is None or pending[1] != callback_data.amount:
        await callback.answer("Это подтверждение уже неактуально.", show_alert=True)
        return

    if callback_data.action == "no":
        _pending.pop(user_id, None)
        await leave_only_menu(callback, "Хорошо, расход не записан.")
        return

    if callback_data.action not in {"ok", "nf"}:
        await callback.answer()
        return

    trip_id, token = pending
    trips = await list_trips(user_id)
    trip = next((item for item in trips if item["id"] == trip_id), None)
    amount = parse_positive_number(token)
    if trip is None or amount is None:
        _pending.pop(user_id, None)
        await callback.answer("Путешествие не найдено.", show_alert=True)
        return

    target_amount, home_amount = split_expense(amount, trip["exchange_rate"])
    if callback_data.action == "ok" and overdraws(
        trip["home_balance"],
        trip["target_balance"],
        home_amount,
        target_amount,
    ):
        await _show(callback, NEGATIVE_BALANCE, negative_balance_keyboard(token))
        return

    try:
        await add_expense(trip["id"], target_amount, home_amount)
    except ValueError as exc:
        _pending.pop(user_id, None)
        await callback.answer(str(exc), show_alert=True)
        return

    _pending.pop(user_id, None)
    updated_rows = await list_trips(user_id)
    updated = next((item for item in updated_rows if item["id"] == trip_id), None)
    if updated is None:
        await leave_only_menu(callback, "Расход записан.")
        return
    await leave_only_menu(
        callback,
        format_balance_line(
            updated["target_balance"],
            updated["target_currency"],
            updated["home_balance"],
            updated["home_currency"],
        ),
    )


@router.callback_query(RateCB.filter())
async def on_stale_rate(callback: CallbackQuery) -> None:
    """Старая кнопка курса, если диалог уже закрыт."""
    await callback.answer(
        "Это действие уже недоступно. Запросите курс ещё раз.",
        show_alert=True,
    )


async def _show(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Обновляет сообщение с вопросом, чтобы в чате не копились кнопки."""
    await callback.answer()
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            await message.answer(text, reply_markup=markup)
            return
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)
