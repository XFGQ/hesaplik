"""Admin paneli uçları (/api/admin/*).

En kritik davranış: **tokensız hiçbir veri dışarı çıkmaz.** İşlem akışı ham
müşteri mesajlarını, kişi adlarını ve tutarları içerir; bu uç korumasız
kalırsa tüm defter sızar. Giriş akışının kendisi (kullanıcı adı/şifre,
bcrypt, kilitleme) tests/test_auth.py'de test edilir — burada yalnızca
"geçerli JWT'siz hiçbir admin ucu çalışmaz" ve panel-özel davranışlar
(flow/filtre/sayfalama, LLM izleme, kuyruk, arşiv, log) test edilir.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import router
from app.api.auth import router as auth_router
from app.db import get_session
from app.models import Person, RawMessage, Transaction, TxKind, TxSource
from app.services import message_trace
from conftest import AUTH_PASSWORD, AUTH_USERNAME


@pytest.fixture
def admin_password(auth_account):
    """Adı geçmişten kalma (eskiden ADMIN_PASSWORD): artık tek hesabın
    ortak girişini (auth_account) kurar. Aşağıdaki onlarca testin imzasını
    değiştirmemek için bu isimle bırakıldı."""
    return auth_account


@pytest_asyncio.fixture(loop_scope="session")
async def client(session):
    """Admin router'ı + giriş router'ını içeren küçük bir uygulama.
    get_session testin kendi session'ına bağlanır ki gerçek veritabanına
    gidilmesin."""
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client) -> str:
    r = await client.post(
        "/api/auth/login", json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD}
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    # Cookie'nin eski işlevini görür: sonraki her istek otomatik yetkili
    # olsun diye header istemcide kalıcı olarak ayarlanır.
    client.headers["Authorization"] = f"Bearer {token}"
    return token


# ---------------------------------------------------------------- koruma

async def test_flow_tokensiz_erisilemez(client, admin_password):
    r = await client.get("/api/admin/flow")
    assert r.status_code == 401
    assert "text" not in r.text  # akış satırı sızmadı


async def test_uydurma_token_gecmez(client, admin_password):
    for sahte in ("", "abc", "abc.def.ghi", "9999999999.deadbeef"):
        r = await client.get("/api/admin/flow", headers={"Authorization": f"Bearer {sahte}"})
        assert r.status_code == 401, sahte


async def test_dogru_bilgilerle_calisir(client, admin_password):
    token = await _login(client)

    r = await client.get("/api/admin/flow", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["items"] == []


async def test_baska_bir_admin_ucu_da_ayni_tokenla_calisir(client, admin_password):
    """Tek hesap: giriş yapan HER ŞEYE erişir, ayrı ayrı yetki gerekmez."""
    await _login(client)
    assert (await client.get("/api/admin/queue")).status_code == 200


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


# ---------------------------------------------------------------- koruma (yeni bölümler)

YENI_UCLAR = [
    "/api/admin/llm-monitor",
    "/api/admin/queue",
    "/api/admin/persons",
    "/api/admin/persons/1/transactions",
    "/api/admin/archived-persons",
    "/api/admin/archived-transactions",
    "/api/admin/audit-log",
]


@pytest.mark.parametrize("yol", YENI_UCLAR)
async def test_yeni_bolumler_sifresiz_erisilemez(client, admin_password, yol):
    r = await client.get(yol)
    assert r.status_code == 401


# ---------------------------------------------------------------- ortak veri

@pytest_asyncio.fixture(loop_scope="session")
async def panel_verisi(session):
    """Dört yeni bölümü de dolduran küçük bir senaryo: iki kişi, biri
    arşivlenmiş bir hareketi olan aktif kişi, biri tamamen arşivlenmiş kişi;
    bir istek kuyruğu batch'i; iki LLM'e düşmüş mesaj."""
    from datetime import date as _date

    from app.models import PriceHistory, Product
    from app.services import ledger, person_archive, request_queue
    from app.services.ledger import LineInput, TxMeta

    ahmet = Person(full_name="Ahmet Yılmaz")
    mehmet = Person(full_name="Mehmet Kaya")
    session.add_all([ahmet, mehmet])
    await session.flush()

    saman = Product(name="Saman", base_unit="balya")
    session.add(saman)
    await session.flush()
    session.add(
        PriceHistory(product_id=saman.id, unit_price=Decimal("75.00"), valid_from=_date(2026, 1, 1))
    )
    await session.flush()

    def meta():
        return TxMeta(created_by="test")

    # Ahmet: bir borç arşivlenir (silinir), bir borç + bir tahsilat canlı kalır.
    silinecek = await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(5))], meta()
    )
    await ledger.archive_transaction(
        session, silinecek.id, actor="admin-panel@test", reason="yanlış kişi"
    )
    canli_borc = await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(10))], meta()
    )
    await ledger.add_payment(session, ahmet.id, Decimal("200.00"), meta())

    # Mehmet: bir borç, sonra kişi tamamen arşivlenir.
    await ledger.add_debt(
        session, mehmet.id, [LineInput(product_id=saman.id, qty=Decimal(3))], meta()
    )
    arsivli_kisi = await person_archive.archive_person(
        session, mehmet.id, archived_by="admin-panel@test", reason="mükerrer kayıt"
    )

    # İstek kuyruğu: bir batch, biri beklemede biri başarısız.
    batch_id, kayitlar = await request_queue.create_batch(
        session, chat_id="42", texts=["ahmetten 100 al", "bilinmeyen cümle"]
    )
    await request_queue.mark(session, kayitlar[1].id, request_queue.BASARISIZ, hata="anlaşılamadı")

    # LLM izleme: biri başarılı biri hatalı.
    session.add_all(
        [
            RawMessage(
                channel="telegram",
                external_id="9001",
                chat_id="42",
                payload={"message": {"chat": {"id": 42}, "text": "ahmete borç yaz serbest cümle"}},
                detected_kind=message_trace.KIND_DEBT,
                parse_source=message_trace.SOURCE_LLM,
                parse_ms=250,
                outcome=message_trace.OUTCOME_RECORDED,
                outcome_detail="deftere yazıldı",
            ),
            RawMessage(
                channel="telegram",
                external_id="9002",
                chat_id="42",
                payload={"message": {"chat": {"id": 42}, "text": "çok garip bir cümle"}},
                detected_kind=message_trace.KIND_NONE,
                parse_source=message_trace.SOURCE_LLM,
                parse_ms=400,
                outcome=message_trace.OUTCOME_ERROR,
                outcome_detail="işlenemedi",
            ),
        ]
    )
    await session.flush()

    return {
        "ahmet": ahmet,
        "mehmet": mehmet,
        "canli_borc": canli_borc,
        "silinen_tx_id": silinecek.id,
        "arsivli_kisi": arsivli_kisi,
        "batch_id": batch_id,
        "basarisiz_id": kayitlar[1].id,
    }


# ---------------------------------------------------------------- LLM izleme

async def test_llm_monitor_yalnizca_llm_satirlarini_doner(client, admin_password, akis, panel_verisi):
    """akis fixture'ı regex kaynaklı satırlar ekliyor; onlar sızmamalı."""
    await _login(client)
    body = (await client.get("/api/admin/llm-monitor")).json()

    assert body["total"] == 2
    assert all(True for _ in body["items"])  # parse_source alanı yanıtta yok, dolaylı doğrulama:
    metinler = {i["text"] for i in body["items"]}
    assert metinler == {"ahmete borç yaz serbest cümle", "çok garip bir cümle"}


