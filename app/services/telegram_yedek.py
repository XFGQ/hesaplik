"""Veritabanı yedeğini Telegram'a gönderir — bot `/yedek` komutu ve admin
paneli "Şimdi Yedek Al ve Telegram'a Gönder" düğmesi için.

scripts/telegram-yedek.sh'nin (host'ta systemd timer, 04:00 ve 06:00)
Python ikizi. İkisi AYNI sözleşmeyi uygular — biri değişirse diğeri de:

    pg_dump --clean --if-exists --no-owner (düz SQL) → gzip → pg_dump'ın
    bitiş satırı var mı → 45 MB sınırı → sendDocument → geçici dizin silinir

Neden iki kopya: timer uygulamadan BAĞIMSIZ olmalı (API/bot çökse de yedek
gider); düğme/komut ise container'ın içinden çalışır — orada ne `docker` ne
host betiği var. Burada pg_dump doğrudan DATABASE_URL'e bağlanır (imajda
sunucuyla aynı ana sürümde postgresql-client-16 kurulu, bkz. Dockerfile).
Geliştirmede pg_dump host'ta yoksa db container'ınınki kullanılır.

Hiçbir hata yukarı fırlatılmaz: sonuç her zaman bir YedekSonucu'dur ve
mesajı kullanıcıya olduğu gibi gösterilebilir. Bot token'ı hiçbir mesajda,
logda ya da istisna zincirinde görünmez.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditLog, Setting

log = logging.getLogger(__name__)

# Telegram bot API'si 50 MB üstü dosya kabul etmez; pay bırakmak için 45 MB.
MAX_BAYT = 45 * 1024 * 1024
# Dosya adı ve başlık Türkiye saatiyle (host betiğiyle aynı).
ZAMAN_DILIMI = ZoneInfo("Europe/Istanbul")
# pg_dump düz SQL çıktısının son satırı: yoksa döküm yarıda kesilmiştir.
BITIS_ISARETI = b"PostgreSQL database dump complete"
# --clean --if-exists: aynı dosya hem schema.sql'in kurduğu taze volume'e hem
# dolu bir veritabanına psql ile yüklenir. --no-owner: başka kullanıcıya da.
DUMP_BAYRAKLARI = ("--clean", "--if-exists", "--no-owner")
DUMP_TIMEOUT = 600.0
TELEGRAM_TIMEOUT = 300.0
TELEGRAM_API = "https://api.telegram.org"
PROJE_KOKU = Path(__file__).resolve().parents[2]

AUDIT_ACTION = "telegram_yedek"

# /durum'un "Son yedek" satırı bu ayardan okunur (ISO 8601, UTC). Başarılı her
# gönderimden sonra İKİ yer yazar: bu modül (/yedek, panel düğmesi) ve host
# betiği scripts/telegram-yedek.sh (otomatik 04:00/06:00). Yalnızca biri
# yazsaydı otomatik yedekler /durum'da hiç görünmezdi.
SON_YEDEK_KEY = "son_yedek_zamani"

# Otomatik gönderim saatleri (Europe/Istanbul) — /durum'da statik bilgi.
# deployment/hesaplik-telegram-yedek.timer ile AYNI olmalı (test kilitler).
OTOMATIK_SAATLER = ("04:00", "06:00")

# Aynı süreçte iki yedek üst üste binmesin (çift tıklama, /yedek + düğme).
_kilit = asyncio.Lock()


class YedekHatasi(Exception):
    """Kullanıcıya gösterilebilir Türkçe sebep taşır."""


@dataclass(slots=True)
class YedekSonucu:
    ok: bool
    mesaj: str
    # "gonderildi" | "mesgul" | "yapilandirilmamis" | "sinir" | "hata"
    durum: str
    dosya_adi: str | None = None
    boyut: int | None = None
    zaman: datetime | None = None  # yalnızca başarıda: dökümün alındığı an


def boyut_yazisi(bayt: int) -> str:
    """1 MB altı KB yazılır: küçük veritabanında "0,0 MB" boş yedek sanılmasın."""
    if bayt < 1024 * 1024:
        return f"{round(bayt / 1024)} KB"
    return f"{bayt / (1024 * 1024):.1f} MB".replace(".", ",")


def _gizle(metin: str) -> str:
    token = settings.telegram_bot_token or ""
    return metin.replace(token, "***") if token else metin


def _dump_komutu() -> tuple[list[str], dict[str, str]]:
    url = make_url(settings.database_url)
    kullanici = url.username or "hesaplik"
    veritabani = url.database or "hesaplik"
    env = dict(os.environ)

    pg_dump = shutil.which("pg_dump")
    if pg_dump:
        # Parola komut satırına değil ortama (`ps`'te görünmesin); -w: parola
        # sormak için asla beklemesin, etkileşimsiz bir süreç bu.
        env["PGPASSWORD"] = url.password or ""
        argv = [
            pg_dump, *DUMP_BAYRAKLARI, "-w",
            "-h", url.host or "localhost", "-p", str(url.port or 5432),
            "-U", kullanici, veritabani,
        ]
        return argv, env

    docker = shutil.which("docker")
    if docker:
        # Geliştirme: bot/API host'ta çalışıyor ve pg_dump kurulu değil —
        # db container'ının kendi pg_dump'ı (scripts/telegram-yedek.sh gibi).
        argv = [docker, "compose", "exec", "-T", "db", "pg_dump", *DUMP_BAYRAKLARI,
                "-U", kullanici, veritabani]
        return argv, env

    raise YedekHatasi("pg_dump bulunamadı (sunucu imajında postgresql-client olmalı).")


async def _akit(kaynak: asyncio.StreamReader, hedef: Path) -> bytes:
    """stdout'u gzip dosyasına akıtır, son baytları döner (bitiş kontrolü
    için). Sıkıştırma iş parçacığında: büyük dökümde bot donmasın."""
    son = b""
    with gzip.open(hedef, "wb") as gz:
        while parca := await kaynak.read(1 << 16):
            await asyncio.to_thread(gz.write, parca)
            son = (son + parca)[-4096:]
    return son


async def _dok(hedef: Path) -> None:
    argv, env = _dump_komutu()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=PROJE_KOKU,
        )
    except OSError as e:
        raise YedekHatasi(f"pg_dump başlatılamadı: {e}") from None

    # stderr ayrı okunur: dolarsa pg_dump yazamayıp kilitlenirdi.
    stderr_gorevi = asyncio.create_task(proc.stderr.read())
    try:
        son = await asyncio.wait_for(_akit(proc.stdout, hedef), DUMP_TIMEOUT)
        kod = await asyncio.wait_for(proc.wait(), 30)
    except asyncio.TimeoutError:
        stderr_gorevi.cancel()
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise YedekHatasi("Veritabanı dökümü zaman aşımına uğradı.") from None
    stderr = await stderr_gorevi

    if kod != 0:
        satirlar = stderr.decode(errors="replace").strip().splitlines()
        sebep = satirlar[-1] if satirlar else f"çıkış kodu {kod}"
        raise YedekHatasi(f"Veritabanı dökülemedi: {sebep}")
    if BITIS_ISARETI not in son:
        raise YedekHatasi("Döküm yarım kalmış (pg_dump bitiş satırı yok).")


def _istemci() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT)


async def _telegram(metod: str, *, data: dict, files: dict | None = None) -> None:
    """Telegram'ın kendi hata açıklaması ("chat not found" gibi) mesaja
    girer. İstisnalar `from None`: zincirdeki URL bot token'ını taşır."""
    url = f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/{metod}"
    try:
        async with _istemci() as client:
            yanit = await client.post(url, data=data, files=files)
    except httpx.HTTPError as e:
        raise YedekHatasi(
            f"Telegram'a bağlanılamadı: {_gizle(str(e)) or type(e).__name__}"
        ) from None

    try:
        govde = yanit.json()
    except ValueError:
        govde = {}
    if yanit.status_code != 200 or not govde.get("ok"):
        aciklama = govde.get("description") or f"HTTP {yanit.status_code}"
        raise YedekHatasi(f"Telegram reddetti: {_gizle(str(aciklama))}")


