"""Баланс, история, список поездок и смена курса."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from api_client import ExchangeAPIError, InvalidCurrencyError
from database import (
    Trip,
    get_active_trip,
    get_expenses,
    list_trips,
    switch_trip,
    update_exchange_rate,
)
from handlers.cleanup import leave_only_menu
from handlers.common import (
    NO_ACTIVE_TRIP,
    NOT_A_NUMBER,
    format_amount,
    format_balance_line,
    format_rate,
    manual_rate_prompt,
    parse_positive_number,
    quote_home_per_target,
    rate_question,
)
from keyboards import RateCB, TripCB, main_menu_keyboard, rate_choice_keyboard, trips_keyboard
from states import RateUpdateState

router = Router(name="admin_or_utils")


async def balance_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Текст баланса активной поездки и главное меню."""
    trip = await get_active_trip(user_id)
    if trip is None:
        return NO_ACTIVE_TRIP, main_menu_keyboard()
    text = (
        f"💰 {trip['name']}\n"
        f"{format_balance_line(trip['target_balance'], trip['target_currency'], trip['home_balance'], trip['home_currency'])}\n"
        f"Курс: 1 {trip['target_currency']} = {format_rate(trip['exchange_rate'])} "
        f"{trip['home_currency']}"
    )
    return text, main_menu_keyboard()


async def history_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Последние 10 расходов активной поездки."""
    trip = await get_active_trip(user_id)
    if trip is None:
        return NO_ACTIVE_TRIP, main_menu_keyboard()
    expenses = await get_expenses(trip["id"], limit=10)
    if not expenses:
        return "По этому путешествию пока нет расходов.", main_menu_keyboard()
    lines = [f"📜 Последние расходы — {trip['name']}:"]
    for expense in expenses:
        when = str(expense["timestamp"])[:16]
        line = (
            f"{when} · {format_amount(expense['amount_target'])} {trip['target_currency']}"
            f" = {format_amount(expense['amount_home'])} {trip['home_currency']}"
        )
        if expense["description"]:
            line = f"{line} ({expense['description']})"
        lines.append(line)
    return "\n".join(lines), main_menu_keyboard()


async def trips_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Список поездок для переключения активной."""
    trips = await list_trips(user_id)
    if not trips:
        return NO_ACTIVE_TRIP, main_menu_keyboard()
    return "Выберите активное путешествие:", trips_keyboard(trips)


