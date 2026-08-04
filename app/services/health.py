"""Sistem sağlığı: bir şey bozulduğunda "ne ayakta, ne değil" tek bakışta.

Admin panelindeki "Sistem Sağlığı" bölümünün veri kaynağı. Her bileşen bir
`Check` döner: durum + tek satırlık özet + ayrıntı satırları.

Üç kural:

1. **Uydurma yok.** Bu süreçten ölçülemeyen hiçbir şey "çalışıyor" diye
   gösterilmez. API container'ının içinden host diski, systemd timer'ı ya da
   bot sürecinin canlılığı görünmez; bunlar ya DOLAYLI bir izden okunur
   (`measured=False` + açıklama) ya da hiç gösterilmez.
2. **Sağlık kontrolü sistemi yavaşlatmaz/çökertmez.** Ollama'ya kısa
   timeout'la (3 sn) gidilir, her kontrol kendi hatasını yakalar; biri
   patlarsa diğerleri yine görünür.
3. **"Kapalı" hata değildir.** LLM_PROVIDER=none bir arıza değil bir
   tercihtir (kural parser her zaman açıktır, CLAUDE.md > "LLM son çare");
   bot mesaj almamış olması da öyle. Bunlar STATUS_INFO ile gri gösterilir,
   genel özeti kırmızıya çevirmez.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import settings
from app.models import Person, RawMessage, Transaction
from app.services import backup

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- durumlar

STATUS_OK = "ok"
STATUS_WARN = "uyari"
STATUS_ERROR = "hata"
STATUS_INFO = "bilgi"    # ne iyi ne kötü: kapalı, sessiz, ölçülemiyor

# Genel özet en kötü duruma göre belirlenir. "bilgi" bilinçli bir durumdur,
# ağırlığı yoktur — kapalı LLM yüzünden panel kırmızı yanmaz.
_SEVERITY = {STATUS_OK: 0, STATUS_INFO: 0, STATUS_WARN: 1, STATUS_ERROR: 2}

# Sağlık kontrolü panelin açılışını bekletmemeli: Ollama yavaşsa/kapalıysa
# 3 saniyede pes edilir (asıl parse timeout'u 90 sn, o ayrı iş).
LLM_HEALTH_TIMEOUT = 3.0

# Bu süre içinde Telegram mesajı geldiyse bot kesin ayakta. Gelmediyse bot
# ölmüş de olabilir, kimse yazmamış da — "sessiz" deriz, hata demeyiz.
BOT_ACTIVE_MINUTES = 15

# Yedek timer'ı 5 dakikada bir çalışıyor (deployment/hesaplik-backup.timer).
# Bir saati aşan boşluk timer'ın durduğuna işarettir.
BACKUP_STALE_MINUTES = 60

# Süreç başlangıcı ≈ bu modülün ilk import edildiği an (uygulama açılışında
# app.api.admin üzerinden import edilir). Konteyner içinde /proc'a bakmadan
# elde edilebilecek en dürüst "ne zamandır ayakta" ölçüsü.
STARTED_AT = time.monotonic()


@dataclass(frozen=True)
class Check:
    """Tek bir bileşenin sağlık raporu.

    `measured=False`: bu değer doğrudan ölçülmedi, dolaylı bir izden
    çıkarıldı (ör. bot ayrı container'da; canlılığı yalnızca son gelen
    mesajdan tahmin edilir). Panel bunu açıkça yazar ki kimse dolaylı bir
    ipucunu kesin bilgi sanmasın."""

    id: str
    label: str
    status: str
    summary: str
    details: list[tuple[str, str]] = field(default_factory=list)
    measured: bool = True
    note: str | None = None


@dataclass(frozen=True)
class HealthReport:
    checked_at: datetime
    overall: str
    overall_text: str
    components: list[Check]


# ---------------------------------------------------------------- yardımcılar

_CRED_RE = re.compile(r"//[^/\s:@]+:[^/\s@]+@")


def _short_error(exc: Exception) -> str:
    """Hata mesajının ilk satırı, kısaltılmış ve parolası maskelenmiş.
    Uç admin şifresiyle korunuyor ama bağlantı dizgesindeki parolayı
    ekrana basmanın hiçbir faydası yok."""
    line = str(exc).strip().splitlines()
    text = line[0] if line else exc.__class__.__name__
    text = _CRED_RE.sub("//***@", text)
    return text[:200] if len(text) <= 200 else text[:197] + "…"


def _aware(dt: datetime) -> datetime:
    """Zaman dilimsiz gelen bir damgayı UTC say (SQLite/eski kayıt gibi
    durumlarda karşılaştırma TypeError vermesin)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def elapsed_text(dt: datetime, now: datetime | None = None) -> str:
    """"5 dk önce" gibi okunur bir geçmiş zaman. Saat kayması yüzünden
    gelecekte kalan damgalar "az önce" sayılır (negatif süre gösterme)."""
    now = now or datetime.now(timezone.utc)
    seconds = (now - _aware(dt)).total_seconds()
    if seconds < 60:
        return "az önce"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} dk önce"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} saat önce"
    return f"{hours // 24} gün önce"


