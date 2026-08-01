"""Yedek listeleme (GET /api/backups) API container'ının içinden çalışır:
Dockerfile restic ikilisini kurar, docker-compose.prod.yml RESTIC_*
değişkenlerini geçirir ve depoyu SALT OKUNUR bağlar.

Buradaki testler iki şeyi korur:
1. list_snapshots restic'i doğru env ve `--no-lock` ile çağırır (kilit
   denemesi salt okunur depoda "permission denied" ile düşerdi).
2. BackupUnavailable yalnızca gerçekten erişilemediğinde fırlar — restic
   var, parola var ve depo okunuyorsa arayüz "yapılandırılmamış" demez.
"""

import asyncio
import json

import pytest

from app.config import settings
from app.services import backup

SNAPSHOT_JSON = json.dumps(
    [
        {
            "time": "2026-08-01T03:05:00.123456+03:00",
            "short_id": "2cadfd44",
            "tags": ["hesaplik"],
            "summary": {"total_bytes_processed": 8},
        }
    ]
).encode()


class FakeProcess:
    """asyncio.create_subprocess_exec'in döndürdüğü nesnenin testte
    kullanılan yüzeyi: communicate() + returncode."""

    def __init__(self, stdout: bytes, returncode: int):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, b""


@pytest.fixture
def restic(monkeypatch):
    """restic kurulu, RESTIC_* tanımlı, `docker` YOK — yani API
    container'ının içindeki durum. Çağrılar `cagrilar` listesinde birikir,
    dönen çıktı `sonuc` ile ayarlanır."""

    class Sahte:
        def __init__(self):
            self.cagrilar: list[dict] = []
            self.sonuc = FakeProcess(SNAPSHOT_JSON, 0)
            self.kurulu = {"restic"}

        async def exec(self, *args, env=None, **kwargs):
            self.cagrilar.append({"args": args, "env": env})
            return self.sonuc

    sahte = Sahte()
    monkeypatch.setattr(settings, "restic_repository", "/var/www/duman/data/backups")
    monkeypatch.setattr(settings, "restic_password", "gizli")
    monkeypatch.setattr(backup.shutil, "which", lambda ad: ad if ad in sahte.kurulu else None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", sahte.exec)
    return sahte


# ------------------------------------------------------------- listeleme

async def test_snapshotlari_cozer(restic):
    snapshots = await backup.list_snapshots()
    assert [s["short_id"] for s in snapshots] == ["2cadfd44"]


async def test_restic_env_ve_no_lock_ile_cagrilir(restic):
    await backup.list_snapshots()
    cagri = restic.cagrilar[0]

    assert cagri["args"][0] == "restic"
    assert "snapshots" in cagri["args"]
    # Salt okunur depoda kilit denemesi hata verir; bayrak kaybolmamalı.
    assert "--no-lock" in cagri["args"]
    assert cagri["env"]["RESTIC_REPOSITORY"] == "/var/www/duman/data/backups"
    assert cagri["env"]["RESTIC_PASSWORD"] == "gizli"


async def test_depo_henuz_yoksa_bos_liste(restic):
    """restic 10 döner: "depo yok" bir arıza değil, henüz yedek alınmamış."""
    restic.sonuc = FakeProcess(b"", backup.RESTIC_REPO_NOT_FOUND)
    assert await backup.list_snapshots() == []


async def test_bos_ciktida_bos_liste(restic):
    restic.sonuc = FakeProcess(b"", 0)
    assert await backup.list_snapshots() == []


# ------------------------- BackupUnavailable yalnızca gerçek arızalarda

async def test_restic_kurulu_degilse_unavailable(restic):
    restic.kurulu = set()
    with pytest.raises(backup.BackupUnavailable, match="restic"):
        await backup.list_snapshots()


async def test_parola_yoksa_unavailable(restic, monkeypatch):
    monkeypatch.setattr(settings, "restic_password", None)
    with pytest.raises(backup.BackupUnavailable, match="RESTIC_PASSWORD"):
        await backup.list_snapshots()


async def test_yanlis_parola_unavailable(restic):
    restic.sonuc = FakeProcess(b"", backup.RESTIC_WRONG_PASSWORD)
    with pytest.raises(backup.BackupUnavailable, match="RESTIC_PASSWORD yanlış"):
        await backup.list_snapshots()


async def test_depoya_erisilemezse_unavailable(restic):
    restic.sonuc = FakeProcess(b"", 1)
    with pytest.raises(backup.BackupUnavailable, match="erişilemedi"):
        await backup.list_snapshots()


def test_restic_ve_parola_varsa_yapilandirilmis(restic):
    """Container'ın normal hâli: 'yapılandırılmamış' denmemeli."""
    backup.ensure_configured()


# ----------------------------------------------- "şimdi yedekle" (host işi)

def test_docker_yoksa_yedek_alma_anlasilir_hata(restic):
    """Container'da `docker compose` yok ve depo salt okunur; yedeği host'taki
    systemd timer alıyor. Script yarı yolda patlamak yerine baştan uyarır."""
    with pytest.raises(backup.BackupUnavailable, match="otomatik"):
        backup.ensure_can_run_backup()


def test_yedek_alma_engellenince_listeleme_calisir(restic):
    """Kritik: "şimdi yedekle" çalışmıyor diye listeleme de kapanmamalı."""
    with pytest.raises(backup.BackupUnavailable):
        backup.ensure_can_run_backup()
    backup.ensure_configured()


def test_docker_ve_script_varsa_yedek_alinabilir(restic, monkeypatch, tmp_path):
    """Host'ta (docker + script var) engel yok."""
    script = tmp_path / "backup.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(backup, "BACKUP_SCRIPT", script)
    restic.kurulu = {"restic", "docker"}
    backup.ensure_can_run_backup()
