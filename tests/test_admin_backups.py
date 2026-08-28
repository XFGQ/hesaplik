"""Admin paneli yedekleme bölümü: liste (/api/admin/backups) ve "ana veri
yap" akışı (/api/admin/backups/restore + /status).

Mimari ("Yol A"): panel İSTER, host UYGULAR. API veritabanını geri yüklemez —
`restore_requests` tablosuna kalıcı bir istek yazar, host'taki izleyici
(scripts/restore-apply.sh) uygular. Buradaki testler API tarafını kilitler:

- şifresiz hiçbir şey dışarı çıkmaz, yanlış şifre reddedilir,
- istek gerçekten kuyruğa yazılır (kaybolmaz),
- aynı anda İKİ geri yükleme başlatılamaz (409 + veritabanı indeksi),
- API'nin kendisi asla yedek alma/geri yükleme çalıştırmaz.

Host script'i burada test edilmez (entegrasyon, elle denenecek).
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.admin import _failures as _restore_failures
from app.api.admin import router
from app.api.auth import router as auth_router
from app.config import settings
from app.db import get_session
from app.models import AuditLog, RestoreRequest
from app.services import backup, restore
from conftest import AUTH_PASSWORD, AUTH_USERNAME

SIFRE = AUTH_PASSWORD


@pytest.fixture
def admin_password(auth_account):
    """Adı geçmişten kalma (eskiden ADMIN_PASSWORD): artık tek hesabın
    ortak girişini (auth_account) kurar — geri yükleme onayındaki şifre
    tekrarı da (auth.verify_password) aynı hesaba karşı doğrular.

    Geri yüklemenin KENDİ kilitleme sayacı (admin.py::_failures, giriş
    kilidinden AYRI) da testler arasında sızmasın diye temizlenir."""
    _restore_failures.clear()
    return auth_account


@pytest_asyncio.fixture(loop_scope="session")
async def client(session):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def ham_snapshot(when: datetime, short_id: str, boyut: int | None = 38012) -> dict:
    """restic snapshots --json çıktısının gerçek biçimi (restic 0.19)."""
    raw = {
        "time": when.isoformat(),
        "paths": ["/hesaplik.dump"],
        "hostname": "hesaplik",
        "username": "xfgq",
        "tags": ["hesaplik"],
        "id": short_id * 8,
        "short_id": short_id,
    }
    if boyut is not None:
        raw["summary"] = {"total_bytes_processed": boyut, "total_files_processed": 1}
    return raw


DUN = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
BUGUN = datetime(2026, 8, 4, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def yedekler(monkeypatch):
    """İki yedekli depo. Liste sırası KASTEN ters (eski önce) veriliyor:
    servisin kendisi en yeniyi üste almalı."""

    async def _liste():
        return [ham_snapshot(DUN, "aaaa1111"), ham_snapshot(BUGUN, "bbbb2222")]

    monkeypatch.setattr(backup, "list_snapshots", _liste)
    monkeypatch.setattr(settings, "restic_repository", "/var/www/hesaplik/data/backups")


async def _login(client) -> None:
    r = await client.post(
        "/api/auth/login", json={"username": AUTH_USERNAME, "password": SIFRE}
    )
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


# ---------------------------------------------------------------- koruma

async def test_liste_sifresiz_erisilemez(client, admin_password, yedekler):
    r = await client.get("/api/admin/backups")
    assert r.status_code == 401
    # Depo yolu ve yedek kimlikleri sızmadı.
    assert "data/backups" not in r.text
    assert "bbbb2222" not in r.text


async def test_restore_sifresiz_erisilemez(client, admin_password, yedekler):
    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "bbbb2222", "password": SIFRE})
    assert r.status_code == 401


async def test_uydurma_token_gecmez(client, admin_password, yedekler):
    r = await client.get("/api/admin/backups", headers={"Authorization": "Bearer 9999999999.x"})
    assert r.status_code == 401


# ---------------------------------------------------------------- liste

async def test_liste_en_yeni_ustte_ve_detayli(client, admin_password, yedekler):
    await _login(client)
    r = await client.get("/api/admin/backups")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total"] == 2
    assert body["repository"] == "/var/www/hesaplik/data/backups"
    assert [i["id"] for i in body["items"]] == ["bbbb2222", "aaaa1111"]

    ilk = body["items"][0]
    assert ilk["time"].startswith("2026-08-04T09:30")
    assert ilk["size_bytes"] == 38012
    assert ilk["hostname"] == "hesaplik"
    assert ilk["tags"] == ["hesaplik"]
    assert ilk["paths"] == ["/hesaplik.dump"]
    assert ilk["full_id"] == "bbbb2222" * 8


async def test_liste_ozet_bilgileri(client, admin_password, yedekler):
    """Üstteki özet: son yedek + sonraki otomatik yedek tahmini."""
    await _login(client)
    body = (await client.get("/api/admin/backups")).json()

    assert body["last_time"].startswith("2026-08-04T09:30")
    assert body["auto_interval_minutes"] == backup.AUTO_INTERVAL_MINUTES
    beklenen = (BUGUN + timedelta(minutes=backup.AUTO_INTERVAL_MINUTES)).isoformat()
    assert body["next_auto_estimate"].replace("Z", "+00:00") == beklenen


async def test_liste_bos_depo(client, admin_password, monkeypatch):
    async def _bos():
        return []

    monkeypatch.setattr(backup, "list_snapshots", _bos)
    await _login(client)
    body = (await client.get("/api/admin/backups")).json()

    assert body["total"] == 0
    assert body["items"] == []
    assert body["last_time"] is None
    assert body["next_auto_estimate"] is None


async def test_liste_depo_okunamazsa_503(client, admin_password, monkeypatch):
    """"Yedek yok" ile "depoya erişilemiyor" karıştırılmamalı."""

    async def _patlat():
        raise backup.BackupUnavailable("RESTIC_PASSWORD yanlış, depo açılamadı")

    monkeypatch.setattr(backup, "list_snapshots", _patlat)
    await _login(client)

    r = await client.get("/api/admin/backups")
    assert r.status_code == 503
    assert "RESTIC_PASSWORD" in r.json()["detail"]


async def test_liste_boyutsuz_snapshot_uydurmaz(client, admin_password, monkeypatch):
    """restic 'summary' vermezse boyut null kalır — 0 yazmak yalan olurdu."""

    async def _liste():
        return [ham_snapshot(BUGUN, "cccc3333", boyut=None)]

    monkeypatch.setattr(backup, "list_snapshots", _liste)
    await _login(client)

    body = (await client.get("/api/admin/backups")).json()
    assert body["items"][0]["size_bytes"] is None


async def test_bozuk_satir_listeyi_dusurmez(client, admin_password, monkeypatch):
    """Tek bozuk kayıt yüzünden tüm yedek listesi kaybolmamalı."""

    async def _liste():
        return [{"short_id": "kirik", "time": "tarih değil"}, ham_snapshot(BUGUN, "dddd4444")]

    monkeypatch.setattr(backup, "list_snapshots", _liste)
    await _login(client)

    body = (await client.get("/api/admin/backups")).json()
    assert [i["id"] for i in body["items"]] == ["dddd4444"]


# ---------------------------------------------------------------- geri yükleme

async def test_restore_yanlis_sifre_reddedilir(client, admin_password, yedekler, session):
    """403, 401 DEĞİL: oturum geçerli, izin verilmeyen tek şey bu işlem.
    401 dönseydi panel şifre ekranına düşer, kullanıcı yanlış yazdığını
    anlamazdı."""
    await _login(client)
    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "bbbb2222", "password": "yanlis"})

    assert r.status_code == 403
    # Reddedilen istek audit'e "yapıldı" diye geçmemeli.
    kayitlar = (await session.execute(select(AuditLog))).scalars().all()
    assert kayitlar == []


async def test_restore_bos_sifre_reddedilir(client, admin_password, yedekler):
    await _login(client)
    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "bbbb2222", "password": ""})
    assert r.status_code == 403


async def test_restore_istegi_kuyruga_yazilir(client, admin_password, yedekler, session):
    """Akışın sonu: istek KALICI olarak kuyruğa yazılır (host uygulayacak)."""
    await _login(client)
    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "bbbb2222", "password": SIFRE})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["message"] == restore.ACCEPTED_MESSAGE
    assert body["request"]["snapshot_id"] == "bbbb2222"
    assert body["request"]["status"] == "bekliyor"
    assert body["request"]["pre_backup_snapshot"] is None    # host dolduracak
    assert body["request"]["finished_at"] is None

    # Tabloda gerçekten duruyor: panel kapansa, API yeniden başlasa da kalır.
    kayit = (await session.execute(select(RestoreRequest))).scalars().one()
    assert kayit.snapshot_id == "bbbb2222"
    assert kayit.status == restore.STATUS_WAITING
    assert kayit.requested_by.startswith("admin-panel@")


async def test_restore_api_gercek_isi_yapmaz(client, admin_password, yedekler, monkeypatch):
    """API yalnızca istek yazar: yedek alma ya da geri yükleme çalıştırmaz
    (o host'un işi). Çağrılırsa test patlar."""

    async def _olmamali(*_a, **_k):
        raise AssertionError("API gerçek yedek/geri yükleme çalıştırmamalı")

    monkeypatch.setattr(backup, "run_backup", _olmamali)
    monkeypatch.setattr(backup, "ensure_can_run_backup", _olmamali)

    await _login(client)
    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "bbbb2222", "password": SIFRE})
    assert r.status_code == 200


