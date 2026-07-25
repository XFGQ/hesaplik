from decimal import Decimal

import pytest

from app.models import Person, Product, TxSource
from app.services import queries
from app.services.ledger import LineInput, TxMeta, add_debt, add_payment


def meta(**kw):
    return TxMeta(created_by="test", source=TxSource.WEB, **kw)


async def _person(session, full_name, district=None):
    p = Person(full_name=full_name, district=district)
    session.add(p)
    await session.flush()
    return p


async def test_bakiyesi_olmayan_kisi_de_listelenir_all(session):
    await _person(session, "Ahmet Yılmaz")
    rows = await queries.list_persons_with_balance(session, scope="all")
    assert len(rows) == 1
    assert rows[0].balance_try == Decimal("0.00")


async def test_borcluları_filtreler(session):
    borclu = await _person(session, "Borçlu Kişi")
    alacakli = await _person(session, "Alacaklı Kişi")
    await add_debt(session, borclu.id, [], meta(), amount_override=Decimal("1000"))
    await add_debt(session, alacakli.id, [], meta(), amount_override=Decimal("500"))
    await add_payment(session, alacakli.id, Decimal("700"), meta())

    rows = await queries.list_persons_with_balance(session, scope="debtors")

    assert [r.person.id for r in rows] == [borclu.id]
    assert rows[0].balance_try == Decimal("1000.00")


async def test_alacaklilari_filtreler(session):
    borclu = await _person(session, "Borçlu Kişi")
    alacakli = await _person(session, "Alacaklı Kişi")
    await add_debt(session, borclu.id, [], meta(), amount_override=Decimal("1000"))
    await add_debt(session, alacakli.id, [], meta(), amount_override=Decimal("500"))
    await add_payment(session, alacakli.id, Decimal("700"), meta())

    rows = await queries.list_persons_with_balance(session, scope="creditors")

    assert [r.person.id for r in rows] == [alacakli.id]
    assert rows[0].balance_try == Decimal("-200.00")


async def test_buyukten_kucuge_siralanir(session):
    az = await _person(session, "Az Borçlu")
    cok = await _person(session, "Çok Borçlu")
    await add_debt(session, az.id, [], meta(), amount_override=Decimal("100"))
    await add_debt(session, cok.id, [], meta(), amount_override=Decimal("5000"))

    rows = await queries.list_persons_with_balance(session, scope="all")

    assert [r.person.id for r in rows] == [cok.id, az.id]


async def test_ilce_filtresi_normalize_edilir(session):
    await _person(session, "Bergamalı Ahmet", district="Bergama")
    await _person(session, "İzmirli Mehmet", district="İzmir")

    rows = await queries.list_persons_with_balance(session, district="bergama")

    assert len(rows) == 1
    assert rows[0].person.full_name == "Bergamalı Ahmet"


async def test_ilce_filtresi_bos_ilceli_kisiyi_almaz(session):
    await _person(session, "İlçesiz Kişi")

    rows = await queries.list_persons_with_balance(session, district="bergama")

    assert rows == []


async def test_acik_kalemler_listeye_dahil(session):
    kisi = await _person(session, "Kalemli Kişi")
    saman = Product(name="Saman", base_unit="balya")
    session.add(saman)
    await session.flush()
    await add_debt(
        session, kisi.id, [LineInput(product_id=saman.id, qty=Decimal("20"), line_total=Decimal("1000"))], meta()
    )

    rows = await queries.list_persons_with_balance(session, scope="debtors")

    assert rows[0].items == [("Saman", Decimal("20"), "balya")]


async def test_gecersiz_filtre_hata_verir(session):
    with pytest.raises(ValueError):
        await queries.list_persons_with_balance(session, scope="unknown")
