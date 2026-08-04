"""Yedekten geri dönme ("ana veri yap") — "Yol A": panel İSTER, host UYGULAR.

API container'ı veritabanını geri YÜKLEYEMEZ: içinde `docker`/`compose` yok
ve restic deposu salt okunur bağlı (bkz. backup.ensure_can_run_backup). Geri
yükleme üç adım ister ve üçü de host'ta çalışmak zorundadır:

    1. mevcut veriyi önce yedekle   (scripts/backup.sh <etiket>)
    2. restic ile snapshot'ı çıkar  (restic dump)
    3. pg_restore --clean --if-exists

Bu yüzden iş ikiye ayrıldı:

- **Bu modül (API):** isteği `restore_requests` tablosuna KALICI yazar. Panel
  sekmesi kapansa, API yeniden başlasa, host o an meşgul olsa bile istek
  kaybolmaz ve durumu izlenebilir. Buradan hiçbir shell komutu çalışmaz.
- **Host izleyici (scripts/restore-apply.sh, systemd):** kuyruğu okur, güvenlik
  yedeğini alır, geri yükler, durumu tablodan günceller.

İki koruma:

1. **Tek seferde tek aktif restore.** Uygulama katmanı anlaşılır bir hata verir
   (RestoreInProgress → 409), asıl garanti kısmi tekil indekstedir
   (uq_restore_tek_aktif): iki yönetici aynı anda tıklarsa ikinci INSERT
   veritabanı tarafından reddedilir.
2. **İstek her hâlükârda audit_log'a düşer** — kim, ne zaman, hangi yedeği
   ana veri yapmak istedi.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, RestoreRequest
from app.services import backup

log = logging.getLogger(__name__)

AUDIT_ACTION = "restore_request"
AUDIT_ENTITY = "backups"

STATUS_WAITING = "bekliyor"
STATUS_BACKING_UP = "yedekleniyor"
STATUS_LOADING = "yukleniyor"
STATUS_DONE = "tamamlandi"
STATUS_ERROR = "hata"

# Host izleyici bu durumlardan birindeki işi sahiplenmiş sayılır; biri varken
# yeni istek kabul edilmez.
ACTIVE_STATUSES = (STATUS_WAITING, STATUS_BACKING_UP, STATUS_LOADING)

# Host izleyicinin GERÇEKTEN kurulu/çalışır olduğu container içinden
# görülemez (systemd host'ta). Dolaylı sinyal: bir istek bu süreden uzun
# süredir "bekliyor"da duruyorsa izleyici muhtemelen çalışmıyor. Panel bunu
# uyarı olarak gösterir, kesin bilgi diye değil.
APPLIER_STALE_MINUTES = 5

ACCEPTED_MESSAGE = (
    "Geri yükleme isteği alındı. Önce mevcut veri yedeklenecek, sonra seçili "
    "yedek yüklenecek. Durum aşağıda güncellenecek."
)


class UnknownSnapshot(Exception):
    """İstenen kimlikte yedek yok (silinmiş ya da yanlış yazılmış)."""


class RestoreInProgress(Exception):
    """Zaten süren bir geri yükleme var; ikincisi başlatılamaz."""


def is_available() -> bool:
    """Geri yükleme isteği KABUL EDİLİYOR mu.

    Artık True: istek kuyruğu (restore_requests) şemada var, panel yazıyor,
    host izleyici uyguluyor. Bu "host izleyici şu an çalışıyor" demek
    DEĞİLDİR — o container'dan ölçülemez; `is_stale()` dolaylı sinyali
    verir."""
    return True


def is_stale(request: RestoreRequest | None, now: datetime | None = None) -> bool:
    """İstek uzun süredir "bekliyor"da mı — host izleyici çalışmıyor olabilir.
    Yalnızca WAITING için anlamlıdır: işi eline almış bir izleyicinin
    yedekleme/yükleme adımları uzun sürebilir, o gecikme normaldir."""
    if request is None or request.status != STATUS_WAITING:
        return False
    now = now or datetime.now(timezone.utc)
    requested_at = request.requested_at
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    return now - requested_at > timedelta(minutes=APPLIER_STALE_MINUTES)


async def active_request(session: AsyncSession) -> RestoreRequest | None:
    """Şu an süren geri yükleme (varsa). Kısmi tekil indeks sayesinde en
    fazla bir tane olabilir."""
    stmt = (
        select(RestoreRequest)
        .where(RestoreRequest.status.in_(ACTIVE_STATUSES))
        .order_by(RestoreRequest.requested_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def latest_request(session: AsyncSession) -> RestoreRequest | None:
    """En son istek — aktif yoksa panel "en son ne olmuştu"yu gösterir."""
    stmt = (
        select(RestoreRequest)
        .order_by(RestoreRequest.requested_at.desc(), RestoreRequest.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def request_restore(
    session: AsyncSession,
    *,
    snapshot_id: str,
    actor: str,
) -> RestoreRequest:
    """Şifresi doğrulanmış bir "ana veri yap" isteğini kuyruğa yazar.

    Şifre kontrolü ÇAĞIRANIN işidir (app/api/admin.py) — buraya yalnızca
    doğrulanmış istek gelir. Burada: yedek gerçekten var mı bakılır, başka
    restore sürüyor mu bakılır, istek + audit kaydı yazılır. Veritabanına
    geri YÜKLEME yapılmaz; onu host izleyici yapar."""
    snapshot = await backup.find_snapshot(snapshot_id)
    if snapshot is None:
        log.warning("Geri yükleme isteği reddedildi, yedek yok: %s (%s)", snapshot_id, actor)
        raise UnknownSnapshot(snapshot_id)

    if (running := await active_request(session)) is not None:
        log.warning(
            "Geri yükleme isteği reddedildi, süren iş var: #%s (%s)", running.id, running.status
        )
        raise RestoreInProgress(running.status)

    request = RestoreRequest(
        snapshot_id=snapshot.id,
        requested_by=actor,
        status=STATUS_WAITING,
    )
    session.add(request)
    try:
        await session.flush()
    except IntegrityError as e:
        # Kısmi tekil indeks: aynı anda gelen ikinci istek. Uygulama
        # kontrolünü geçmiş olabilir (yarış), veritabanı geçirmez.
        await session.rollback()
        log.warning("Geri yükleme isteği veritabanınca reddedildi (eşzamanlı istek): %s", e)
        raise RestoreInProgress("eszamanli") from e

    log.warning(
        "GERİ YÜKLEME İSTEĞİ #%s: snapshot=%s (%s) isteyen=%s — host izleyici uygulayacak",
        request.id, snapshot.id, snapshot.time.isoformat(), actor,
    )

    session.add(
        AuditLog(
            actor=actor,
            action=AUDIT_ACTION,
            entity=AUDIT_ENTITY,
            entity_id=snapshot.id,
            before={
                "snapshot_id": snapshot.id,
                "snapshot_time": snapshot.time.isoformat(),
                "size_bytes": snapshot.size_bytes,
                "hostname": snapshot.hostname,
            },
            after={"request_id": request.id, "status": STATUS_WAITING},
        )
    )
    await session.flush()
    return request
