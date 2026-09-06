"""Web sohbet mesaj alımı (telegram_intake.py'nin web eşleniği).

Mesaj asla kaybolmaz: her web sohbet isteği işlenmeden ÖNCE burada
raw_messages'a yazılır. Telegram'ın aksine web isteklerinde tekrarlanan
webhook update'i riski yok (her POST kullanıcının o an gönderdiği tek
mesajdır), bu yüzden external_id/idempotency kontrolü gerekmez — chat_id'ye
JWT kullanıcı adı yazılır (message_processor._actor_for ve
web_chat_state bunu kullanır).

Sesli mesaj (mikrofon) ayrı bir kanal adıyla (`web_voice`) yazılır: ses
Groq'a hiç GİTMEDEN önce satır açılır ve commit edilir, böylece çeviri
başarısız olsa bile "kullanıcı burada bir şey söyledi" izi durur (Telegram
botundaki on_voice ile aynı disiplin). Ham ses saklanmaz — tarayıcıdan gelen
kayıt yalnızca bellekte Groq'a iletilir; payload yalnızca boyut/biçim izini
tutar, metin çevrildiğinde `voice_transcript` doldurulur.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawMessage

CHANNEL_WEB = "web"
CHANNEL_WEB_VOICE = "web_voice"

#: Web'den gelen kanalların hepsi (metin + ses). message_processor bir kaydın
#: aktörünü/kaynağını belirlerken buna bakar.
WEB_CHANNELS = frozenset({CHANNEL_WEB, CHANNEL_WEB_VOICE})


async def save_web_message(session: AsyncSession, chat_id: str, text: str) -> RawMessage:
    raw = RawMessage(channel=CHANNEL_WEB, chat_id=chat_id, payload={"text": text})
    session.add(raw)
    await session.flush()
    return raw


async def save_web_voice_message(
    session: AsyncSession, chat_id: str, filename: str, size: int
) -> RawMessage:
    """Ses metne çevrilmeden ÖNCE çağrılır; `voice_transcript` çeviri
    başarılı olursa çağıran tarafından doldurulur (bkz. web_chat.handle_voice)."""
    raw = RawMessage(
        channel=CHANNEL_WEB_VOICE,
        chat_id=chat_id,
        payload={"voice": True, "filename": filename, "size": size},
    )
    session.add(raw)
    await session.flush()
    return raw
