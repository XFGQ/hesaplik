"""Admin panelinin salt okunur bölümleri: LLM İzleme, İstek Kuyruğu,
Kişiler & İşlemler, Loglar.

Ortak kural (test_admin_api.py ile aynı): **şifresiz hiçbir veri dışarı
çıkmaz.** Bu uçlar kişi adı, tutar, ham müşteri mesajı ve denetim kaydı
taşır; korumasız kalırlarsa defter sızar.

Bölüme özel kilitlenen davranışlar:

- LLM İzleme: yalnızca parse_source='llm' satırları listelenir (regex
  karışmaz), istatistik `outcome` filtresinden ETKİLENMEZ, hiç çağrı yokken
  oran 0.0 değil null olur ("veri yok" ile "hepsi başarısız" karışmasın).
- İstek Kuyruğu: durum sayımları durum filtresinden etkilenmez, uzun
  süredir ilerlemeyen istek `stale` işaretlenir.
- Kişiler & İşlemler: SALT OKUNUR (yazan uç yok), özet sayfaya değil
  filtrenin tamamına bakar, arşiv ayrı listede görünür.
- Loglar: audit_log'tan gelir, filtre listeleri veride geçen değerlerdir.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import QUEUE_STALE_MINUTES, _failures, router
from app.config import settings
from app.db import get_session
from app.models import (
    AuditLog,
    PendingRequest,
    Person,
    Product,
    RawMessage,
    TxSource,
)
from app.services import ledger, message_trace, person_archive, request_queue

SIFRE = "cok-gizli-parola"


@pytest.fixture
def admin_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", SIFRE)
    _failures.clear()
    return SIFRE


@pytest_asyncio.fixture(loop_scope="session")
async def client(session):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client) -> None:
    r = await client.post("/api/admin/login", json={"password": SIFRE})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- koruma

BOLUM_UCLARI = (
    "/api/admin/llm-monitor",
    "/api/admin/queue",
    "/api/admin/data/persons",
    "/api/admin/data/transactions",
    "/api/admin/data/archived-persons",
    "/api/admin/data/archived-transactions",
    "/api/admin/logs",
)


@pytest.mark.parametrize("yol", BOLUM_UCLARI)
async def test_bolumler_sifresiz_erisilemez(client, admin_password, yol):
    r = await client.get(yol)
    assert r.status_code == 401
    assert "items" not in r.text  # tek satır bile sızmadı


# ---------------------------------------------------------------- LLM İzleme

@pytest_asyncio.fixture(loop_scope="session")
async def llm_akisi(session):
    """İki LLM çağrısı (biri kaydedildi, biri anlaşılamadı) + bir regex
    mesajı: regex satırı LLM listesine KARIŞMAMALI."""
    dun = datetime.now(timezone.utc) - timedelta(days=1)
    session.add_all(
        [
            RawMessage(
                channel="telegram",
                external_id="9001",
                chat_id="42",
                payload={"message": {"chat": {"id": 42}, "text": "mehmetten 5 bin aldım"}},
                detected_kind=message_trace.KIND_PAYMENT,
                detected_person="Mehmet Kaya",
                detected_amount=Decimal("5000.00"),
                parse_source=message_trace.SOURCE_LLM,
                parse_ms=8000,
                outcome=message_trace.OUTCOME_RECORDED,
                outcome_detail="deftere yazıldı · niyet: payment",
            ),
            RawMessage(
                channel="telegram",
                external_id="9002",
                chat_id="42",
                payload={"message": {"chat": {"id": 42}, "text": "hava nasıl olacak"}},
                detected_kind=message_trace.KIND_NONE,
                parse_source=message_trace.SOURCE_LLM,
                parse_ms=12000,
                outcome=message_trace.OUTCOME_IGNORED,
                outcome_detail="anlaşılamadı",
            ),
            RawMessage(
                channel="telegram",
                external_id="9003",
                chat_id="42",
                payload={"message": {"chat": {"id": 42}, "text": "ahmet bakiye"}},
                received_at=dun,
                detected_kind=message_trace.KIND_QUERY,
                parse_source=message_trace.SOURCE_REGEX,
                parse_ms=4,
                outcome=message_trace.OUTCOME_ANSWERED,
            ),
        ]
    )
    await session.flush()
    return {"dun": dun}


async def test_llm_monitor_yalnizca_llm_satirlari(client, admin_password, llm_akisi):
    await _login(client)
    body = (await client.get("/api/admin/llm-monitor")).json()

    assert body["total"] == 2
    assert {r["parse_ms"] for r in body["items"]} == {8000, 12000}
    # Regex'in çözdüğü mesaj listede yok, ama kıyas için sayımda var.
    assert "ahmet bakiye" not in [r["text"] for r in body["items"]]
    assert body["stats"]["regex_total"] == 1


async def test_llm_monitor_istatistik(client, admin_password, llm_akisi):
    await _login(client)
    s = (await client.get("/api/admin/llm-monitor")).json()["stats"]

    assert s["llm_total"] == 2
    assert s["avg_ms"] == 10000 and s["max_ms"] == 12000
    assert s["regex_avg_ms"] == 4
    assert s["basarili"] == 1 and s["basarisiz"] == 1 and s["soruldu"] == 0
    assert s["basari_orani"] == 0.5
    assert s["llm_share"] == round(2 / 3, 3)
    assert s["llm_primary"] == "auto"  # ayarlanmamışsa güvenli varsayılan


async def test_llm_monitor_istatistik_sonuc_filtresinden_etkilenmez(
    client, admin_password, llm_akisi
):
    """Liste daralır, oran aynı kalır: filtre değiştikçe "başarı oranı"
    oynarsa panel yanıltır."""
    await _login(client)
    body = (await client.get("/api/admin/llm-monitor?outcome=hata")).json()

    assert body["total"] == 0 and body["items"] == []
    assert body["stats"]["llm_total"] == 2
    assert body["stats"]["basari_orani"] == 0.5


async def test_llm_monitor_hic_cagri_yoksa_oran_null(client, admin_password):
    """Payda 0: oran 0.0 DEĞİL null — "hiç çağrı yok" ile "hepsi başarısız"
    aynı görünmesin."""
    await _login(client)
    s = (await client.get("/api/admin/llm-monitor")).json()["stats"]

    assert s["llm_total"] == 0
    assert s["basari_orani"] is None
    assert s["llm_share"] is None
    assert s["avg_ms"] is None


async def test_llm_monitor_tarih_filtresi_ve_sayfalama(client, admin_password, llm_akisi):
    await _login(client)
    bugun = datetime.now(timezone.utc).date().isoformat()

    body = (await client.get(f"/api/admin/llm-monitor?from={bugun}")).json()
    assert body["stats"]["regex_total"] == 0  # dünkü regex mesajı aralık dışı

    ilk = (await client.get("/api/admin/llm-monitor?limit=1&offset=0")).json()
    ikinci = (await client.get("/api/admin/llm-monitor?limit=1&offset=1")).json()
    assert ilk["total"] == 2 and len(ilk["items"]) == 1
    assert ilk["items"][0]["id"] != ikinci["items"][0]["id"]


async def test_llm_monitor_gecersiz_sonuc_422(client, admin_password):
    await _login(client)
    assert (await client.get("/api/admin/llm-monitor?outcome=olmayan")).status_code == 422


# ---------------------------------------------------------------- İstek Kuyruğu

@pytest_asyncio.fixture(loop_scope="session")
async def kuyruk(session):
    """Üç istekli bir batch: biri tamamlandı, biri uzun süredir 'işleniyor'
    (takılmış), biri beklemede."""
    batch_id, kayitlar = await request_queue.create_batch(
        session,
        chat_id="42",
        texts=["mehmetten 5000 aldım", "aliye 500 mal gitti", "ahmet bakiye"],
    )
    await request_queue.mark(session, kayitlar[0].id, request_queue.TAMAMLANDI, sonuc="kaydedildi")

    eski = datetime.now(timezone.utc) - timedelta(minutes=QUEUE_STALE_MINUTES + 5)
    kayitlar[1].durum = request_queue.ISLENIYOR
    kayitlar[1].updated_at = eski
    await session.flush()

    session.add(
        PendingRequest(
            chat_id="99",
            batch_id="baska-batch",
            raw_text="anlaşılmayan cümle",
            sira_no=1,
            durum=request_queue.BASARISIZ,
            hata="anlaşılamadı",
        )
    )
    await session.flush()
    return {"batch_id": batch_id, "kayitlar": kayitlar}


async def test_kuyruk_listeler(client, admin_password, kuyruk):
    await _login(client)
    body = (await client.get("/api/admin/queue")).json()

    assert body["total"] == 4
    assert body["durum_sayilari"] == {
        "beklemede": 1,
        "isleniyor": 1,
        "tamamlandi": 1,
        "basarisiz": 1,
        "iptal": 0,
    }
    assert body["acik"] == 2


async def test_kuyruk_takilan_istek_isaretlenir(client, admin_password, kuyruk):
    await _login(client)
    body = (await client.get("/api/admin/queue")).json()

    assert body["takilan"] == 1
    assert body["stale_minutes"] == QUEUE_STALE_MINUTES
    takilanlar = [r for r in body["items"] if r["stale"]]
    assert len(takilanlar) == 1
    assert takilanlar[0]["durum"] == "isleniyor"
    assert takilanlar[0]["raw_text"] == "aliye 500 mal gitti"


async def test_kuyruk_durum_filtresi_sayimlari_bozmaz(client, admin_password, kuyruk):
    await _login(client)
    body = (await client.get("/api/admin/queue?durum=basarisiz")).json()

    assert body["total"] == 1
    assert body["items"][0]["hata"] == "anlaşılamadı"
    # Sekmeler arası geçişte sayılar sabit kalsın.
    assert body["durum_sayilari"]["tamamlandi"] == 1


async def test_kuyruk_chat_filtresi(client, admin_password, kuyruk):
    await _login(client)
    body = (await client.get("/api/admin/queue?chat_id=99")).json()

    assert body["total"] == 1
    assert body["durum_sayilari"]["beklemede"] == 0  # filtre sayımlara da uygulanır


async def test_kuyruk_gecersiz_durum_422(client, admin_password):
    await _login(client)
    assert (await client.get("/api/admin/queue?durum=olmayan")).status_code == 422


# ---------------------------------------------------------------- Kişiler & İşlemler

@pytest_asyncio.fixture(loop_scope="session")
async def defter(session):
    """İki kişi: biri borçlu (10.000), biri fazla ödemiş (−2.000)."""
    saman = Product(name="Saman", base_unit="balya")
    ahmet = Person(full_name="Ahmet Yılmaz", district="Bergama", phone="5551112233")
    veli = Person(full_name="Veli Kaya", district="Ahmetbeyler")
    session.add_all([saman, ahmet, veli])
    await session.flush()

    meta = ledger.TxMeta(created_by="test", source=TxSource.WEB)
    await ledger.add_debt(
        session,
        person_id=ahmet.id,
        lines=[
            ledger.LineInput(
                product_id=saman.id,
                qty=Decimal("20"),
                unit="balya",
                line_total=Decimal("10000.00"),
            )
        ],
        meta=meta,
    )
    await ledger.add_payment(session, person_id=veli.id, amount=Decimal("2000.00"), meta=meta)
    await session.flush()
    return {"ahmet": ahmet, "veli": veli}


async def test_data_persons_bakiye_ve_ozet(client, admin_password, defter):
    await _login(client)
    body = (await client.get("/api/admin/data/persons")).json()

    assert body["kisi_sayisi"] == 2
    assert body["toplam_alacak"] == "10000.00"
    assert body["toplam_borc"] == "2000.00"
    assert body["net"] == "8000.00"

    # En çok borçlu üstte (varsayılan sıralama).
    ilk = body["items"][0]
    assert ilk["full_name"] == "Ahmet Yılmaz"
    assert ilk["balance_try"] == "10000.00"
    assert ilk["district"] == "Bergama"
    assert ilk["items"][0]["product_name"] == "Saman"


async def test_data_persons_ozet_sayfadan_bagimsiz(client, admin_password, defter):
    """2. sayfadayken toplamlar değişmemeli — özet filtrenin tamamına bakar."""
    await _login(client)
    body = (await client.get("/api/admin/data/persons?limit=1&offset=1")).json()

    assert len(body["items"]) == 1
    assert body["total"] == 2
    assert body["toplam_alacak"] == "10000.00"


async def test_data_persons_filtreler(client, admin_password, defter):
    await _login(client)

    borclu = (await client.get("/api/admin/data/persons?scope=debtors")).json()
    assert [r["full_name"] for r in borclu["items"]] == ["Ahmet Yılmaz"]

    ilce = (await client.get("/api/admin/data/persons?district=bergama")).json()
    assert [r["full_name"] for r in ilce["items"]] == ["Ahmet Yılmaz"]

    arama = (await client.get("/api/admin/data/persons?q=veli")).json()
    assert [r["full_name"] for r in arama["items"]] == ["Veli Kaya"]

    assert (await client.get("/api/admin/data/persons?scope=olmayan")).status_code == 422
    assert (await client.get("/api/admin/data/persons?order=olmayan")).status_code == 422


async def test_data_transactions_kalemleriyle_doner(client, admin_password, defter):
    await _login(client)
    body = (await client.get("/api/admin/data/transactions")).json()

    assert body["total"] == 2
    assert body["toplam_borc"] == "10000.00"
    assert body["toplam_tahsilat"] == "2000.00"

    borc = next(r for r in body["items"] if r["kind"] == "DEBIT")
    assert borc["person_name"] == "Ahmet Yılmaz"
    assert borc["lines"][0]["product_name"] == "Saman"
    assert borc["lines"][0]["qty"] == "20.000"
    assert borc["lines"][0]["unit_price"] == "500.00"  # 10.000 / 20, tutardan türetilir


async def test_data_transactions_filtreler(client, admin_password, defter):
    await _login(client)

    kisi = (await client.get(f"/api/admin/data/transactions?person_id={defter['veli'].id}")).json()
    assert kisi["total"] == 1 and kisi["items"][0]["kind"] == "CREDIT"

    tur = (await client.get("/api/admin/data/transactions?kind=DEBIT")).json()
    assert tur["total"] == 1 and tur["items"][0]["person_name"] == "Ahmet Yılmaz"

    ad = (await client.get("/api/admin/data/transactions?person=yılmaz")).json()
    assert ad["total"] == 1

    assert (await client.get("/api/admin/data/transactions?kind=olmayan")).status_code == 422


async def test_arsiv_silinen_kayit_gorunur(client, admin_password, defter, session):
    """Defterden silinen kayıt canlı listeden düşer, arşiv listesinde
    kalemleriyle birlikte durur."""
    await _login(client)
    tx_id = (await client.get("/api/admin/data/transactions?kind=DEBIT")).json()["items"][0]["id"]

    await ledger.archive_transaction(session, tx_id, actor="admin-panel", reason="test")
    await session.flush()

    canli = (await client.get("/api/admin/data/transactions")).json()
    assert tx_id not in [r["id"] for r in canli["items"]]

    arsiv = (await client.get("/api/admin/data/archived-transactions")).json()
    assert arsiv["total"] == 1
    kayit = arsiv["items"][0]
    assert kayit["id"] == tx_id
    assert kayit["person_name"] == "Ahmet Yılmaz"
    assert kayit["archived_by"] == "admin-panel"
    # Kalem ürün adı canlı ürün tablosundan çözülür (arşivde product_id durur).
    assert kayit["lines"][0]["product_name"] == "Saman"


async def test_arsiv_silinen_kisi_gorunur(client, admin_password, defter, session):
    await _login(client)
    await person_archive.archive_person(
        session, defter["veli"].id, archived_by="admin-panel", reason="test"
    )
    await session.flush()

    body = (await client.get("/api/admin/data/archived-persons")).json()
    assert body["total"] == 1
    kayit = body["items"][0]
    assert kayit["full_name"] == "Veli Kaya"
    assert kayit["balance_try"] == "-2000.00"
    assert kayit["islem_sayisi"] == 1
    assert len(kayit["transactions_snapshot"]) == 1

    # Arşivlenen kişi canlı listeden düşer.
    canli = (await client.get("/api/admin/data/persons")).json()
    assert "Veli Kaya" not in [r["full_name"] for r in canli["items"]]


async def test_data_uclari_salt_okunur(client, admin_password, defter):
    """Panelin bu bölümü hiçbir şey yazmaz/silmez: yazan yöntemler kapalı."""
    await _login(client)
    for yol in ("/api/admin/data/persons", "/api/admin/data/transactions"):
        assert (await client.post(yol, json={})).status_code == 405
        assert (await client.delete(yol)).status_code == 405


# ---------------------------------------------------------------- Loglar

@pytest_asyncio.fixture(loop_scope="session")
async def denetim(session):
    dun = datetime.now(timezone.utc) - timedelta(days=1)
    session.add_all(
        [
            AuditLog(
                actor="admin-panel@127.0.0.1",
                action="restore_request",
                entity="restore_requests",
                entity_id="1",
                after={"snapshot_id": "abc123"},
                at=dun,
            ),
            AuditLog(
                actor="telegram:42",
                action="archive_person",
                entity="persons",
                entity_id="7",
                before={"full_name": "Veli Kaya", "balance_try": "-2000.00"},
                after={"is_active": False},
            ),
        ]
    )
    await session.flush()
    return {"dun": dun}


async def test_loglar_zaman_sirali(client, admin_password, denetim):
    await _login(client)
    body = (await client.get("/api/admin/logs")).json()

    assert body["total"] == 2
    assert body["items"][0]["action"] == "archive_person"  # en yeni üstte
    assert body["items"][0]["before"]["full_name"] == "Veli Kaya"
    assert body["items"][0]["after"] == {"is_active": False}


async def test_loglar_filtre_listeleri_veriden_gelir(client, admin_password, denetim):
    await _login(client)
    body = (await client.get("/api/admin/logs")).json()

    assert body["actions"] == ["archive_person", "restore_request"]
    assert body["entities"] == ["persons", "restore_requests"]


async def test_loglar_filtreler(client, admin_password, denetim):
    await _login(client)

    eylem = (await client.get("/api/admin/logs?action=restore_request")).json()
    assert eylem["total"] == 1 and eylem["items"][0]["entity_id"] == "1"

    aktor = (await client.get("/api/admin/logs?actor=telegram")).json()
    assert aktor["total"] == 1 and aktor["items"][0]["action"] == "archive_person"

    varlik = (await client.get("/api/admin/logs?entity=persons")).json()
    assert varlik["total"] == 1

    bugun = datetime.now(timezone.utc).date().isoformat()
    bugunku = (await client.get(f"/api/admin/logs?from={bugun}")).json()
    assert bugunku["total"] == 1  # dünkü restore kaydı düştü


async def test_loglar_sayfalama(client, admin_password, denetim):
    await _login(client)
    ilk = (await client.get("/api/admin/logs?limit=1&offset=0")).json()
    ikinci = (await client.get("/api/admin/logs?limit=1&offset=1")).json()

    assert ilk["total"] == 2 and len(ilk["items"]) == 1
    assert ilk["items"][0]["id"] != ikinci["items"][0]["id"]
