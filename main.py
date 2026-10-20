"""Точка входа: база, роутеры и polling Telegram."""

import asyncio
import logging
import re

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from pydantic import ValidationError

from config import get_settings
from database import init_db
from handlers import setup_routers
from handlers.cleanup import RememberIncoming, RememberOutgoing

logger = logging.getLogger(__name__)

# Не пишем access_key и токен в консоль: httpx по умолчанию логирует полный URL.
_SECRET_IN_LOG = re.compile(
    r"(access_key|api_key|bot_token|token)=([^&\s]+)",
    re.IGNORECASE,
)


class _RedactSecretsFilter(logging.Filter):
    """Прячет значения секретов в любых лог-сообщениях процесса."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        record.msg = _SECRET_IN_LOG.sub(r"\1=***", message)
        record.args = ()
        return True


def _configure_logging() -> None:
    """INFO для бота, без сырых URL httpx с ключом."""
    logging.basicConfig(level=logging.INFO)
    redact = _RedactSecretsFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redact)
    # Сами запросы httpx не нужны в INFO: там всегда полный query string.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _load_settings():
    """Читает `.env`. В консоль попадает только текст ошибки, без значения токена."""
    try:
        return get_settings()
    except ValidationError as exc:
        for error in exc.errors():
            message = str(error.get("msg", "Проверьте файл .env."))
            print(message.removeprefix("Value error, "))
        raise SystemExit(1) from None


async def main() -> None:
    """Создаёт таблицы, подключает обработчики и запускает бота."""
    settings = _load_settings()
    await init_db(settings.db_path)

    bot = Bot(token=settings.bot_token.get_secret_value())
    # Исходящие сообщения запоминаются, чтобы после сценария стереть весь диалог.
    bot.session.middleware(RememberOutgoing())
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.message.outer_middleware(RememberIncoming())
    setup_routers(dispatcher)

    logger.info("Бот запущен, база: %s", settings.db_path)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    _configure_logging()
    asyncio.run(main())
