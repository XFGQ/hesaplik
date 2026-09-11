"""Varsayılan saman balya fiyatı (CLAUDE.md > "Varsayılan saman fiyatı").

Kural 4'ün tek istisnası: SAMAN için tutar yazılmazsa tutar = adet ×
varsayılan fiyat. Kullanıcı tutar yazarsa her zaman o esastır; arpa vb.
hiçbir ürün etkilenmez. Burada: ayarın okunup yazılması (audit dahil), API
uçları ve deftere yazılan hesap. Sohbet/bot teyit akışları:
tests/test_chat_api.py > "varsayılan saman fiyatı", tests/test_bot_saman_fiyat.py.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.api.routes import router
from app.db import get_session
from app.models import AuditLog, Person, Product, RawMessage, Setting, Transaction, TxKind
from app.services import message_processor, saman_fiyat
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent
from app.services.message_processor import ProcessOutcome
from conftest import AUTH_USERNAME


@pytest_asyncio.fixture(loop_scope="session")
async def client(session):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    p = Person(full_name="Ahmet Yılmaz")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def saman(session):
    p = Product(name="Saman", base_unit="balya")
    session.add(p)
    await session.flush()
    return p


_update_id = iter(range(90_000, 99_999))


async def _process(session, text: str):
    raw = RawMessage(channel="telegram", external_id=str(next(_update_id)), chat_id="1", payload={"text": text})
    session.add(raw)
    await session.flush()
    return await message_processor.process_raw_message(session, raw, text)


async def _tx_count(session) -> int:
    return (await session.execute(select(func.count(Transaction.id)))).scalar_one()


async def _set_raw_value(session, value: str) -> None:
    await session.execute(
        update(Setting).where(Setting.key == saman_fiyat.SAMAN_FIYAT_KEY).values(value=value)
    )
    session.expire_all()


# ---------------------------------------------------------------- ayar oku/yaz


async def test_varsayilan_fiyat_180_tohumlanmis(session):
    assert await saman_fiyat.get_saman_price(session) == Decimal("180.00")


@pytest.mark.parametrize("bozuk", ["abc", "0", "-5", "NaN", ""])
async def test_bozuk_ayar_fiyat_saymaz(session, bozuk):
    """Bozuk bir fiyattan tutar HESAPLANMAZ — sistem eski davranışa düşer."""
    await _set_raw_value(session, bozuk)
    assert await saman_fiyat.get_saman_price(session) is None


async def test_fiyat_yazilir_ve_audit_loga_eski_yeni_duser(session):
    yeni = await saman_fiyat.set_saman_price(session, Decimal("200"), actor="furkan")
    assert yeni == Decimal("200.00")
    assert await saman_fiyat.get_saman_price(session) == Decimal("200.00")

    log = (
        await session.execute(select(AuditLog).where(AuditLog.action == "set_saman_price"))
    ).scalar_one()
    assert log.actor == "furkan"
    assert log.entity_id == saman_fiyat.SAMAN_FIYAT_KEY
    assert log.before == {"value": "180.00"}
    assert log.after == {"value": "200.00"}


@pytest.mark.parametrize("gecersiz", [Decimal("0"), Decimal("-5"), Decimal("2000000")])
async def test_gecersiz_fiyat_reddedilir(session, gecersiz):
    with pytest.raises(saman_fiyat.SamanFiyatError):
        await saman_fiyat.set_saman_price(session, gecersiz, actor="furkan")
    assert await saman_fiyat.get_saman_price(session) == Decimal("180.00")


@pytest.mark.parametrize(
    "metin,beklenen",
    [
        ("180", Decimal("180.00")),
        ("180,50", Decimal("180.50")),
        ("1.250", Decimal("1250.00")),
        ("abc", None),
        ("0", None),
        ("", None),
    ],
)
def test_fiyat_metni_turkce_cozulur(metin, beklenen):
    assert saman_fiyat.parse_price(metin) == beklenen


# ---------------------------------------------------------------- API


async def test_api_tokensiz_401(client, auth_account):
    assert (await client.get("/api/settings/saman-fiyat")).status_code == 401
    r = await client.post("/api/settings/saman-fiyat", json={"unit_price": "200"})
    assert r.status_code == 401


async def test_api_fiyat_okunur_ve_degistirilir(client, session, auth_headers):
    r = await client.get("/api/settings/saman-fiyat", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"unit_price": "180.00", "unit": "balya", "product": "saman"}

    r = await client.post("/api/settings/saman-fiyat", json={"unit_price": "210.5"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["unit_price"] == "210.50"
    assert (await client.get("/api/settings/saman-fiyat", headers=auth_headers)).json()["unit_price"] == "210.50"

    log = (
        await session.execute(select(AuditLog).where(AuditLog.action == "set_saman_price"))
    ).scalar_one()
    assert log.actor == AUTH_USERNAME


@pytest.mark.parametrize("gecersiz", ["0", "-1", "abc", "2000000"])
async def test_api_gecersiz_fiyat_422(client, auth_headers, gecersiz):
    r = await client.post("/api/settings/saman-fiyat", json={"unit_price": gecersiz}, headers=auth_headers)
    assert r.status_code == 422


async def test_genel_ayar_ucu_da_ayni_dogrulama_ve_audit(client, session, auth_headers):
    """PUT /settings/{key} saman fiyatını denetimsiz yazamaz."""
    r = await client.put("/api/settings/saman_birim_fiyat", json={"value": "250"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"key": "saman_birim_fiyat", "value": "250.00"}
    assert await saman_fiyat.get_saman_price(session) == Decimal("250.00")
    assert (
        await session.execute(select(func.count(AuditLog.id)).where(AuditLog.action == "set_saman_price"))
    ).scalar_one() == 1

    r = await client.put("/api/settings/saman_birim_fiyat", json={"value": "abc"}, headers=auth_headers)
    assert r.status_code == 422
    assert await saman_fiyat.get_saman_price(session) == Decimal("250.00")


# ---------------------------------------------------------------- otomatik hesap


@pytest.mark.parametrize(
    "metin,adet",
    [
        ("ahmet yılmaz 20 saman aldı", Decimal("20")),
        ("ahmet yılmaz 30 saman borç", Decimal("30")),
        ("ahmet yılmaz 20 balya saman verdim", Decimal("20")),
    ],
)
async def test_fiyatsiz_saman_varsayilan_fiyattan_kaydedilir(session, ahmet, saman, metin, adet):
    result = await _process(session, metin)

    assert result.outcome == ProcessOutcome.RECORDED
    assert result.resolved.default_unit_price == Decimal("180.00")
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.kind == TxKind.DEBIT
    assert tx.amount_try == adet * 180
    assert tx.lines[0].qty == adet
    assert tx.lines[0].unit == "balya"
    assert tx.lines[0].unit_price == Decimal("180.00")


async def test_fiyat_yazildiysa_kullanicinin_tutari_gecerli(session, ahmet, saman):
    result = await _process(session, "ahmet yılmaz 20 saman aldı 4000 tl")

    assert result.outcome == ProcessOutcome.RECORDED
    assert result.resolved.default_unit_price is None
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.amount_try == Decimal("4000.00")
    assert tx.lines[0].unit_price == Decimal("200.00")


async def test_fiyat_degisince_yeni_fiyattan_hesaplanir(session, ahmet, saman):
    await saman_fiyat.set_saman_price(session, Decimal("200"), actor="furkan")
    result = await _process(session, "ahmet yılmaz 20 saman aldı")

    tx = await session.get(Transaction, result.transaction_id)
    assert tx.amount_try == Decimal("4000.00")


async def test_katalogda_saman_yoksa_balya_birimiyle_acilir(session, ahmet):
    """Saman ürünü yoksa 'adet' birimiyle açılıp fiyat eşleşmesi bozulmasın."""
    result = await _process(session, "ahmet yılmaz 20 saman aldı")

    assert result.outcome == ProcessOutcome.RECORDED
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.amount_try == Decimal("3600.00")
    urun = (await session.execute(select(Product))).scalar_one()
    assert urun.base_unit == "balya"


@pytest.mark.parametrize(
    "metin",
    [
        "ahmet yılmaz 20 arpa aldı",        # arpanın varsayılan fiyatı yok
        "ahmet yılmaz 20 kilo saman aldı",  # fiyat balya başına, kilo ile çarpılmaz
    ],
)
async def test_sadece_balya_saman_etkilenir(session, ahmet, saman, metin):
    result = await _process(session, metin)

    assert result.outcome == ProcessOutcome.UNRECOGNIZED
    assert await _tx_count(session) == 0


async def test_bozuk_ayarda_tutar_uydurulmaz(session, ahmet, saman):
    await _set_raw_value(session, "abc")
    result = await _process(session, "ahmet yılmaz 20 saman aldı")

    assert result.outcome == ProcessOutcome.UNRECOGNIZED
    assert await _tx_count(session) == 0


# ---------------------------------------------------------------- belirsizlik teyidi


async def test_fiilsiz_saman_sorulur_kaydedilmez(session, ahmet, saman):
    """"ahmet yılmaz 20 saman": ürün+adet net, yön söylenmemiş -> sor."""
    result = await _process(session, "ahmet yılmaz 20 saman")

    assert result.outcome == ProcessOutcome.SAMAN_PRICE_CONFIRM
    assert result.resolved.amount == Decimal("3600.00")
    assert result.resolved.assumed_kind is True
    assert result.resolved.assumed_product is False
    assert await _tx_count(session) == 0


async def test_urunsuz_sayi_saman_mi_diye_sorulur(session, ahmet, saman):
    """"ahmet yılmaz 20": ürün belirsiz -> "20 saman borç mu?" diye sor."""
    result = await _process(session, "ahmet yılmaz 20")

    assert result.outcome == ProcessOutcome.SAMAN_PRICE_CONFIRM
    assert result.resolved.assumed_product is True
    assert result.resolved.product.id == saman.id
    assert result.resolved.amount == Decimal("3600.00")
    assert await _tx_count(session) == 0


async def test_tahsilat_varsayilan_fiyatla_sorulur(session, ahmet, saman):
    """Kişinin verdiği parayı biz bilemeyiz: tahsilatta hesap teklif edilir, sorulur."""
    result = await _process(session, "ahmet yılmazdan 20 saman aldım")

    assert result.outcome == ProcessOutcome.SAMAN_PRICE_CONFIRM
    assert result.resolved.kind == "payment"
    assert await _tx_count(session) == 0


async def test_llm_sonucu_varsayilan_fiyatla_sorulur(session, ahmet, saman):
    raw = RawMessage(channel="telegram", external_id="llm-1", chat_id="1", payload={"text": "x"})
    session.add(raw)
    await session.flush()
    resolved = ResolvedIntent(
        status=ResolutionStatus.READY, kind="debt", person=ahmet, qty=Decimal("20"), product=saman
    )

    result = await message_processor.handle_resolved(session, raw, resolved, "x", source="llm")

    assert result.outcome == ProcessOutcome.SAMAN_PRICE_CONFIRM
    assert result.resolved.amount == Decimal("3600.00")
    assert await _tx_count(session) == 0


# ---------------------------------------------------------------- koşan format


async def test_kosan_format_samanda_varsayilan_fiyattan_kaydedilir(session, ahmet, saman):
    result = await _process(session, "ahmet yılmaz 70-20-50 saman")

    assert result.outcome == ProcessOutcome.RECORDED
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.lines[0].qty == Decimal("20")  # FARK
    assert tx.amount_try == Decimal("3600.00")


async def test_kosan_format_arpada_tutar_hala_sorulur(session, ahmet, saman):
    result = await _process(session, "ahmet yılmaz 70-20-50 arpa")

    assert result.outcome == ProcessOutcome.RUNNING_AMOUNT_NEEDED
    assert await _tx_count(session) == 0