# ------------------------------------------------- tek seferde tek restore

async def test_ikinci_restore_409(client, admin_password, yedekler):
    """Süren bir geri yükleme varken ikincisi başlatılamaz."""
    await _login(client)
    ilk = await client.post("/api/admin/backups/restore",
                            json={"snapshot_id": "bbbb2222", "password": SIFRE})
    assert ilk.status_code == 200

    ikinci = await client.post("/api/admin/backups/restore",
                               json={"snapshot_id": "aaaa1111", "password": SIFRE})
    assert ikinci.status_code == 409
    assert "sürüyor" in ikinci.json()["detail"]


async def test_biten_restore_yeni_istegi_engellemez(client, admin_password, yedekler, session):
    await _login(client)
    await client.post("/api/admin/backups/restore",
                      json={"snapshot_id": "bbbb2222", "password": SIFRE})

    kayit = (await session.execute(select(RestoreRequest))).scalars().one()
    kayit.status = restore.STATUS_DONE          # host bitirmiş gibi
    await session.flush()

    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "aaaa1111", "password": SIFRE})
    assert r.status_code == 200


async def test_veritabani_iki_aktif_istege_izin_vermez(session):
    """Asıl garanti uygulama katmanında değil, kısmi tekil indekste
    (uq_restore_tek_aktif): eşzamanlı iki istek veritabanınca reddedilir."""
    session.add(RestoreRequest(snapshot_id="aaaa1111", requested_by="t1", status="bekliyor"))
    await session.flush()

    session.add(RestoreRequest(snapshot_id="bbbb2222", requested_by="t2", status="yukleniyor"))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


