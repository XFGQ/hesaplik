"""Telegram mesaj alımı.

Mesaj asla kaybolmaz: her Telegram güncellemesi işlenmeden ÖNCE burada
raw_messages'a yazılır. Idempotent: aynı (channel, external_id) ikinci kez
gelirse yeniden yazılmaz, mevcut kayıt döner (external_id = Telegram
update_id).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawMessage

CHANNEL_TELEGRAM = "telegram"


def _extract_chat_id(update: dict) -> str | None:
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        node = update.get(key)
        if node and node.get("chat", {}).get("id") is not None:
            return str(node["chat"]["id"])

    callback = update.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        if chat_id is not None:
            return str(chat_id)

    return None


async def save_raw_message(session: AsyncSession, update: dict) -> RawMessage:
    """Verilen Telegram güncellemesini raw_messages'a yazar. Aynı update_id
    tekrar gelirse mevcut kaydı döner, yeniden yazmaz."""
    external_id = str(update["update_id"])

    stmt = select(RawMessage).where(
        RawMessage.channel == CHANNEL_TELEGRAM, RawMessage.external_id == external_id
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    raw = RawMessage(
        channel=CHANNEL_TELEGRAM,
        external_id=external_id,
        chat_id=_extract_chat_id(update),
        payload=update,
    )
    session.add(raw)
    await session.flush()
    return raw
