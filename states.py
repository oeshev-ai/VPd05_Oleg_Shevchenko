"""Состояния диалогов бота (FSM)."""

from aiogram.fsm.state import State, StatesGroup


class TripCreationState(StatesGroup):
    """Создание путешествия.

    В данных FSM по шагам хранятся:
    ``home_currency``, ``target_currency`` — коды валют;
    ``exchange_rate`` — сколько домашней валюты стоит 1 единица валюты назначения;
    ``is_manual_rate`` — курс введён вручную.
    """

    home_currency = State()
    target_currency = State()
    confirm_rate = State()
    manual_rate = State()
    start_amount = State()


class RateUpdateState(StatesGroup):
    """Смена курса уже активной поездки.

    Новый курс лежит в ``exchange_rate``, признак ручного ввода — в ``is_manual_rate``.
    """

    confirm_rate = State()
    manual_rate = State()