# ---------------------------------------------------------------- durum

async def test_durum_istek_yokken_bos(client, admin_password):
    await _login(client)
    body = (await client.get("/api/admin/backups/restore/status")).json()
    assert body == {"active": False, "request": None}


async def test_durum_sifresiz_erisilemez(client, admin_password):
    assert (await client.get("/api/admin/backups/restore/status")).status_code == 401


async def test_durum_aktif_istegi_gosterir(client, admin_password, yedekler, session):
    await _login(client)
    await client.post("/api/admin/backups/restore",
                      json={"snapshot_id": "bbbb2222", "password": SIFRE})

    body = (await client.get("/api/admin/backups/restore/status")).json()
    assert body["active"] is True
    assert body["request"]["status"] == "bekliyor"
    assert body["request"]["stale"] is False


async def test_durum_host_ilerlemesini_yansitir(client, admin_password, yedekler, session):
    """Durumu HOST yazar; API okuyup aynen aktarır (pre_backup dahil)."""
    await _login(client)
    await client.post("/api/admin/backups/restore",
                      json={"snapshot_id": "bbbb2222", "password": SIFRE})

    kayit = (await session.execute(select(RestoreRequest))).scalars().one()
    kayit.status = restore.STATUS_LOADING
    kayit.pre_backup_snapshot = "9999abcd"
    kayit.started_at = datetime.now(timezone.utc)
    await session.flush()

    body = (await client.get("/api/admin/backups/restore/status")).json()
    assert body["active"] is True
    assert body["request"]["status"] == "yukleniyor"
    assert body["request"]["pre_backup_snapshot"] == "9999abcd"


