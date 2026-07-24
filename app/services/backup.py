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


class BackupUnavailable(Exception):
    """restic kurulu değil, .env eksik ya da depoya erişilemiyor."""


def _restic_env() -> dict[str, str]:
    env = os.environ.copy()
    env["RESTIC_REPOSITORY"] = settings.restic_repository
    env["RESTIC_PASSWORD"] = settings.restic_password or ""
    return env


def ensure_configured() -> None:
    if shutil.which("restic") is None:
        raise BackupUnavailable("restic kurulu değil")
    if not settings.restic_password:
        raise BackupUnavailable("RESTIC_PASSWORD tanımlı değil (.env eksik)")


async def list_snapshots() -> list[dict]:
    ensure_configured()
    proc = await asyncio.create_subprocess_exec(
        "restic", "snapshots", "--tag", "hesaplik", "--json",
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

    if proc.returncode == RESTIC_REPO_NOT_FOUND:
        return []
    if proc.returncode != 0:
        raise BackupUnavailable("Yedek deposuna erişilemedi")
    return json.loads(stdout or b"[]")


async def run_backup(timeout: float = 300) -> tuple[bool, str, float]:
    ensure_configured()
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
