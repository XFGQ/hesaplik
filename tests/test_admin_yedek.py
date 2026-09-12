"""Admin paneli "Şimdi Yedek Al ve Telegram'a Gönder" ucu
(POST /api/admin/yedek-gonder).

Geri yüklemenin aksine burada host'a iş devredilmez: pg_dump container'ın
içinden çalışır ve sonuç panelde anında görünür. Testler API sözleşmesini
kilitler — gerçek pg_dump/Telegram çağrısı YAPILMAZ (o
app/services/telegram_yedek.py'nin işi, entegrasyon elle denenir):

- şifresiz kimse veritabanını dışarı gönderemez (401),
- yapılandırma eksikse 503, meşgulse 409, döküm/Telegram hatası 502,
- başarı da başarısızlık da audit_log'a yazılır (veritabanının TAMAMI
  dışarı çıkıyor, izi kalmalı).
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.admin import router
from app.api.auth import router as auth_router
from app.config import settings
from app.db import get_session
from app.models import AuditLog
from app.services import telegram_yedek
from conftest import AUTH_PASSWORD, AUTH_USERNAME

ADMIN_CHAT = "4242"


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
def yedek_chat(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_chat_id", ADMIN_CHAT)


def _sonuc(monkeypatch, sonuc: telegram_yedek.YedekSonucu) -> list[int]:
    cagrilar: list[int] = []

    async def _gonder(chat_id: int):
        cagrilar.append(chat_id)
        return sonuc

    monkeypatch.setattr(telegram_yedek, "yedek_gonder", _gonder)
    return cagrilar


async def _login(client) -> None:
    r = await client.post(
        "/api/auth/login", json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD}
    )
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


async def _audit(session) -> list[AuditLog]:
    rows = await session.execute(
        select(AuditLog).where(AuditLog.action == telegram_yedek.AUDIT_ACTION)
    )
    return list(rows.scalars())


# ---------------------------------------------------------------- koruma


async def test_sifresiz_yedek_gonderilemez(client, auth_account, yedek_chat, monkeypatch):
    cagrilar = _sonuc(monkeypatch, telegram_yedek.YedekSonucu(True, "ok", "gonderildi"))

    r = await client.post("/api/admin/yedek-gonder")

    assert r.status_code == 401
    assert cagrilar == []  # veritabanı DÖKÜLMEDİ


async def test_uydurma_token_gecmez(client, auth_account, yedek_chat, monkeypatch):
    cagrilar = _sonuc(monkeypatch, telegram_yedek.YedekSonucu(True, "ok", "gonderildi"))

    r = await client.post(
        "/api/admin/yedek-gonder", headers={"Authorization": "Bearer 9999999999.x"}
    )

    assert r.status_code == 401
    assert cagrilar == []


# ---------------------------------------------------------------- sonuçlar


async def test_basarili_yedek_sonucu_ve_denetim_kaydi(
    client, session, auth_account, yedek_chat, monkeypatch
):
    cagrilar = _sonuc(
        monkeypatch,
        telegram_yedek.YedekSonucu(
            True, "Gönderildi: hesaplik.sql.gz (512 KB)", "gonderildi", "hesaplik.sql.gz", 524288
        ),
    )
    await _login(client)

    r = await client.post("/api/admin/yedek-gonder")

    assert r.status_code == 200, r.text
    govde = r.json()
    assert govde["ok"] is True
    assert govde["filename"] == "hesaplik.sql.gz"
    assert govde["size_bytes"] == 524288
    assert cagrilar == [int(ADMIN_CHAT)]

    kayit = (await _audit(session))[-1]
    assert kayit.actor == f"admin-panel:{AUTH_USERNAME}"
    assert kayit.after["durum"] == "gonderildi"


async def test_chat_id_tanimsizsa_503(client, session, auth_account, monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_chat_id", "")
    cagrilar = _sonuc(monkeypatch, telegram_yedek.YedekSonucu(True, "ok", "gonderildi"))
    await _login(client)

    r = await client.post("/api/admin/yedek-gonder")

    assert r.status_code == 503
    assert "TELEGRAM_ADMIN_CHAT_ID" in r.json()["detail"]
    assert cagrilar == []


async def test_zaten_yedek_suruyorsa_409(client, session, auth_account, yedek_chat, monkeypatch):
    _sonuc(
        monkeypatch,
        telegram_yedek.YedekSonucu(
            False, "Zaten bir yedek alınıyor, biraz sonra tekrar deneyin.", "mesgul"
        ),
    )
    await _login(client)

    r = await client.post("/api/admin/yedek-gonder")

    assert r.status_code == 409
    assert "Zaten bir yedek alınıyor" in r.json()["detail"]


async def test_hata_502_ve_sebep_kullaniciya_gosterilir(
    client, session, auth_account, yedek_chat, monkeypatch
):
    _sonuc(
        monkeypatch,
        telegram_yedek.YedekSonucu(False, "Döküm yarım kalmış (pg_dump bitiş satırı yok).", "hata"),
    )
    await _login(client)

    r = await client.post("/api/admin/yedek-gonder")

    assert r.status_code == 502
    assert r.json()["detail"] == "Döküm yarım kalmış (pg_dump bitiş satırı yok)."


async def test_basarisiz_deneme_de_denetime_yazilir(
    client, session, auth_account, yedek_chat, monkeypatch
):
    """Başarısız deneme sessizce kaybolmaz: kim ne zaman denedi, izi kalır."""
    _sonuc(
        monkeypatch,
        telegram_yedek.YedekSonucu(
            False, "Yedek 50MB'ı aştı (61,2 MB), alternatif gerekli.", "sinir"
        ),
    )
    await _login(client)

    r = await client.post("/api/admin/yedek-gonder")

    assert r.status_code == 502
    kayit = (await _audit(session))[-1]
    assert kayit.after["durum"] == "sinir"