async def yedek_gonder(chat_id: int) -> YedekSonucu:
    """Tüm veritabanını döker, sıkıştırır, doğrular ve `chat_id`ye dosya
    olarak gönderir. 45 MB'ı aşarsa GÖNDERMEZ, sonuç bunu söyler."""
    if not settings.telegram_bot_token:
        return YedekSonucu(
            False, "Telegram botu yapılandırılmamış (TELEGRAM_BOT_TOKEN boş).", "yapilandirilmamis"
        )
    if _kilit.locked():
        return YedekSonucu(False, "Zaten bir yedek alınıyor, biraz sonra tekrar deneyin.", "mesgul")

    async with _kilit:
        simdi = datetime.now(ZAMAN_DILIMI)
        dosya_adi = f"hesaplik_{simdi:%Y-%m-%d_%H%M}.sql.gz"
        # 0700 izinli dizin; başarıda da hatada da silinir — sunucuda kopya kalmaz.
        with tempfile.TemporaryDirectory(prefix="hesaplik-telegram-yedek-") as dizin:
            hedef = Path(dizin) / dosya_adi
            try:
                await _dok(hedef)
                boyut = hedef.stat().st_size
                if boyut > MAX_BAYT:
                    return YedekSonucu(
                        False,
                        f"Yedek 50MB'ı aştı ({boyut_yazisi(boyut)}), alternatif gerekli. "
                        "Bu yedek Telegram'a gönderilmedi.",
                        "sinir", dosya_adi, boyut,
                    )
                baslik = f"Hesaplık yedek {simdi:%d.%m.%Y %H:%M} · {boyut_yazisi(boyut)}"
                with hedef.open("rb") as f:
                    await _telegram(
                        "sendDocument",
                        data={"chat_id": str(chat_id), "caption": baslik},
                        files={"document": (dosya_adi, f, "application/gzip")},
                    )
            except YedekHatasi as e:
                log.warning("Telegram yedeği başarısız: %s", e)
                return YedekSonucu(False, str(e), "hata", dosya_adi)
            except Exception as e:
                log.exception("Telegram yedeğinde beklenmeyen hata")
                return YedekSonucu(
                    False, f"Beklenmeyen hata: {_gizle(str(e)) or type(e).__name__}", "hata", dosya_adi
                )

    log.info("Telegram yedeği gönderildi: %s (%s bayt)", dosya_adi, boyut)
    return YedekSonucu(
        True, f"Gönderildi: {dosya_adi} ({boyut_yazisi(boyut)})", "gonderildi", dosya_adi, boyut,
        simdi,
    )


