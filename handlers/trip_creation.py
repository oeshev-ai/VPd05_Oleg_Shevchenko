"""Диалог создания путешествия: валюты, курс и стартовая сумма."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from api_client import ExchangeAPIError, InvalidCurrencyError, get_exchange_rate
from database import create_trip
from handlers.cleanup import leave_only_menu
from handlers.common import (
    NOT_A_NUMBER,
    currency_error_text,
    format_amount,
    manual_rate_prompt,
    opening_by_rate,
    parse_positive_number,
    quote_home_per_target,
    rate_question,
    read_api_key,
    resolve_currency,
)
from keyboards import RateCB, rate_choice_keyboard
from states import TripCreationState

router = Router(name="trip_creation")


async def begin_trip(message: Message, state: FSMContext) -> None:
    """Сбрасывает прошлый диалог и спрашивает страну или валюту отправления."""
    await state.clear()
    await state.set_state(TripCreationState.home_currency)
    await message.answer(
        "Введите страну отправления (или код валюты, например, RUB)."
    )


@router.message(Command("newtrip"))
async def cmd_newtrip(message: Message, state: FSMContext) -> None:
    """Команда /newtrip запускает тот же диалог, что и кнопка меню."""
    if message.from_user is None:
        return
    await begin_trip(message, state)


@router.message(TripCreationState.home_currency, F.text, ~F.text.startswith("/"))
async def on_home_currency(message: Message, state: FSMContext) -> None:
    """Шаг 1: запоминает домашнюю валюту и спрашивает направление."""
    if message.text is None:
        return
    code = resolve_currency(message.text)
    if code is None:
        await message.answer(currency_error_text())
        return
    await state.update_data(home_currency=code)
    await state.set_state(TripCreationState.target_currency)
    await message.answer(
        "Введите страну назначения (или код валюты, например, CNY)."
    )


@router.message(TripCreationState.target_currency, F.text, ~F.text.startswith("/"))
async def on_target_currency(message: Message, state: FSMContext) -> None:
    """Шаг 2–4: вторая валюта, запрос курса и вопрос, подходит ли он."""
    if message.text is None or message.from_user is None:
        return
    code = resolve_currency(message.text)
    if code is None:
        await message.answer(currency_error_text())
        return

    data = await state.get_data()
    home = str(data["home_currency"])
    raw_name = message.text.strip()
    trip_name = raw_name if raw_name.upper() != code else code
    await state.update_data(target_currency=code, trip_name=trip_name[:80])
    await _offer_rate(message, state, home, code)


async def _offer_rate(message: Message, state: FSMContext, home: str, target: str) -> None:
    """Берёт курс из API. Если сервис недоступен, сразу просит число вручную."""
    try:
        rate = await quote_home_per_target(home, target)
    except InvalidCurrencyError as exc:
        await state.set_state(TripCreationState.target_currency)
        await message.answer(exc.user_message)
        return
    except ExchangeAPIError as exc:
        await state.update_data(is_manual_rate=True)
        await state.set_state(TripCreationState.manual_rate)
        await message.answer(exc.user_message)
        await message.answer(manual_rate_prompt(home, target))
        return

    await state.update_data(exchange_rate=rate, is_manual_rate=False)
    await state.set_state(TripCreationState.confirm_rate)
    await message.answer(
        rate_question(target, home, rate),
        reply_markup=rate_choice_keyboard(),
    )


@router.callback_query(TripCreationState.confirm_rate, RateCB.filter())
async def on_rate_choice(
    callback: CallbackQuery,
    callback_data: RateCB,
    state: FSMContext,
) -> None:
    """Шаг 4: согласие с курсом или переход к ручному вводу."""
    await callback.answer()
    message = callback.message
    if not isinstance(message, Message):
        return
    data = await state.get_data()
    home = str(data.get("home_currency", ""))
    target = str(data.get("target_currency", ""))
    if callback_data.action == "manual":
        await state.update_data(is_manual_rate=True)
        await state.set_state(TripCreationState.manual_rate)
        await message.answer(manual_rate_prompt(home, target))
        return
    if callback_data.action != "yes":
        return
    await state.set_state(TripCreationState.start_amount)
    await message.answer(
        "Введите начальную сумму в домашней валюте (например, 10000)."
    )


@router.message(TripCreationState.manual_rate, F.text, ~F.text.startswith("/"))
async def on_manual_rate(message: Message, state: FSMContext) -> None:
    """Шаг 5: пользователь заменяет курс сервиса своим числом."""
    if message.text is None:
        return
    rate = parse_positive_number(message.text)
    if rate is None:
        await message.answer(NOT_A_NUMBER)
        return
    await state.update_data(exchange_rate=rate, is_manual_rate=True)
    await state.set_state(TripCreationState.start_amount)
    await message.answer(
        "Введите начальную сумму в домашней валюте (например, 10000)."
    )


@router.message(TripCreationState.start_amount, F.text, ~F.text.startswith("/"))
async def on_start_amount(message: Message, state: FSMContext) -> None:
    """Шаг 6–7: конвертирует стартовую сумму и сохраняет активную поездку."""
    if message.text is None or message.from_user is None:
        return
    amount = parse_positive_number(message.text)
    if amount is None:
        await message.answer(NOT_A_NUMBER)
        return

    data = await state.get_data()
    try:
        home = str(data["home_currency"])
        target = str(data["target_currency"])
        rate = float(data["exchange_rate"])
        manual = bool(data["is_manual_rate"])
        trip_name = str(data["trip_name"])
    except (KeyError, TypeError, ValueError):
        await state.clear()
        await leave_only_menu(
            message,
            "Не удалось продолжить создание. Начните снова командой /newtrip.",
        )
        return

    home_balance, target_balance, stored_rate = await _opening_balances(
        home, target, amount, rate, manual
    )
    if target_balance <= 0 or stored_rate <= 0:
        await message.answer(
            "Сумма слишком мала для этого курса. Введите другую начальную сумму."
        )
        return

    try:
        await create_trip(
            user_id=message.from_user.id,
            name=trip_name,
            home_currency=home,
            target_currency=target,
            exchange_rate=stored_rate,
            is_manual_rate=manual,
            home_balance=home_balance,
            target_balance=target_balance,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await state.clear()
    await leave_only_menu(
        message,
        "Путешествие создано! Стартовый баланс: "
        f"{format_amount(home_balance)} {home} = {format_amount(target_balance)} {target}",
    )


async def _opening_balances(
    home: str,
    target: str,
    home_amount: float,
    rate: float,
    manual: bool,
) -> tuple[float, float, float]:
    """Считает стартовые остатки.

    Ручной курс применяется как есть. Курс из API ещё раз проверяется
    запросом на всю введённую сумму; если сервис не ответил, остаётся
    уже подтверждённый курс.
    """
    home_balance, target_balance = opening_by_rate(home_amount, rate)
    if manual or home == target:
        return home_balance, target_balance, rate
    try:
        conversion = await get_exchange_rate(home, target, home_balance, read_api_key())
    except ExchangeAPIError:
        return home_balance, target_balance, rate
    api_target = round(conversion.result, 2)
    if api_target <= 0:
        return home_balance, target_balance, rate
    if api_target == target_balance:
        return home_balance, target_balance, rate
    return home_balance, api_target, home_balance / api_target


@router.message(TripCreationState.home_currency)
@router.message(TripCreationState.target_currency)
@router.message(TripCreationState.manual_rate)
@router.message(TripCreationState.start_amount)
async def on_trip_not_text(message: Message) -> None:
    """В диалоге создания ждём текст, а не фото или стикер."""
    await message.answer("Пожалуйста, отправьте ответ текстовым сообщением.")


@router.message(TripCreationState.confirm_rate)
async def on_rate_text_instead_of_button(message: Message) -> None:
    """Пока висит вопрос о курсе, число не считается расходом."""
    await message.answer("Нажмите «Да» или «Ввести вручную».")
