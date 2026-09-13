"""Yönetici durum raporu — bot `/durum` komutu ve API açılış bildirimi AYNI
raporu buradan üretir (CLAUDE.md > '"/" komut menüsü' > `/durum`).

Toplama (`durum_topla`) ile biçim (`format_durum`) ayrı: biçim veritabanısız
test edilir. Tek fark raporu kimin ürettiği (`kaynak`):

- "bot": /durum'a cevap veren süreç botun kendisidir → "Telegram botu: ✅
  çalışıyor" ölçülmüş bir gerçektir.
- "api": açılış bildirimini API süreci gönderir; bot ayrı container'dadır ve
  bu süreçten canlılığı GÖRÜLMEZ (bkz. health.check_bot) → uydurulmaz,
  "/durum ile doğrula" denir. Onun yerine API satırı eklenir.

Rapor yalnızca yöneticiye gider; katman adları (NVIDIA/vLLM) burada serbest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawMessage
from app.services import llm_provider, telegram_yedek
from app.services.queries import TotalBalance, total_balance

logger = logging.getLogger(__name__)

Kaynak = Literal["bot", "api"]

_LLM_ADLARI = {"nvidia": "NVIDIA", "vllm": "vLLM", "ollama": "Ollama", "none": "yok"}
_LLM_TERCIH_ADLARI = {**_LLM_ADLARI, "auto": "otomatik", "none": "kapalı"}


def _fmt_try(value: Decimal) -> str:
    """1500.5 -> "1.500,50" (bot/main.py'deki biçimle aynı)."""
    q = value.quantize(Decimal("0.01"))
    neg = q < 0
    formatted = f"{abs(q):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-{formatted}" if neg else formatted


@dataclass(slots=True)
class DurumRaporu:
    """/durum'un anlık tablosu."""

    db_ok: bool
    llm: llm_provider.LLMStatus | None = None
    toplam: TotalBalance | None = None
    son_yedek: datetime | None = None
    mesaj_toplam: int = 0
    kayit_olusturan: int = 0

    @property
    def sorgu_diger(self) -> int:
        return self.mesaj_toplam - self.kayit_olusturan


async def durum_topla(session: AsyncSession) -> DurumRaporu:
    try:
        await session.execute(select(1))
    except Exception:
        logger.warning("/durum: veritabanına erişilemedi", exc_info=True)
        return DurumRaporu(db_ok=False)

    # processed_at YALNIZCA deftere kayıt yazan mesajda dolar
    # (message_processor.record_resolved). Sorgu, liste, "hangisi?" teyidi ve
    # anlaşılmayan mesajda tasarım gereği boş kalır — "işlenmemiş" DEĞİLDİR.
    # count(kolon) yalnızca dolu satırları sayar.
    mesaj_toplam, kayit_olusturan = (
        await session.execute(
            select(func.count(RawMessage.id), func.count(RawMessage.processed_at))
        )
    ).one()
    rapor = DurumRaporu(
        db_ok=True,
        toplam=await total_balance(session),
        son_yedek=await telegram_yedek.son_yedek_oku(session),
        mesaj_toplam=mesaj_toplam,
        kayit_olusturan=kayit_olusturan,
    )
    # Üç katman da yoklanır (get_status, 10 sn önbellekli). Yoklama patlarsa
    # rapor yine gelir, yalnızca LLM bölümü "okunamadı" der.
    try:
        rapor.llm = await llm_provider.get_status(session)
    except Exception:
        logger.warning("/durum: LLM durumu alınamadı", exc_info=True)
    return rapor


def _sistem_satirlari(r: DurumRaporu, kaynak: Kaynak) -> list[str]:
    satirlar = [
        "🩺 Sistem durumu",
        f"Veritabanı: {'✅ sağlıklı' if r.db_ok else '❌ ERİŞİLEMİYOR'}",
    ]
    if kaynak == "api":
        return satirlar + [
            "API: ✅ çalışıyor",
            "Telegram botu: ayrı serviste, buradan görülmez — /durum yazarak doğrula",
        ]
    return satirlar + ["Telegram botu: ✅ çalışıyor"]


def format_durum(r: DurumRaporu, kaynak: Kaynak = "bot") -> str:
    satirlar = _sistem_satirlari(r, kaynak)
    if not r.db_ok or r.toplam is None:
        satirlar += ["", "Veritabanı olmadan defter, LLM tercihi, yedek ve mesaj bilgisi okunamaz."]
        return "\n".join(satirlar)

    t = r.toplam
    satirlar += [
        "",
        "📊 Cari Hesap",
        f"Kişi: {t.kisi_sayisi}",
        f"Toplam alacak: {_fmt_try(t.toplam_alacak)} TL ({t.borclu_sayisi} kişi sana borçlu)",
        f"Toplam borç: {_fmt_try(t.toplam_borc)} TL ({t.alacakli_sayisi} kişi senden alacaklı)",
        f"Hesabı sıfır: {t.kisi_sayisi - t.borclu_sayisi - t.alacakli_sayisi} kişi",
        "",
        "🤖 LLM",
    ]
    if r.llm is None:
        satirlar.append("Durum okunamadı")
    else:
        katmanlar = (("nvidia", r.llm.nvidia), ("vllm", r.llm.vllm), ("ollama", r.llm.ollama))
        satirlar += [
            f"Aktif: {_LLM_ADLARI.get(r.llm.active, r.llm.active)} "
            f"(tercih: {_LLM_TERCIH_ADLARI.get(r.llm.primary, r.llm.primary)})",
            " · ".join(f"{'✅' if s.ok else '❌'} {_LLM_ADLARI[ad]}" for ad, s in katmanlar),
        ]

    son_yedek = (
        f"{r.son_yedek.astimezone(telegram_yedek.ZAMAN_DILIMI):%d.%m.%Y %H:%M}"
        if r.son_yedek is not None
        else "henüz kayıt yok"
    )
    saatler = telegram_yedek.OTOMATIK_SAATLER
    satirlar += [
        "",
        "💾 Yedekleme",
        f"Son yedek: {son_yedek}",
        f"Otomatik: günde {len(saatler)} kez ({', '.join(saatler)})",
        "",
        "📨 Mesajlar",
        f"Toplam ham mesaj: {r.mesaj_toplam}",
        f"Kayıt oluşturan: {r.kayit_olusturan}",
        f"Sorgu/diğer: {r.sorgu_diger}",
    ]
    return "\n".join(satirlar)


async def build_durum_raporu(session: AsyncSession, kaynak: Kaynak = "bot") -> str:
    """Topla + biçimle: /durum ve açılış bildiriminin ortak giriş noktası."""
    return format_durum(await durum_topla(session), kaynak)
