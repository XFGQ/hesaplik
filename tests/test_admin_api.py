"""Admin paneli uçları (/api/admin/*).

En kritik davranış: **şifresiz hiçbir veri dışarı çıkmaz.** İşlem akışı ham
müşteri mesajlarını, kişi adlarını ve tutarları içerir; bu uç korumasız
kalırsa tüm defter sızar. Buradaki testler korumayı, giriş akışını ve
filtre/sayfalamayı kilitler.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import _failures, router
from app.config import settings
from app.db import get_session
from app.models import Person, RawMessage, Transaction, TxKind, TxSource
from app.services import message_trace

SIFRE = "cok-gizli-parola"


@pytest.fixture
def admin_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", SIFRE)
    _failures.clear()
    return SIFRE


@pytest.fixture
def admin_disabled(monkeypatch):
    """ADMIN_PASSWORD tanımsız kurulum: panel tamamen kapalı olmalı."""
    monkeypatch.setattr(settings, "admin_password", "")
    _failures.clear()


@pytest_asyncio.fixture(loop_scope="session")
async def client(session):
    """Yalnızca admin router'ı içeren küçük bir uygulama. get_session testin
    kendi session'ına bağlanır ki gerçek veritabanına gidilmesin."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client) -> str:
    r = await client.post("/api/admin/login", json={"password": SIFRE})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---------------------------------------------------------------- koruma

async def test_flow_sifresiz_erisilemez(client, admin_password):
    r = await client.get("/api/admin/flow")
    assert r.status_code == 401
    assert "text" not in r.text  # akış satırı sızmadı


async def test_me_sifresiz_401(client, admin_password):
    assert (await client.get("/api/admin/me")).status_code == 401


async def test_yanlis_sifre_giremez(client, admin_password):
    r = await client.post("/api/admin/login", json={"password": "yanlis"})
    assert r.status_code == 401
    assert (await client.get("/api/admin/flow")).status_code == 401


async def test_uydurma_token_gecmez(client, admin_password):
    for sahte in ("", "abc", "9999999999.deadbeef", "abc.def"):
        r = await client.get("/api/admin/flow", headers={"Authorization": f"Bearer {sahte}"})
        assert r.status_code == 401, sahte