@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    """Команда /balance."""
    if message.from_user is None:
        return
    text, markup = await balance_view(message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    """Команда /history."""
    if message.from_user is None:
        return
    text, markup = await history_view(message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.message(Command("switch"))
async def cmd_switch(message: Message) -> None:
    """Команда /switch."""
    if message.from_user is None:
        return
    text, markup = await trips_view(message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(TripCB.filter())
async def on_switch_trip(callback: CallbackQuery, callback_data: TripCB) -> None:
    """Делает выбранную поездку единственной активной."""
    if callback.from_user is None:
        return
    try:
        trip = await switch_trip(callback.from_user.id, callback_data.id)
    except ValueError:
        await callback.answer("Путешествие не найдено.", show_alert=True)
        return
    text = (
        f"Активное путешествие: {trip['name']}.\n"
        f"{format_balance_line(trip['target_balance'], trip['target_currency'], trip['home_balance'], trip['home_currency'])}"
    )
    await _show_callback(callback, text, main_menu_keyboard())


@router.message(Command("setrate"))
async def cmd_setrate(message: Message, state: FSMContext) -> None:
    """Команда /setrate меняет курс активной поездки."""
    if message.from_user is None:
        return
    await begin_rate_update(message, state, message.from_user.id)


async def begin_rate_update(message: Message, state: FSMContext, user_id: int) -> None:
    """Запускает FSM смены курса, не трогая черновик поездки, если активной нет."""
    trip = await get_active_trip(user_id)
    if trip is None:
        await leave_only_menu(message, NO_ACTIVE_TRIP)
        return
    await state.set_data(
        {
            "trip_id": trip["id"],
            "home_currency": trip["home_currency"],
            "target_currency": trip["target_currency"],
        }
    )
    await _offer_new_rate(message, state, trip)


async def _offer_new_rate(message: Message, state: FSMContext, trip: Trip) -> None:
    """Показывает свежий курс API или просит ввести число, если сервис молчит."""
    home = trip["home_currency"]
    target = trip["target_currency"]
    try:
        rate = await quote_home_per_target(home, target)
    except InvalidCurrencyError as exc:
        await state.clear()
        await leave_only_menu(message, exc.user_message)
        return
    except ExchangeAPIError as exc:
        await state.update_data(is_manual_rate=True)
        await state.set_state(RateUpdateState.manual_rate)
        await message.answer(exc.user_message)
        await message.answer(manual_rate_prompt(home, target))
        return

    await state.update_data(exchange_rate=rate, is_manual_rate=False)
    await state.set_state(RateUpdateState.confirm_rate)
    await message.answer(
        rate_question(target, home, rate),
        reply_markup=rate_choice_keyboard(),
    )


@router.callback_query(RateUpdateState.confirm_rate, RateCB.filter())
async def on_rate_choice(
    callback: CallbackQuery,
    callback_data: RateCB,
    state: FSMContext,
) -> None:
    """Подтверждение нового курса или переход к ручному числу."""
    await callback.answer()
    message = callback.message
    if not isinstance(message, Message) or callback.from_user is None:
        return
    data = await state.get_data()
    home = str(data.get("home_currency", ""))
    target = str(data.get("target_currency", ""))
    if callback_data.action == "manual":
        await state.update_data(is_manual_rate=True)
        await state.set_state(RateUpdateState.manual_rate)
        await message.answer(manual_rate_prompt(home, target))
        return
    if callback_data.action != "yes":
        return
    await _save_rate(message, state, callback.from_user.id, manual=False)


@router.message(RateUpdateState.manual_rate, F.text, ~F.text.startswith("/"))
async def on_manual_rate(message: Message, state: FSMContext) -> None:
    """Принимает новый курс числом."""
    if message.text is None or message.from_user is None:
        return
    rate = parse_positive_number(message.text)
    if rate is None:
        await message.answer(NOT_A_NUMBER)
        return
    await state.update_data(exchange_rate=rate, is_manual_rate=True)
    await _save_rate(message, state, message.from_user.id, manual=True)


async def _save_rate(message: Message, state: FSMContext, user_id: int, manual: bool) -> None:
    """Записывает курс. Уже накопленные остатки не пересчитывает."""
    data = await state.get_data()
    try:
        trip_id = int(data["trip_id"])
        rate = float(data["exchange_rate"])
        home = str(data["home_currency"])
        target = str(data["target_currency"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await leave_only_menu(
            message,
            "Не удалось обновить курс. Попробуйте ещё раз: /setrate",
        )
        return
    try:
        await update_exchange_rate(trip_id, user_id, rate, manual)
    except ValueError:
        await state.clear()
        await leave_only_menu(message, "Путешествие не найдено.")
        return
    await state.clear()
    await leave_only_menu(
        message,
        f"Курс обновлён: 1 {target} = {format_rate(rate)} {home}.",
    )


@router.message(RateUpdateState.manual_rate)
async def on_rate_not_text(message: Message) -> None:
    """Ручной курс принимается только текстом."""
    await message.answer("Пожалуйста, отправьте курс текстовым сообщением.")


@router.message(RateUpdateState.confirm_rate)
async def on_rate_buttons_expected(message: Message) -> None:
    """Пока курс не подтверждён, свободный текст не тратит деньги."""
    await message.answer("Нажмите «Да» или «Ввести вручную».")


async def _show_callback(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup,
) -> None:
    """Меняет сообщение с кнопкой или отправляет новое, если править его нельзя."""
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
