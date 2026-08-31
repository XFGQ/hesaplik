"""Sistem sağlığı (app/services/health.py + GET /api/admin/health).

Bu ekranın tek işi doğru söylemek: bir şey çalışmıyorken "çalışıyor" demesi,
çalışırken "bozuk" demesinden daha tehlikeli. Testler durum eşlemesini
(ok/uyari/hata/bilgi) ve şifre korumasını kilitler.

Ollama'ya gerçekten gidilmez: conftest gerçek httpx taşıyıcısını kapatıyor,
buradaki testler httpx.MockTransport ile sahte yanıt verir.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from app.api.admin import router
from app.api.auth import router as auth_router
from app.config import settings
from app.db import get_session
from app.models import Person, RawMessage, Transaction, TxKind, TxSource
from app.services import backup, health
from conftest import AUTH_PASSWORD, AUTH_USERNAME

SIFRE = AUTH_PASSWORD


@pytest.fixture
def admin_password(auth_account):
    """Adı geçmişten kalma (eskiden ADMIN_PASSWORD): artık tek hesabın
    ortak girişini (auth_account) kurar."""
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


@pytest.fixture
def yedek_yok(monkeypatch):
    """Varsayılan: yedek deposu okunamıyor. Testlerin çoğu yedeği
    umursamıyor; restic'e gerçekten gitmesinler diye baştan kesiyoruz."""

    async def _patlat():
        raise backup.BackupUnavailable("restic kurulu değil")

    monkeypatch.setattr(backup, "list_snapshots", _patlat)