async def son_yedek_yaz(session: AsyncSession, zaman: datetime) -> None:
    deger = zaman.astimezone(timezone.utc).isoformat(timespec="seconds")
    ayar = await session.get(Setting, SON_YEDEK_KEY)
    if ayar is None:
        session.add(Setting(key=SON_YEDEK_KEY, value=deger))
    else:
        ayar.value = deger
        ayar.updated_at = datetime.now(timezone.utc)
    await session.flush()


async def son_yedek_oku(session: AsyncSession) -> datetime | None:
    """Hiç yedek gönderilmemişse ya da değer bozuksa None (/durum "henüz
    kayıt yok" der, çökmez)."""
    ayar = await session.get(Setting, SON_YEDEK_KEY)
    if ayar is None:
        return None
    try:
        zaman = datetime.fromisoformat(ayar.value.strip())
    except ValueError:
        return None
    return zaman if zaman.tzinfo else zaman.replace(tzinfo=timezone.utc)


async def sonucu_kaydet(session: AsyncSession, actor: str, sonuc: YedekSonucu) -> None:
    """Tüm veritabanı dışarı (Telegram'a) çıktığı için kim/ne zaman izi
    tutulur — başarısız denemeler de. Başarıdaysa son yedek zamanı da
    yazılır (/durum). Commit çağıranın işi."""
    session.add(
        AuditLog(
            actor=actor,
            action=AUDIT_ACTION,
            entity="database",
            entity_id=sonuc.dosya_adi,
            after={"durum": sonuc.durum, "mesaj": sonuc.mesaj, "boyut": sonuc.boyut},
        )
    )
    if sonuc.ok:
        await son_yedek_yaz(session, sonuc.zaman or datetime.now(timezone.utc))