def duration_text(seconds: float) -> str:
    """Çalışma süresi: "3 sa 12 dk"."""
    total_minutes = int(seconds // 60)
    if total_minutes < 1:
        return "1 dk'dan az"
    days, rest = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days} gün {hours} sa"
    if hours:
        return f"{hours} sa {minutes} dk"
    return f"{minutes} dk"


async def _rollback_quietly(session: AsyncSession) -> None:
    """Sorgu patladıysa session'ı temizle: get_session çıkışta commit
    deniyor, kirli session orada ikinci bir hata fırlatırdı."""
    try:
        await session.rollback()
    except SQLAlchemyError:  # bağlantı tamamen gitmişse bu da patlar, önemsiz
        log.debug("Sağlık kontrolü: rollback başarısız", exc_info=True)


# ---------------------------------------------------------------- kontroller

async def check_database(session: AsyncSession) -> Check:
    """Bağlantı canlı mı + defterin kaba büyüklüğü. Erişilemiyorsa HATA:
    veritabanı olmadan sistemin hiçbir parçası çalışmaz."""
    try:
        await session.execute(select(1))
        persons = (
            await session.execute(
                select(func.count()).select_from(Person).where(Person.is_active.is_(True))
            )
        ).scalar_one()
        transactions = (
            await session.execute(select(func.count()).select_from(Transaction))
        ).scalar_one()
        last_tx = (await session.execute(select(func.max(Transaction.occurred_at)))).scalar_one()
    except SQLAlchemyError as e:
        await _rollback_quietly(session)
        log.warning("Sağlık: veritabanına erişilemedi: %s", e)
        return Check(
            id="db",
            label="Veritabanı",
            status=STATUS_ERROR,
            summary="Erişilemiyor",
            details=[("Hata", _short_error(e))],
        )

    return Check(
        id="db",
        label="Veritabanı",
        status=STATUS_OK,
        summary=f"{persons} kişi · {transactions} işlem",
        details=[
            ("Kişi", str(persons)),
            ("İşlem", str(transactions)),
            ("Son işlem", elapsed_text(last_tx) if last_tx else "kayıt yok"),
        ],
    )


def _model_loaded(target: str, models: list[str]) -> bool:
    """Aktif model Ollama'da yüklü mü. "qwen2.5" ile "qwen2.5:latest" aynı
    modeldir; etiketsiz yazılan ad etiketli sürümle eşleşir."""
    target = (target or "").strip()
    if not target:
        return False
    if target in models:
        return True
    if ":" in target:
        return False
    return any(m.split(":")[0] == target for m in models)


