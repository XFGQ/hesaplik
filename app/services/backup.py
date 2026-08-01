"""Yedekleme durumu ve tetikleme. Kullanıcıdan gelen hiçbir veri buradan
shell'e geçmez: sabit argüman listeleri, shell=False (create_subprocess_exec)."""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup.sh"

RESTIC_REPO_NOT_FOUND = 10
RESTIC_WRONG_PASSWORD = 12


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
    # --no-lock: depo container'a SALT OKUNUR bağlı; kilitsiz restic
    # locks/ altına yazmaya çalışıp "permission denied" ile düşer.
    # Listeleme zaten okuma işlemi, kilide ihtiyacı yok.
    proc = await asyncio.create_subprocess_exec(
        "restic", "snapshots", "--tag", "hesaplik", "--no-lock", "--json",
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
