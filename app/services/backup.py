"""Yedekleme durumu ve tetikleme. Kullanıcıdan gelen hiçbir veri buradan
shell'e geçmez: sabit argüman listeleri, shell=False (create_subprocess_exec)."""

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup.sh"

RESTIC_REPO_NOT_FOUND = 10
RESTIC_WRONG_PASSWORD = 12

# Yedek etiketleri. Normal (zamanlayıcı) yedekler DEFAULT_TAG, geri yükleme
# öncesi alınan güvenlik yedekleri PRE_RESTORE_TAG taşır. Ayrım iki işe yarar:
# (1) panelde "bu bir güvenlik yedeği" diye görünür, (2) `restic forget`
# budama politikası yalnızca DEFAULT_TAG'e uygulanır — güvenlik yedeği 24
# saat sonra kendiliğinden silinmez (bkz. scripts/backup.sh).
DEFAULT_TAG = "hesaplik"
PRE_RESTORE_TAG = "restore-oncesi"
LIST_TAGS = (DEFAULT_TAG, PRE_RESTORE_TAG)

# Yedek zamanlayıcısının aralığı (deployment/hesaplik-backup.timer:
# OnUnitActiveSec=5min). Timer HOST'ta çalışır; container içinden ne durumu
# ne de bir sonraki tetiklenme anı okunabilir — bu sabit yalnızca "sonraki
# yedek yaklaşık ne zaman" TAHMİNİ içindir, ölçüm değildir. Timer dosyası
# değişirse burası da değişmeli.
AUTO_INTERVAL_MINUTES = 5


class BackupUnavailable(Exception):
    """restic kurulu değil, .env eksik ya da depoya erişilemiyor."""


def _restic_env() -> dict[str, str]:
    env = os.environ.copy()
    env["RESTIC_REPOSITORY"] = settings.restic_repository
    env["RESTIC_PASSWORD"] = settings.restic_password or ""
    return env


def ensure_configured() -> None:
    """Yalnızca LİSTELEME için gerekenler: restic ikilisi + parola. API
    container'ında ikisi de var (Dockerfile restic'i kurar,
    docker-compose.prod.yml RESTIC_* değişkenlerini geçirir), o yüzden
    listeleme burada 'yapılandırılmamış' demez."""
    if shutil.which("restic") is None:
        raise BackupUnavailable("restic kurulu değil")
    if not settings.restic_password:
        raise BackupUnavailable("RESTIC_PASSWORD tanımlı değil (.env eksik)")


def ensure_can_run_backup() -> None:
    """Yedek ALMAK listelemekten fazlasını ister: scripts/backup.sh, `docker
    compose` ve depoya yazma izni. API container'ında docker istemcisi yok ve
    depo salt okunur bağlı — yedeği host'taki systemd timer alıyor. Bu durumda
    script'i çalıştırıp yarı yolda patlamak yerine baştan anlaşılır bir hata
    veriyoruz."""
    ensure_configured()
    if not BACKUP_SCRIPT.exists():
        raise BackupUnavailable("Yedek betiği bulunamadı")
    if shutil.which("docker") is None:
        raise BackupUnavailable(
            "Yedekler sunucuda otomatik alınıyor; buradan elle yedek alınamıyor"
        )