async def check_llm(client: httpx.AsyncClient | None = None) -> Check:
    """LLM_PROVIDER=none → "kapalı" (hata değil, tercih). ollama → gerçekten
    /api/tags'e gidilir; erişilemiyorsa HATA (açık olması beklenen bir servis
    cevap vermiyor), model listede yoksa UYARI.

    `client` yalnızca testler içindir (httpx.MockTransport)."""
    provider = (settings.llm_provider or "none").strip().lower()
    label = "LLM (Ollama)"

    if provider != "ollama":
        return Check(
            id="llm",
            label=label,
            status=STATUS_INFO,
            summary="Kapalı",
            details=[("Sağlayıcı", provider or "none")],
            note="LLM kapalı; mesajlar yalnızca kural parser ile çözülüyor.",
        )

    url = f"{settings.ollama_url.rstrip('/')}/api/tags"
    try:
        if client is not None:
            resp = await client.get(url)
        else:
            async with httpx.AsyncClient(timeout=LLM_HEALTH_TIMEOUT) as c:
                resp = await c.get(url)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Sağlık: Ollama'ya erişilemedi: %s", e)
        return Check(
            id="llm",
            label=label,
            status=STATUS_ERROR,
            summary="Erişilemiyor",
            details=[
                ("Adres", settings.ollama_url),
                ("Aktif model", settings.llm_model),
                ("Hata", _short_error(e)),
            ],
            note="LLM erişilemiyor; kayıt ve sorgular kural parser ile sürüyor.",
        )

    raw_models = data.get("models") if isinstance(data, dict) else None
    models = [
        m["name"]
        for m in (raw_models or [])
        if isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"]
    ]
    active = settings.llm_model
    loaded = _model_loaded(active, models)

    details = [
        ("Adres", settings.ollama_url),
        ("Aktif model", active),
        ("Yüklü modeller", ", ".join(models) if models else "yok"),
    ]
    if loaded:
        return Check(id="llm", label=label, status=STATUS_OK,
                     summary=f"{active} yüklü", details=details)
    return Check(
        id="llm",
        label=label,
        status=STATUS_WARN,
        summary=f"{active} yüklü değil",
        details=details,
        note="Ollama çalışıyor ama ayarlı model listede yok; LLM çağrısı başarısız olur.",
    )


async def check_bot(session: AsyncSession) -> Check:
    """DOLAYLI. Bot ayrı bir container'da çalışır; API süreci onun canlı olup
    olmadığını göremez. Elimizdeki tek gerçek iz `raw_messages`: son Telegram
    mesajı yeniyse bot o an kesin ayaktaydı. Mesaj yoksa "sessiz" — kimse
    yazmamış da olabilir, bu bir hata değildir."""
    label = "Telegram botu"
    note = (
        "Bot ayrı bir container'da çalışır; süreç durumu buradan görülemez. "
        "Durum, deftere düşen son Telegram mesajından çıkarılır."
    )

    if not settings.telegram_bot_token:
        return Check(
            id="bot",
            label=label,
            status=STATUS_INFO,
            summary="Yapılandırılmamış",
            details=[("Jeton", "TELEGRAM_BOT_TOKEN tanımlı değil")],
            measured=False,
            note="Jeton yok; bot hiç başlatılmıyor. API ve web etkilenmez.",
        )

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        last_seen = (
            await session.execute(
                select(func.max(RawMessage.received_at)).where(RawMessage.channel == "telegram")
            )
        ).scalar_one()
        last_day = (
            await session.execute(
                select(func.count())
                .select_from(RawMessage)
                .where(RawMessage.channel == "telegram", RawMessage.received_at >= since)
            )
        ).scalar_one()
    except SQLAlchemyError as e:
        await _rollback_quietly(session)
        return Check(
            id="bot",
            label=label,
            status=STATUS_INFO,
            summary="Ölçülemedi",
            details=[("Sebep", _short_error(e))],
            measured=False,
            note="Veritabanına erişilemediği için bot durumu okunamadı.",
        )

    details = [
        ("Son mesaj", elapsed_text(last_seen) if last_seen else "hiç"),
        ("Son 24 saat", f"{last_day} mesaj"),
    ]

    if last_seen is None:
        return Check(id="bot", label=label, status=STATUS_INFO, summary="Mesaj gelmemiş",
                     details=details, measured=False, note=note)

    fresh = datetime.now(timezone.utc) - _aware(last_seen) <= timedelta(minutes=BOT_ACTIVE_MINUTES)
    if fresh:
        return Check(id="bot", label=label, status=STATUS_OK,
                     summary=f"Aktif · son mesaj {elapsed_text(last_seen)}",
                     details=details, measured=False, note=note)
    return Check(id="bot", label=label, status=STATUS_INFO,
                 summary=f"Sessiz · son mesaj {elapsed_text(last_seen)}",
                 details=details, measured=False, note=note)


