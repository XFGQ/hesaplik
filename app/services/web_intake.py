"""Web sohbet mesaj alımı (telegram_intake.py'nin web eşleniği).

Mesaj asla kaybolmaz: her web sohbet isteği işlenmeden ÖNCE burada
raw_messages'a yazılır. Telegram'ın aksine web isteklerinde tekrarlanan
webhook update'i riski yok (her POST kullanıcının o an gönderdiği tek
mesajdır), bu yüzden external_id/idempotency kontrolü gerekmez — chat_id'ye
JWT kullanıcı adı yazılır (message_processor._actor_for ve
web_chat_state bunu kullanır).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawMessage

CHANNEL_WEB = "web"


async def save_web_message(session: AsyncSession, chat_id: str, text: str) -> RawMessage:
    raw = RawMessage(channel=CHANNEL_WEB, chat_id=chat_id, payload={"text": text})
    session.add(raw)
    await session.flush()
    return raw
