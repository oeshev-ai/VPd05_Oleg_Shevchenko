"""После завершённого сценария в чате остаётся одно сообщение с главным меню."""

import asyncio

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Message

from keyboards import main_menu_keyboard

# chat_id → id сообщений, которые бот уже видел в этом запуске.
_seen: dict[int, set[int]] = {}
# Последнее сообщение бота: его текст меняется на меню, чат не пустеет.
_last_bot: dict[int, int] = {}
# Фоновые удаления, чтобы их не собрал сборщик мусора до завершения.
_pending_deletes: set[asyncio.Task[None]] = set()
# Лимит Telegram на deleteMessages за один запрос.
_DELETE_BATCH = 100


def remember(chat_id: int, message_id: int) -> None:
    """Запоминает сообщение, чтобы потом удалить его вместе с диалогом."""
    if chat_id > 0 and message_id > 0:
        _seen.setdefault(chat_id, set()).add(message_id)


class RememberIncoming(BaseMiddleware):
    """Запоминает сообщения пользователя до того, как их разберёт хендлер."""

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            remember(event.chat.id, event.message_id)
        return await handler(event, data)


class RememberOutgoing(BaseRequestMiddleware):
    """Запоминает сообщения, которые бот отправил или изменил."""

    async def __call__(
        self,
        make_request,
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ):
        result = await make_request(bot, method)
        if isinstance(result, Message):
            remember(result.chat.id, result.message_id)
            _last_bot[result.chat.id] = result.message_id
        return result


def _ids_to_delete(chat_id: int, anchor_id: int) -> list[int]:
    """Только сообщения, которые бот реально видел в этом запуске.

    Раньше дописывались подряд id вокруг якоря — это трогало чужие реплики
    и замедляло очистку. ``anchor_id`` оставлен для совместимости вызова.
    """
    del anchor_id  # якорь больше не раздувает окно удаления
    ids = {message_id for message_id in _seen.pop(chat_id, set()) if message_id > 0}
    fresh = sorted(ids, reverse=True)
    return fresh[:_DELETE_BATCH]


def _schedule_delete(bot: Bot, chat_id: int, message_ids: list[int]) -> None:
    """Стирает хвост диалога уже после того, как меню видно пользователю."""
    if not message_ids:
        return
    task = asyncio.create_task(_delete_quietly(bot, chat_id, message_ids))
    _pending_deletes.add(task)
    task.add_done_callback(_pending_deletes.discard)


async def _delete_quietly(bot: Bot, chat_id: int, message_ids: list[int]) -> None:
    """Один пакетный запрос. По одному — только если пакет Telegram не принял."""
    try:
        await bot.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except TelegramBadRequest:
        await asyncio.gather(
            *(bot.delete_message(chat_id, message_id) for message_id in message_ids),
            return_exceptions=True,
        )


async def _show_menu(bot: Bot, chat_id: int, text: str) -> int:
    """Меняет последнее сообщение бота на меню. Новое шлёт, только если править нечего."""
    kept_id = _last_bot.get(chat_id)
    if kept_id:
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=kept_id,
                reply_markup=main_menu_keyboard(),
            )
            return kept_id
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                return kept_id
    sent = await bot.send_message(chat_id, text, reply_markup=main_menu_keyboard())
    return sent.message_id


async def leave_only_menu(event: Message | CallbackQuery, text: str) -> None:
    """Сразу показывает меню и следом убирает остальные сообщения диалога."""
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except TelegramBadRequest:
            pass
        message = event.message
        if isinstance(message, Message):
            chat_id = message.chat.id
            anchor_id = message.message_id
        elif event.from_user is not None:
            chat_id = event.from_user.id
            anchor_id = 0
        else:
            return
        bot = event.bot
    else:
        chat_id = event.chat.id
        anchor_id = event.message_id
        bot = event.bot

    kept_id = await _show_menu(bot, chat_id, text)
    _last_bot[chat_id] = kept_id
    stale = [message_id for message_id in _ids_to_delete(chat_id, anchor_id) if message_id != kept_id]
    remember(chat_id, kept_id)
    _schedule_delete(bot, chat_id, stale)
