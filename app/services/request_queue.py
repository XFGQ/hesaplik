"""Kalıcı istek kuyruğu (CLAUDE.md > "Çoklu istek — kalıcı istek kuyruğu").

Tek mesajdan çıkan işlemler bellekte (chat_data) değil DB'de tutulur: bot
soru sorup beklese, internet kopsa, bot yeniden başlasa bile istek kaybolmaz
ve kaldığı yerden devam edilebilir.

Bu modül YALNIZCA kuyruk yönetimidir — mesajı işleme, kişi eşleştirme, onay
akışı burada YOK. Bot bağlantısı sonraki adımda gelir.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PendingRequest

BEKLEMEDE = "beklemede"
ISLENIYOR = "isleniyor"
TAMAMLANDI = "tamamlandi"
BASARISIZ = "basarisiz"
IPTAL = "iptal"

DURUMLAR = frozenset({BEKLEMEDE, ISLENIYOR, TAMAMLANDI, BASARISIZ, IPTAL})


class RequestQueueError(Exception):
    """İş kuralı ihlali."""


@dataclass(frozen=True)
class BatchSummary:
    batch_id: str
    toplam: int
    beklemede: int
    isleniyor: int
    tamamlandi: int
    basarisiz: int
    iptal: int


async def create_batch(
    session: AsyncSession, chat_id: str, texts: list[str], raw_message_id: int | None = None
) -> tuple[str, list[PendingRequest]]:
    """Bir mesajdan çıkan işlem metinlerini tek batch olarak kuyruğa yazar.

    Her metin 'beklemede' durumunda, verilen sırayla (sira_no 1'den başlar)
    kaydedilir. `raw_message_id` verilirse (web sohbeti — CLAUDE.md > "Web'e
    chat asistanı ekle") batch'teki TÜM parçalar aynı tek raw_messages
    satırını paylaşır (Telegram gibi: bir gelen mesaj, birden çok işlem).
    Döner: (batch_id, kayıtlar).
    """
    temiz = [t.strip() for t in texts if t and t.strip()]
    if not temiz:
        raise RequestQueueError("Boş istek listesi kuyruğa yazılamaz")

    batch_id = str(uuid.uuid4())
    kayitlar = [
        PendingRequest(
            chat_id=chat_id,
            batch_id=batch_id,
            raw_text=metin,
            sira_no=i,
            durum=BEKLEMEDE,
            raw_message_id=raw_message_id,
        )
        for i, metin in enumerate(temiz, start=1)
    ]
    session.add_all(kayitlar)
    await session.flush()
    return batch_id, kayitlar


async def next_pending(session: AsyncSession, chat_id: str) -> PendingRequest | None:
    """O chat'in en küçük sira_no'lu 'beklemede' isteğini döner, yoksa None.

    Kuyruk sıralı işler (paralel değil) ki onay akışları karışmasın; sıralama
    önce eski batch (created_at/id), sonra batch içi sira_no'ya göredir.
    """
    stmt = (
        select(PendingRequest)
        .where(PendingRequest.chat_id == chat_id, PendingRequest.durum == BEKLEMEDE)
        .order_by(
            PendingRequest.created_at.asc(),
            PendingRequest.sira_no.asc(),
            PendingRequest.id.asc(),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def mark(
    session: AsyncSession,
    request_id: int,
    durum: str,
    sonuc: str | None = None,
    hata: str | None = None,
) -> PendingRequest:
    """İsteğin durumunu günceller (updated_at yenilenir)."""
    if durum not in DURUMLAR:
        raise RequestQueueError(f"Geçersiz durum: {durum}")

    req = await session.get(PendingRequest, request_id)
    if req is None:
        raise RequestQueueError(f"İstek bulunamadı: {request_id}")

    req.durum = durum
    if sonuc is not None:
        req.sonuc = sonuc
    if hata is not None:
        req.hata = hata
    req.updated_at = func.now()
    await session.flush()
    await session.refresh(req)
    return req


async def batch_summary(session: AsyncSession, batch_id: str) -> BatchSummary:
    """Batch'in durum sayımları — bitince kullanıcıya özet vermek için."""
    stmt = (
        select(PendingRequest.durum, func.count())
        .where(PendingRequest.batch_id == batch_id)
        .group_by(PendingRequest.durum)
    )
    sayim = {durum: adet for durum, adet in (await session.execute(stmt)).all()}
    return BatchSummary(
        batch_id=batch_id,
        toplam=sum(sayim.values()),
        beklemede=sayim.get(BEKLEMEDE, 0),
        isleniyor=sayim.get(ISLENIYOR, 0),
        tamamlandi=sayim.get(TAMAMLANDI, 0),
        basarisiz=sayim.get(BASARISIZ, 0),
        iptal=sayim.get(IPTAL, 0),
    )
