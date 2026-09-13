"""API açılış bildirimi: "🚀 Sistem ayağa kalktı" + /durum raporu → yönetici.

Tetik TEK nokta: FastAPI lifespan (app/main.py). Hem deploy (compose api'yi
yeniden oluşturur) hem sunucu yeniden başlatması (`restart: unless-stopped`
container'ı geri getirir) API sürecini yeniden başlatır, ikisi de buradan
geçer — ayrı bir systemd servisine ya da deploy.yml'e curl eklemeye gerek yok.
"Bu açılış için bildirim attım mı?" kaydı kasten tutulmaz: her başlangıç bir
bildirimdir.

Rapor /durum'la AYNI fonksiyondan gelir (durum.build_durum_raporu,
kaynak="api": bot satırı uydurulmaz).

KRİTİK: bildirim bir kolaylıktır. `gonder()` HİÇBİR istisna fırlatmaz —
Telegram kapalı, token yanlış, veritabanı henüz hazır değil: hepsi yalnızca
loglanır. Lifespan onu arka plan görevi olarak başlatır, API açılışı ne
beklemez ne etkilenir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import settings
from app.db import SessionLocal
from app.services import durum, telegram_yedek

log = logging.getLogger(__name__)

BASLIK = "🚀 Sistem ayağa kalktı"


def mesaj_metni(rapor: str, zaman: datetime) -> str:
    yerel = zaman.astimezone(telegram_yedek.ZAMAN_DILIMI)
    return f"{BASLIK}\n🕒 {yerel:%d.%m.%Y %H:%M:%S}\n\n{rapor}"


async def gonder() -> bool:
    """Bildirimi gönderir; gönderildiyse True. Asla fırlatmaz."""
    if not settings.acilis_bildirimi:
        return False
    chat_id = settings.telegram_admin_chat_id_int
    if not settings.telegram_bot_token or chat_id is None:
        log.info("Açılış bildirimi atlandı: TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID tanımlı değil")
        return False

    zaman = datetime.now(timezone.utc)
    try:
        async with SessionLocal() as session:
            rapor = await durum.build_durum_raporu(session, kaynak="api")
        await telegram_yedek.mesaj_gonder(chat_id, mesaj_metni(rapor, zaman))
    except Exception as e:
        # YedekHatasi mesajı token'ı zaten maskeler; diğerlerinde yalnızca tür
        # adı yazılır (httpx istisnası URL'de token taşıyabilir).
        sebep = str(e) if isinstance(e, telegram_yedek.YedekHatasi) else type(e).__name__
        log.warning("Açılış bildirimi gönderilemedi: %s", sebep)
        return False

    log.info("Açılış bildirimi gönderildi")
    return True