async def test_durum_biten_istegi_de_gosterir(client, admin_password, yedekler, session):
    """Aktif yoksa EN SON istek gösterilir: kullanıcı sonucu görebilsin."""
    await _login(client)
    await client.post("/api/admin/backups/restore",
                      json={"snapshot_id": "bbbb2222", "password": SIFRE})

    kayit = (await session.execute(select(RestoreRequest))).scalars().one()
    kayit.status = restore.STATUS_ERROR
    kayit.error_detail = "pg_restore başarısız: bağlantı reddedildi"
    kayit.finished_at = datetime.now(timezone.utc)
    await session.flush()

    body = (await client.get("/api/admin/backups/restore/status")).json()
    assert body["active"] is False
    assert body["request"]["status"] == "hata"
    assert "pg_restore" in body["request"]["error_detail"]


async def test_uzun_suredir_bekleyen_istek_stale(session):
    """Host izleyici çalışmıyorsa istek 'bekliyor'da kalır — panel bunu
    dolaylı sinyal olarak gösterir (kesin bilgi değil)."""
    eski = datetime.now(timezone.utc) - timedelta(minutes=restore.APPLIER_STALE_MINUTES + 1)
    bekleyen = RestoreRequest(
        snapshot_id="aaaa1111", requested_by="t", status="bekliyor", requested_at=eski
    )
    assert restore.is_stale(bekleyen) is True

    # Yedekleme/yükleme uzun sürebilir; o gecikme normaldir, stale değildir.
    bekleyen.status = restore.STATUS_LOADING
    assert restore.is_stale(bekleyen) is False


async def test_restore_istegi_audit_loga_yazilir(client, admin_password, yedekler, session):
    """Kim, ne zaman, hangi yedeği istedi — kayıt altında."""
    await _login(client)
    await client.post("/api/admin/backups/restore",
                      json={"snapshot_id": "bbbb2222", "password": SIFRE})

    kayit = (await session.execute(select(AuditLog))).scalars().one()
    assert kayit.action == restore.AUDIT_ACTION
    assert kayit.entity == restore.AUDIT_ENTITY
    assert kayit.entity_id == "bbbb2222"
    assert kayit.actor.startswith("admin-panel@")
    assert kayit.before["snapshot_time"].startswith("2026-08-04T09:30")
    assert kayit.after["status"] == restore.STATUS_WAITING
    # Audit kaydı istekle bağlanır: hangi kuyruk satırından doğdu belli.
    istek = (await session.execute(select(RestoreRequest))).scalars().one()
    assert kayit.after["request_id"] == istek.id


async def test_restore_olmayan_yedek_404(client, admin_password, yedekler):
    await _login(client)
    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "olmayan", "password": SIFRE})
    assert r.status_code == 404


async def test_restore_tam_kimlikle_de_calisir(client, admin_password, yedekler):
    await _login(client)
    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "bbbb2222" * 8, "password": SIFRE})
    assert r.status_code == 200
    # Kuyruğa KISA kimlik yazılır: host script restic'e onu verecek.
    assert r.json()["request"]["snapshot_id"] == "bbbb2222"


async def test_restore_cok_yanlis_deneme_kilitler(client, admin_password, yedekler):
    """Şifre denemesi giriş ekranıyla aynı kilide takılır."""
    await _login(client)
    for _ in range(10):
        r = await client.post("/api/admin/backups/restore",
                              json={"snapshot_id": "bbbb2222", "password": "yanlis"})
        assert r.status_code == 403

    r = await client.post("/api/admin/backups/restore",
                          json={"snapshot_id": "bbbb2222", "password": SIFRE})
    assert r.status_code == 429


async def test_restore_acik_bayragi(client, admin_password, yedekler):
    """İstek kuyruğu bağlandı: panel artık "kapalı" uyarısı çizmez."""
    assert restore.is_available() is True

    await _login(client)
    body = (await client.get("/api/admin/backups")).json()
    assert body["restore_available"] is True