async def list_snapshots() -> list[dict]:
    ensure_configured()
    # Her `--tag` bir SEÇENEKTİR (VEYA): normal yedekler + geri yükleme
    # öncesi alınan güvenlik yedekleri birlikte listelenir. Güvenlik yedeği
    # panelde görünmezse kullanıcı "eski hâlim nerede?" diye sorar.
    tag_args = [arg for tag in LIST_TAGS for arg in ("--tag", tag)]
    # --no-lock: depo container'a SALT OKUNUR bağlı; kilitsiz restic
    # locks/ altına yazmaya çalışıp "permission denied" ile düşer.
    # Listeleme zaten okuma işlemi, kilide ihtiyacı yok.
    proc = await asyncio.create_subprocess_exec(
        "restic", "snapshots", *tag_args, "--no-lock", "--json",
        cwd=REPO_ROOT,
        env=_restic_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise BackupUnavailable("restic snapshots zaman aşımına uğradı") from e

    # Depo henüz oluşturulmadıysa bu bir hata değil: "henüz yedek yok".
    if proc.returncode == RESTIC_REPO_NOT_FOUND:
        return []
    if proc.returncode == RESTIC_WRONG_PASSWORD:
        raise BackupUnavailable("RESTIC_PASSWORD yanlış, depo açılamadı")
    if proc.returncode != 0:
        raise BackupUnavailable("Yedek deposuna erişilemedi")
    return json.loads(stdout or b"[]")


# ------------------------------------------------------- ayrıştırılmış liste
#
# `list_snapshots` restic'in ham JSON'unu döner (eski çağıranlar buna
# bağlı). Panel için ham sözlük yerine adı konmuş alanlar gerekiyor:
# aşağısı aynı veriyi ayrıştırıp sıralar, eksik/bozuk alanlarda uydurmaz —
# okunamayan boyut None kalır, ekranda "—" görünür.


@dataclass(frozen=True)
class Snapshot:
    """Bir restic snapshot'ının panelde gösterilen yüzü.

    `id` restic'in kısa kimliği (short_id, 8 karakter) — komut satırında da
    bu kullanılır, panelde gösterilen kimlik ile `restic dump <id>` aynı
    şeyi işaret etsin diye."""

    id: str
    full_id: str
    time: datetime
    size_bytes: int | None      # restic 'summary' vermezse okunamaz, uydurma
    hostname: str | None
    tags: list[str]
    paths: list[str]


def _parse_snapshot(raw: dict) -> Snapshot | None:
    """Tanınmayan/eksik kayıtları sessizce atar: tek bozuk satır yüzünden
    tüm yedek listesi kaybolmasın."""
    if not isinstance(raw, dict):
        return None

    full_id = raw.get("id") or ""
    short_id = raw.get("short_id") or full_id[:8]
    raw_time = raw.get("time")
    if not short_id or not isinstance(raw_time, str):
        return None
    try:
        taken_at = datetime.fromisoformat(raw_time)
    except ValueError:
        log.warning("Yedek listesi: zaman ayrıştırılamadı: %r", raw_time)
        return None
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=timezone.utc)

    summary = raw.get("summary")
    size = summary.get("total_bytes_processed") if isinstance(summary, dict) else None

    return Snapshot(
        id=short_id,
        full_id=full_id or short_id,
        time=taken_at,
        size_bytes=size if isinstance(size, int) else None,
        hostname=raw.get("hostname") or None,
        tags=[t for t in (raw.get("tags") or []) if isinstance(t, str)],
        paths=[p for p in (raw.get("paths") or []) if isinstance(p, str)],
    )


async def snapshots() -> list[Snapshot]:
    """Ayrıştırılmış yedek listesi, EN YENİ ÜSTTE."""
    parsed = [s for s in map(_parse_snapshot, await list_snapshots()) if s is not None]
    return sorted(parsed, key=lambda s: s.time, reverse=True)


async def find_snapshot(snapshot_id: str) -> Snapshot | None:
    """Kimliğe göre tek snapshot. Kısa kimlik (short_id) ya da tam kimlik
    kabul edilir; kullanıcıdan gelen dizge restic'e ARGÜMAN OLARAK GEÇMEZ,
    yalnızca listede aranır."""
    key = (snapshot_id or "").strip().lower()
    if not key:
        return None
    for snap in await snapshots():
        if key in (snap.id.lower(), snap.full_id.lower()):
            return snap
    return None


def next_auto_estimate(last: datetime | None) -> datetime | None:
    """Son yedeğin üstüne timer aralığı: "yaklaşık" bir tahmin. Timer host'ta
    olduğu için kesin zaman bilinemez, arayüz bunu tahmin olarak gösterir."""
    if last is None:
        return None
    return last + timedelta(minutes=AUTO_INTERVAL_MINUTES)


async def run_backup(timeout: float = 300) -> tuple[bool, str, float]:
    ensure_can_run_backup()
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        str(BACKUP_SCRIPT),
        cwd=REPO_ROOT,
        env=_restic_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False, "Yedek alma zaman aşımına uğradı", time.monotonic() - start

    duration = time.monotonic() - start
    ok = proc.returncode == 0
    lines = stdout.decode(errors="replace").strip().splitlines()
    message = (lines[-1] if lines else None) or ("Yedek alındı" if ok else "Yedek alma başarısız")
    return ok, message, duration