async def check_backup() -> Check:
    """Son yedeğin yaşı. Yedek ALMAK bu container'ın işi değil (host'taki
    systemd timer alıyor), burada yalnızca depo LİSTELENİR. Erişilemezse
    UYARI: defter çalışmaya devam eder ama koruma belirsizdir."""
    label = "Yedekleme"
    try:
        # Ayrıştırma/sıralama backup.snapshots'ta (tek yer): panelin
        # "Yedekleme" bölümüyle bu özet aynı listeyi aynı biçimde okur.
        snapshots = await backup.snapshots()
    except (backup.BackupUnavailable, OSError) as e:
        return Check(
            id="backup",
            label=label,
            status=STATUS_WARN,
            summary="Durum okunamadı",
            details=[("Depo", settings.restic_repository), ("Sebep", _short_error(e))],
        )

    if not snapshots:
        return Check(
            id="backup",
            label=label,
            status=STATUS_WARN,
            summary="Henüz yedek alınmamış",
            details=[("Depo", settings.restic_repository), ("Yedek sayısı", "0")],
        )

    last = snapshots[0].time     # en yeni üstte
    details = [
        ("Son yedek", elapsed_text(last)),
        ("Yedek sayısı", str(len(snapshots))),
        ("Depo", settings.restic_repository),
    ]
    stale = datetime.now(timezone.utc) - last > timedelta(minutes=BACKUP_STALE_MINUTES)
    if stale:
        return Check(
            id="backup",
            label=label,
            status=STATUS_WARN,
            summary=f"Son yedek {elapsed_text(last)} (gecikmiş)",
            details=details,
            note=f"Yedek {BACKUP_STALE_MINUTES} dakikadan eski; zamanlayıcı durmuş olabilir.",
        )
    return Check(id="backup", label=label, status=STATUS_OK,
                 summary=f"Son yedek {elapsed_text(last)}", details=details)


def check_api() -> Check:
    """Bu yanıtın var olması API'nin ayakta olduğunun kendisidir. Ölçülebilen
    tek ek bilgi süreç ömrü ve sürüm — CPU/RAM/disk container içinden
    güvenilir okunamaz, uydurmak yerine hiç göstermiyoruz."""
    uptime = time.monotonic() - STARTED_AT
    return Check(
        id="api",
        label="API",
        status=STATUS_OK,
        summary=f"Çalışıyor · {duration_text(uptime)}",
        details=[("Sürüm", __version__), ("Çalışma süresi", duration_text(uptime))],
        note="Bu bölümü görebiliyorsanız API zaten ayakta.",
    )


# ---------------------------------------------------------------- toplama

def overall_status(checks: list[Check]) -> str:
    """En kötü duruma göre. "bilgi" ağırlıksızdır: yalnız başına kaldığında
    genel durum yine "ok"tur (kapalı LLM sistemi hasta göstermez)."""
    worst = max((_SEVERITY.get(c.status, 0) for c in checks), default=0)
    return {2: STATUS_ERROR, 1: STATUS_WARN}.get(worst, STATUS_OK)


def overall_text(checks: list[Check]) -> str:
    errors = sum(1 for c in checks if c.status == STATUS_ERROR)
    warnings = sum(1 for c in checks if c.status == STATUS_WARN)
    parts = []
    if errors:
        parts.append(f"{errors} hata")
    if warnings:
        parts.append(f"{warnings} uyarı")
    return f"{' · '.join(parts)} var" if parts else "Tüm sistemler çalışıyor"


async def collect(session: AsyncSession, llm_client: httpx.AsyncClient | None = None) -> HealthReport:
    """Tüm bileşenleri kontrol eder. Veritabanı kontrolleri aynı session'ı
    paylaştığı için sıralı, dışarı çıkanlar (Ollama, restic) paralel —
    yavaş olan ikisi birbirini beklemesin."""
    db = await check_database(session)
    bot = await check_bot(session)
    llm, backup_check = await asyncio.gather(check_llm(llm_client), check_backup())

    checks = [db, bot, llm, backup_check, check_api()]
    return HealthReport(
        checked_at=datetime.now(timezone.utc),
        overall=overall_status(checks),
        overall_text=overall_text(checks),
        components=checks,
    )