async def test_llm_monitor_istatistik(client, admin_password, panel_verisi):
    await _login(client)
    body = (await client.get("/api/admin/llm-monitor")).json()

    stats = body["stats"]
    assert stats["total_calls"] == 2
    assert stats["avg_parse_ms"] == pytest.approx(325.0)
    assert stats["success_count"] == 1
    assert stats["failure_count"] == 1
    assert stats["success_rate"] == pytest.approx(0.5)


async def test_llm_monitor_sonuc_filtresi(client, admin_password, panel_verisi):
    await _login(client)
    body = (await client.get("/api/admin/llm-monitor?outcome=hata")).json()

    assert body["total"] == 1
    assert body["items"][0]["outcome"] == "hata"
    assert body["stats"]["total_calls"] == 1  # istatistik de filtreye uyar


async def test_llm_monitor_gecersiz_sonuc_422(client, admin_password, panel_verisi):
    await _login(client)
    r = await client.get("/api/admin/llm-monitor?outcome=olmayan")
    assert r.status_code == 422


# ---------------------------------------------------------------- istek kuyruğu

async def test_queue_sayilar_ve_liste(client, admin_password, panel_verisi):
    await _login(client)
    body = (await client.get("/api/admin/queue")).json()

    assert body["total"] == 2
    assert body["counts"]["beklemede"] == 1
    assert body["counts"]["basarisiz"] == 1
    assert body["counts"]["tamamlandi"] == 0