async def test_dogru_sifre_ile_calisir(client, admin_password):
    token = await _login(client)

    # Giriş çerezi bırakır: sonraki istek başlıksız da geçer.
    assert (await client.get("/api/admin/me")).status_code == 200

    client.cookies.clear()
    r = await client.get("/api/admin/flow", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["items"] == []


async def test_cerez_httponly(client, admin_password):
    r = await client.post("/api/admin/login", json={"password": SIFRE})
    assert "httponly" in r.headers["set-cookie"].lower()


async def test_cikis_oturumu_kapatir(client, admin_password):
    await _login(client)
    assert (await client.get("/api/admin/me")).status_code == 200

    await client.post("/api/admin/logout")
    assert (await client.get("/api/admin/me")).status_code == 401


async def test_sifre_tanimsizsa_panel_kapali(client, admin_disabled):
    """ADMIN_PASSWORD boşken boş şifreyle içeri girilemez."""
    r = await client.post("/api/admin/login", json={"password": ""})
    assert r.status_code == 503
    assert (await client.get("/api/admin/flow")).status_code == 401


async def test_cok_fazla_yanlis_deneme_kilitler(client, admin_password):
    for _ in range(10):
        assert (await client.post("/api/admin/login", json={"password": "yanlis"})).status_code == 401

    # Kilitlendikten sonra DOĞRU şifre bile beklemeli.
    r = await client.post("/api/admin/login", json={"password": SIFRE})
    assert r.status_code == 429


# ---------------------------------------------------------------- işlem akışı

@pytest_asyncio.fixture(loop_scope="session")
async def akis(session):
    """Üç mesajlık örnek akış: biri deftere yazılmış borç, biri sorgu, biri
    anlaşılamamış."""
    ahmet = Person(full_name="Ahmet Yılmaz")
    veli = Person(full_name="Veli Kaya")
    session.add_all([ahmet, veli])
    await session.flush()

    tx = Transaction(
        person_id=ahmet.id,
        kind=TxKind.DEBIT,
        amount_try=Decimal("15000.00"),
        source=TxSource.TELEGRAM_TEXT,
        created_by="telegram-bot",
    )
    session.add(tx)
    await session.flush()

    dun = datetime.now(timezone.utc) - timedelta(days=1)
    rows = [
        RawMessage(
            channel="telegram",
            external_id="7001",
            chat_id="42",
            payload={"message": {"chat": {"id": 42}, "text": "ahmet 20 balya saman 15000"}},
            received_at=dun,
            transaction_id=tx.id,
            detected_kind=message_trace.KIND_DEBT,
            detected_person="Ahmet Yılmaz",
            detected_amount=Decimal("15000.00"),
            detected_product="Saman",
            detected_qty=Decimal("20.00"),
            detected_unit="balya",
            parse_source=message_trace.SOURCE_REGEX,
            parse_ms=12,
            outcome=message_trace.OUTCOME_RECORDED,
            outcome_detail="deftere yazıldı · niyet: debt",
        ),
        RawMessage(
            channel="telegram",
            external_id="7002",
            chat_id="42",
            payload={"message": {"chat": {"id": 42}, "text": "veli bakiye"}},
            detected_kind=message_trace.KIND_QUERY,
            detected_person="Veli Kaya",
            parse_source=message_trace.SOURCE_REGEX,
            parse_ms=3,
            outcome=message_trace.OUTCOME_ANSWERED,
            outcome_detail="bakiye gösterildi · niyet: balance_query",
        ),
        RawMessage(
            channel="telegram",
            external_id="7003",
            chat_id="42",
            payload={"message": {"chat": {"id": 42}, "text": "hava güzel"}},
            detected_kind=message_trace.KIND_NONE,
            parse_source=message_trace.SOURCE_NONE,
            parse_ms=8400,
            outcome=message_trace.OUTCOME_IGNORED,
            outcome_detail="anlaşılamadı",
        ),
    ]
    session.add_all(rows)
    await session.flush()
    return {"tx": tx, "ahmet": ahmet, "dun": dun}


async def test_flow_zaman_sirali_doner(client, admin_password, akis):
    await _login(client)
    body = (await client.get("/api/admin/flow")).json()

    assert body["total"] == 3
    # En yeni üstte: dünkü borç mesajı en altta kalır.
    assert body["items"][-1]["text"] == "ahmet 20 balya saman 15000"


async def test_flow_algilanan_ve_islem_bilgisi(client, admin_password, akis):
    await _login(client)
    body = (await client.get("/api/admin/flow?kind=debt")).json()

    assert body["total"] == 1
    row = body["items"][0]
    assert row["text"] == "ahmet 20 balya saman 15000"
    assert row["detected_person"] == "Ahmet Yılmaz"
    assert row["detected_amount"] == "15000.00"
    assert row["detected_product"] == "Saman"
    assert row["detected_unit"] == "balya"
    assert row["parse_source"] == "regex"
    assert row["parse_ms"] == 12
    assert row["outcome"] == "kaydedildi"
    # Oluşan defter kaydı satıra bağlı gelir.
    assert row["transaction"]["id"] == akis["tx"].id
    assert row["transaction"]["kind"] == "DEBIT"
    assert row["transaction"]["person_name"] == "Ahmet Yılmaz"


async def test_flow_islem_yoksa_transaction_none(client, admin_password, akis):
    await _login(client)
    body = (await client.get("/api/admin/flow?kind=query")).json()
    assert body["items"][0]["transaction"] is None


async def test_flow_kisi_filtresi(client, admin_password, akis):
    await _login(client)
    body = (await client.get("/api/admin/flow?person=veli")).json()

    assert body["total"] == 1
    assert body["items"][0]["detected_person"] == "Veli Kaya"


async def test_flow_tarih_filtresi(client, admin_password, akis):
    await _login(client)
    bugun = datetime.now(timezone.utc).date().isoformat()

    bugunku = (await client.get(f"/api/admin/flow?from={bugun}")).json()
    assert bugunku["total"] == 2  # dünkü borç mesajı düştü

    dunku = (await client.get(f"/api/admin/flow?to={akis['dun'].date().isoformat()}")).json()
    assert dunku["total"] == 1
    assert dunku["items"][0]["detected_kind"] == "debt"


async def test_flow_sayfalama(client, admin_password, akis):
    await _login(client)

    ilk = (await client.get("/api/admin/flow?limit=2&offset=0")).json()
    assert ilk["total"] == 3 and len(ilk["items"]) == 2

    ikinci = (await client.get("/api/admin/flow?limit=2&offset=2")).json()
    assert ikinci["total"] == 3 and len(ikinci["items"]) == 1
    # Sayfalar çakışmaz.
    assert {r["id"] for r in ilk["items"]}.isdisjoint({r["id"] for r in ikinci["items"]})


async def test_flow_gecersiz_tur_422(client, admin_password, akis):
    await _login(client)
    r = await client.get("/api/admin/flow?kind=olmayan")
    assert r.status_code == 422
