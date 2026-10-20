"""Приветствие и кнопки главного меню."""

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.admin_or_utils import balance_view, begin_rate_update, history_view, trips_view
from handlers.trip_creation import begin_trip
from keyboards import MenuCB, main_menu_keyboard

router = Router(name="start_menu")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Сбрасывает незавершённый диалог и показывает меню."""
    await state.clear()
    await message.answer(
        "Привет! Я кошелёк путешественника: веду расходы поездки "
        "сразу в двух валютах.\n"
        "Отправьте сумму в валюте назначения, и я спрошу, записать ли расход.",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(MenuCB.filter())
async def on_menu(
    callback: CallbackQuery,
    callback_data: MenuCB,
    state: FSMContext,
) -> None:
    """Пять кнопок меню. Создание поездки и смена курса открывают свои диалоги."""
    if callback.from_user is None:
        return
    user_id = callback.from_user.id
    action = callback_data.action
    message = callback.message

    if action == "new":
        await callback.answer()
        if isinstance(message, Message):
            await begin_trip(message, state)
        return

    if action == "rate":
        await callback.answer()
        if isinstance(message, Message):
            await begin_rate_update(message, state, user_id)
        return

    if action == "trips":
        text, markup = await trips_view(user_id)
    elif action == "bal":
        text, markup = await balance_view(user_id)
    elif action == "hist":
        text, markup = await history_view(user_id)
    else:
        await callback.answer()
        return

    await callback.answer()
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            await message.answer(text, reply_markup=markup)
            return
    await callback.bot.send_message(user_id, text, reply_markup=markup)