def ollama_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def tags_yaniti(*models: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": m} for m in models]})

    return handler


async def _login(client) -> None:
    r = await client.post(
        "/api/auth/login", json={"username": AUTH_USERNAME, "password": SIFRE}
    )
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


# ---------------------------------------------------------------- koruma

async def test_health_sifresiz_erisilemez(client, admin_password):
    r = await client.get("/api/admin/health")
    assert r.status_code == 401
    # Kurulum ayrıntısı (model adı, depo yolu, sayılar) sızmadı.
    assert "components" not in r.text


async def test_health_uydurma_token_gecmez(client, admin_password):
    r = await client.get("/api/admin/health", headers={"Authorization": "Bearer 9999999999.abc"})
    assert r.status_code == 401


async def test_health_sifreyle_doner(client, admin_password, yedek_yok):
    await _login(client)
    r = await client.get("/api/admin/health")
    assert r.status_code == 200, r.text

    body = r.json()
    assert {c["id"] for c in body["components"]} == {"db", "bot", "llm", "backup", "api"}
    assert body["overall"] in ("ok", "uyari", "hata", "bilgi")
    assert body["checked_at"]


# ---------------------------------------------------------------- veritabanı

async def test_db_sayilari_dogru_raporlar(session, yedek_yok):
    ahmet = Person(full_name="Ahmet Yılmaz")
    pasif = Person(full_name="Silinmiş Kişi", is_active=False)
    session.add_all([ahmet, pasif])
    await session.flush()

    session.add(
        Transaction(
            person_id=ahmet.id,
            kind=TxKind.DEBIT,
            amount_try=Decimal("1500.00"),
            source=TxSource.WEB,
            created_by="test",
        )
    )
    await session.flush()

    check = await health.check_database(session)
    assert check.status == health.STATUS_OK
    detay = dict(check.details)
    assert detay["Kişi"] == "1"       # pasif kişi sayılmaz
    assert detay["İşlem"] == "1"
    assert detay["Son işlem"] == "az önce"


class KopukSession:
    """Bağlantısı gitmiş bir session: her çağrı patlar. Gerçek session'ı
    monkeypatch'lemek yerine sahtesini vermek testi izole tutar (gerçek
    session'ın temizliği bozulmasın)."""

    def __init__(self, mesaj: str):
        self.mesaj = mesaj

    async def execute(self, *_a, **_k):
        raise OperationalError("SELECT 1", None, Exception(self.mesaj))

    async def rollback(self):
        raise OperationalError("ROLLBACK", None, Exception(self.mesaj))


async def test_db_erisilemezse_hata():
    """Bağlantı gitmiş: durum HATA, panel yine de çizilebilsin diye
    istisna dışarı sızmaz."""
    check = await health.check_database(KopukSession("connection refused"))

    assert check.status == health.STATUS_ERROR
    assert check.summary == "Erişilemiyor"
    assert "connection refused" in dict(check.details)["Hata"]


async def test_db_hatasi_parolayi_maskeler():
    kopuk = KopukSession("could not connect to postgresql://hesaplik:gizliparola@db:5432/hesaplik")
    check = await health.check_database(kopuk)

    assert "gizliparola" not in json.dumps(check.details)
    assert "//***@" in dict(check.details)["Hata"]


# ---------------------------------------------------------------- LLM

async def test_llm_none_ise_kapali_gosterir(monkeypatch):
    """LLM_PROVIDER=none bir arıza değil tercihtir: gri "bilgi", hata değil."""
    monkeypatch.setattr(settings, "llm_provider", "none")

    check = await health.check_llm()
    assert check.status == health.STATUS_INFO
    assert check.summary == "Kapalı"
    assert "kural parser" in (check.note or "")


async def test_llm_modeli_yukluyse_ok(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:3b")

    async with ollama_client(tags_yaniti("qwen2.5:3b", "llama3:8b")) as c:
        check = await health.check_llm(c)

    assert check.status == health.STATUS_OK
    assert check.summary == "qwen2.5:3b yüklü"
    assert dict(check.details)["Yüklü modeller"] == "qwen2.5:3b, llama3:8b"


async def test_llm_modeli_yuklu_degilse_uyari(monkeypatch):
    """Ollama ayakta ama ayarlı model listede yok — çağrı yapılırsa
    başarısız olur, bu HATA değil ama sessiz de geçilemez."""
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:7b")

    async with ollama_client(tags_yaniti("llama3:8b")) as c:
        check = await health.check_llm(c)

    assert check.status == health.STATUS_WARN
    assert check.summary == "qwen2.5:7b yüklü değil"


async def test_llm_etiketsiz_ad_latest_ile_eslesir(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5")

    async with ollama_client(tags_yaniti("qwen2.5:latest")) as c:
        check = await health.check_llm(c)

    assert check.status == health.STATUS_OK


async def test_llm_erisilemezse_hata(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    def _kopuk(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bağlantı reddedildi", request=request)

    async with ollama_client(_kopuk) as c:
        check = await health.check_llm(c)

    assert check.status == health.STATUS_ERROR
    assert check.summary == "Erişilemiyor"


async def test_llm_bozuk_yanit_cokertmez(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    def _cop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="bu json değil")

    async with ollama_client(_cop) as c:
        check = await health.check_llm(c)

    assert check.status == health.STATUS_ERROR


# ---------------------------------------------------------------- bot (dolaylı)

async def test_bot_jeton_api_surecinde_gorunmese_de_aktif_gosterilir(session, monkeypatch):
    # Bug (2026-08): üretimde TELEGRAM_BOT_TOKEN yalnızca `bot` container'ının
    # ortamında var, `api` süreci hiç göremez — ama bot gerçekten çalışıyor
    # ve mesaj işliyor olabilir. Token bu süreçten okunamayacağı için karara
    # hiç katılmamalı; tek gerçek iz deftere düşen son mesajdır.
    monkeypatch.setattr(settings, "telegram_bot_token", None)
    session.add(
        RawMessage(channel="telegram", external_id="9010", chat_id="42", payload={"text": "selam"})
    )
    await session.flush()

    check = await health.check_bot(session)
    assert check.status == health.STATUS_OK
    assert check.summary.startswith("Aktif")
    assert check.measured is False


async def test_bot_jeton_yoksa_ve_mesaj_da_yoksa_bilgi_durumu(session, monkeypatch):
    # Token görünmüyor VE hiç mesaj da gelmemiş — bu ne kesin "kapalı" ne
    # "hata" demektir, yalnızca "henüz bir şey görmedik" (STATUS_INFO).
    monkeypatch.setattr(settings, "telegram_bot_token", None)

    check = await health.check_bot(session)
    assert check.status == health.STATUS_INFO
    assert check.summary == "Mesaj gelmemiş"
    assert check.measured is False


async def test_bot_yeni_mesaj_varsa_aktif(session, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "jeton")
    session.add(
        RawMessage(channel="telegram", external_id="9001", chat_id="42", payload={"text": "selam"})
    )
    await session.flush()

    check = await health.check_bot(session)
    assert check.status == health.STATUS_OK
    assert check.summary.startswith("Aktif")
    # Dolaylı ölçüm: panel bunu kesin bilgi gibi göstermemeli.
    assert check.measured is False
    assert dict(check.details)["Son 24 saat"] == "1 mesaj"


async def test_bot_eski_mesaj_sessiz_ama_hata_degil(session, monkeypatch):
    """Mesaj gelmemiş olması bot öldü demek değildir — kimse yazmamış da
    olabilir. Bu yüzden asla hata/uyarı değil."""
    monkeypatch.setattr(settings, "telegram_bot_token", "jeton")
    eski = datetime.now(timezone.utc) - timedelta(hours=3)
    session.add(
        RawMessage(
            channel="telegram", external_id="9002", chat_id="42",
            payload={"text": "selam"}, received_at=eski,
        )
    )
    await session.flush()

    check = await health.check_bot(session)
    assert check.status == health.STATUS_INFO
    assert check.summary == "Sessiz · son mesaj 3 saat önce"


async def test_bot_web_mesajini_saymaz(session, monkeypatch):
    """channel='web' kaydı bot hakkında hiçbir şey söylemez."""
    monkeypatch.setattr(settings, "telegram_bot_token", "jeton")
    session.add(RawMessage(channel="web", external_id="w1", payload={"text": "selam"}))
    await session.flush()

    check = await health.check_bot(session)
    assert check.summary == "Mesaj gelmemiş"
    assert check.status == health.STATUS_INFO


# ---------------------------------------------------------------- yedekleme

def _snapshot(dt: datetime, short_id: str = "2cadfd44") -> dict:
    return {"time": dt.isoformat(), "short_id": short_id, "tags": ["hesaplik"]}


async def test_yedek_taze_ise_ok(monkeypatch):
    yeni = datetime.now(timezone.utc) - timedelta(minutes=4)
    eski = datetime.now(timezone.utc) - timedelta(days=2)

    async def _liste():
        return [_snapshot(eski, "aaa"), _snapshot(yeni, "bbb")]

    monkeypatch.setattr(backup, "list_snapshots", _liste)

    check = await health.check_backup()
    assert check.status == health.STATUS_OK
    assert check.summary == "Son yedek 4 dk önce"
    assert dict(check.details)["Yedek sayısı"] == "2"


async def test_yedek_gecikmisse_uyari(monkeypatch):
    async def _liste():
        return [_snapshot(datetime.now(timezone.utc) - timedelta(hours=5))]

    monkeypatch.setattr(backup, "list_snapshots", _liste)

    check = await health.check_backup()
    assert check.status == health.STATUS_WARN
    assert "gecikmiş" in check.summary


async def test_yedek_yoksa_uyari(monkeypatch):
    async def _liste():
        return []

    monkeypatch.setattr(backup, "list_snapshots", _liste)

    check = await health.check_backup()
    assert check.status == health.STATUS_WARN
    assert check.summary == "Henüz yedek alınmamış"


async def test_yedek_deposu_okunamazsa_uyari(monkeypatch):
    async def _patlat():
        raise backup.BackupUnavailable("RESTIC_PASSWORD tanımlı değil (.env eksik)")

    monkeypatch.setattr(backup, "list_snapshots", _patlat)

    check = await health.check_backup()
    assert check.status == health.STATUS_WARN
    assert check.summary == "Durum okunamadı"
    assert "RESTIC_PASSWORD" in dict(check.details)["Sebep"]


# ---------------------------------------------------------------- genel özet

def test_genel_ozet_en_kotu_duruma_gore():
    def c(status: str) -> health.Check:
        return health.Check(id="x", label="X", status=status, summary="")

    hepsi_iyi = [c(health.STATUS_OK), c(health.STATUS_INFO)]
    assert health.overall_status(hepsi_iyi) == health.STATUS_OK
    assert health.overall_text(hepsi_iyi) == "Tüm sistemler çalışıyor"

    bir_uyari = [c(health.STATUS_OK), c(health.STATUS_WARN)]
    assert health.overall_status(bir_uyari) == health.STATUS_WARN
    assert health.overall_text(bir_uyari) == "1 uyarı var"

    karisik = [c(health.STATUS_ERROR), c(health.STATUS_WARN), c(health.STATUS_WARN)]
    assert health.overall_status(karisik) == health.STATUS_ERROR
    assert health.overall_text(karisik) == "1 hata · 2 uyarı var"


def test_kapali_llm_geneli_bozmaz():
    """"bilgi" durumu genel özeti kırmızıya çevirmez."""
    checks = [
        health.Check(id="llm", label="LLM", status=health.STATUS_INFO, summary="Kapalı"),
        health.Check(id="api", label="API", status=health.STATUS_OK, summary="Çalışıyor"),
    ]
    assert health.overall_status(checks) == health.STATUS_OK
    assert health.overall_text(checks) == "Tüm sistemler çalışıyor"


def test_api_kontrolu_surumu_bildirir():
    from app import __version__

    check = health.check_api()
    assert check.status == health.STATUS_OK
    assert dict(check.details)["Sürüm"] == __version__


# ---------------------------------------------------------------- uçtan uca

async def test_health_llm_kapali_iken_kapali_gosterir(client, admin_password, monkeypatch, yedek_yok):
    monkeypatch.setattr(settings, "llm_provider", "none")
    await _login(client)

    body = (await client.get("/api/admin/health")).json()
    llm = next(c for c in body["components"] if c["id"] == "llm")

    assert llm["status"] == "bilgi"
    assert llm["summary"] == "Kapalı"
    # Kapalı LLM + okunamayan yedek: yedek uyarı verir, LLM geneli bozmaz.
    assert body["overall"] == "uyari"
    assert body["overall_text"] == "1 uyarı var"


async def test_health_db_cokse_bile_200_doner(client, admin_password, monkeypatch, yedek_yok):
    """Sağlık sayfası tam da sorun varken açılmalı: DB gitmiş olsa bile uç
    500 değil 200 + "hata" durumu döndürür."""

    async def _db_kopuk(_session):
        return health.Check(
            id="db", label="Veritabanı", status=health.STATUS_ERROR, summary="Erişilemiyor"
        )

    monkeypatch.setattr(health, "check_database", _db_kopuk)
    await _login(client)

    r = await client.get("/api/admin/health")
    assert r.status_code == 200

    body = r.json()
    db = next(c for c in body["components"] if c["id"] == "db")
    assert db["status"] == "hata"
    assert body["overall"] == "hata"
    assert "hata" in body["overall_text"]