async def test_queue_durum_filtresi(client, admin_password, panel_verisi):
    await _login(client)
    body = (await client.get("/api/admin/queue?durum=basarisiz")).json()

    assert body["total"] == 1
    assert body["items"][0]["hata"] == "anlaşılamadı"
    # Sayılar filtreden bağımsız, kuyruğun tamamını yansıtır.
    assert body["counts"]["beklemede"] == 1


async def test_queue_gecersiz_durum_422(client, admin_password, panel_verisi):
    await _login(client)
    r = await client.get("/api/admin/queue?durum=olmayan")
    assert r.status_code == 422


# ---------------------------------------------------------------- kişiler & işlemler

async def test_admin_persons_yalnizca_aktif_kisiler(client, admin_password, panel_verisi):
    """Mehmet arşivlendiği (is_active=false) için listede görünmemeli."""
    await _login(client)
    body = (await client.get("/api/admin/persons")).json()

    isimler = {p["full_name"] for p in body["items"]}
    assert "Ahmet Yılmaz" in isimler
    assert "Mehmet Kaya" not in isimler


async def test_admin_person_transactions_arsivlenen_haric(client, admin_password, panel_verisi):
    """Ahmet'in arşive taşınan (silinen) borcu canlı listede çıkmamalı,
    kalan borç + tahsilat çıkmalı."""
    await _login(client)
    ahmet_id = panel_verisi["ahmet"].id
    body = (await client.get(f"/api/admin/persons/{ahmet_id}/transactions")).json()

    assert body["person_name"] == "Ahmet Yılmaz"
    assert body["total"] == 2
    ids = {t["id"] for t in body["items"]}
    assert panel_verisi["silinen_tx_id"] not in ids
    assert panel_verisi["canli_borc"].id in ids

    borc = next(t for t in body["items"] if t["kind"] == "DEBIT")
    assert borc["lines"][0]["product_name"] == "Saman"
    assert borc["lines"][0]["unit_price"] is not None


async def test_admin_person_transactions_olmayan_kisi_404(client, admin_password, panel_verisi):
    await _login(client)
    r = await client.get("/api/admin/persons/999999/transactions")
    assert r.status_code == 404


async def test_archived_persons_listesi(client, admin_password, panel_verisi):
    await _login(client)
    body = (await client.get("/api/admin/archived-persons")).json()

    assert body["total"] == 1
    row = body["items"][0]
    assert row["full_name"] == "Mehmet Kaya"
    assert row["archived_by"] == "admin-panel@test"
    assert row["archive_reason"] == "mükerrer kayıt"
    assert Decimal(row["balance_try"]) == Decimal("225.00")  # 3 balya * 75


async def test_archived_transactions_kisiye_daralir(client, admin_password, panel_verisi):
    await _login(client)
    ahmet_id = panel_verisi["ahmet"].id

    tumu = (await client.get("/api/admin/archived-transactions")).json()
    assert tumu["total"] == 1

    daralan = (await client.get(f"/api/admin/archived-transactions?person_id={ahmet_id}")).json()
    assert daralan["total"] == 1
    assert daralan["items"][0]["id"] == panel_verisi["silinen_tx_id"]

    bos = (await client.get("/api/admin/archived-transactions?person_id=999999")).json()
    assert bos["total"] == 0


# ---------------------------------------------------------------- loglar

async def test_audit_log_arsivleme_kayitlarini_icerir(client, admin_password, panel_verisi):
    await _login(client)
    body = (await client.get("/api/admin/audit-log?entity=transactions&action=archive_transaction")).json()

    assert body["total"] == 1
    row = body["items"][0]
    assert row["entity_id"] == str(panel_verisi["silinen_tx_id"])
    assert row["actor"] == "admin-panel@test"
    assert row["before"]["person_id"] == panel_verisi["ahmet"].id


async def test_audit_log_actor_filtresi(client, admin_password, panel_verisi):
    await _login(client)
    body = (await client.get("/api/admin/audit-log?actor=admin-panel@test")).json()

    actions = {r["action"] for r in body["items"]}
    assert "archive_transaction" in actions
    assert "archive_person" in actions
