"""Web sohbetinin "bir soruya cevap bekliyorum" durumu (web_chat_pending).

Telegram botu bunu bellekte (context.chat_data) tutar; web HTTP istekleri
arası durumsuz olduğundan aynı durum burada DB'de saklanır (bkz.
app/services/web_chat.py). Bu modül YALNIZCA satırı okur/yazar — kişi
eşleştirme, onay akışı burada YOK.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WebChatPending


async def load(session: AsyncSession, chat_id: str) -> WebChatPending:
    """Satır yoksa boş (kind=None) bir tane oluşturup döner — çağıran taraf
    hep dolu bir nesneyle çalışır, None kontrolü yapmaz."""
    row = await session.get(WebChatPending, chat_id)
    if row is None:
        row = WebChatPending(chat_id=chat_id)
        session.add(row)
        await session.flush()
    return row


async def set_pending(session: AsyncSession, chat_id: str, kind: str, payload: dict) -> WebChatPending:
    """Bekleyen soruyu (kind+payload) yazar/değiştirir; undo alanına dokunmaz."""
    row = await load(session, chat_id)
    row.kind = kind
    row.payload = payload
    await session.flush()
    return row


async def clear_pending(session: AsyncSession, chat_id: str) -> WebChatPending:
    """Bekleyen soruyu temizler (yanıtlandı/iptal edildi); undo alanına dokunmaz."""
    row = await load(session, chat_id)
    row.kind = None
    row.payload = None
    await session.flush()
    return row


async def set_undo(session: AsyncSession, chat_id: str, tx_id: int, expires_at: str) -> WebChatPending:
    row = await load(session, chat_id)
    row.undo = {"tx_id": tx_id, "expires_at": expires_at}
    await session.flush()
    return row


async def clear_undo(session: AsyncSession, chat_id: str) -> WebChatPending:
    row = await load(session, chat_id)
    row.undo = None
    await session.flush()
    return row
