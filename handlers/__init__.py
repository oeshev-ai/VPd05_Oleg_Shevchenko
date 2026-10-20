"""Регистрация роутеров. Числовые сообщения идут последними, чтобы не перехватывать команды."""

from aiogram import Dispatcher

from handlers.admin_or_utils import router as admin_router
from handlers.expense_handler import router as expense_router
from handlers.start_menu import router as start_router
from handlers.trip_creation import router as trip_router


def setup_routers(dispatcher: Dispatcher) -> None:
    """Подключает обработчики в порядке: команды, диалоги, свободный текст."""
    dispatcher.include_router(start_router)
    dispatcher.include_router(admin_router)
    dispatcher.include_router(trip_router)
    dispatcher.include_router(expense_router)
