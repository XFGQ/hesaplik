import asyncio
from unittest.mock import AsyncMock

from telegram.constants import ChatAction

from app.bot.main import typing_action


async def test_typing_action_gonderir():
    bot = AsyncMock()

    async with typing_action(bot, chat_id=42):
        await asyncio.sleep(0.05)

    bot.send_chat_action.assert_awaited()
    _, kwargs = bot.send_chat_action.call_args
    assert kwargs["chat_id"] == 42
    assert kwargs["action"] == ChatAction.TYPING


async def test_typing_action_upload_document_aksiyonu():
    bot = AsyncMock()

    async with typing_action(bot, chat_id=1, action=ChatAction.UPLOAD_DOCUMENT):
        await asyncio.sleep(0.01)

    _, kwargs = bot.send_chat_action.call_args
    assert kwargs["action"] == ChatAction.UPLOAD_DOCUMENT


async def test_typing_action_bloktan_cikinca_durur():
    bot = AsyncMock()

    async with typing_action(bot, chat_id=1):
        await asyncio.sleep(0.03)

    await_count_after_exit = bot.send_chat_action.await_count
    await asyncio.sleep(0.1)
    assert bot.send_chat_action.await_count == await_count_after_exit


async def test_typing_action_uzun_islemde_periyodik_yenilenir(monkeypatch):
    from app.bot import main as bot_main

    monkeypatch.setattr(bot_main, "TYPING_REFRESH_SECONDS", 0.01)
    bot = AsyncMock()

    async with typing_action(bot, chat_id=1):
        await asyncio.sleep(0.05)

    assert bot.send_chat_action.await_count >= 2
