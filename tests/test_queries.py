from decimal import Decimal

import pytest

from app.models import Person, Product, TxKind, TxSource
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


# --------------------------------------------------------------- search_persons (CLAUDE.md >
# "Bot sorgu anlama" Grup 1, madde 5: tek kelime arama)


async def test_search_persons_isimde_gecen_herkes(session):
    await _person(session, "Ahmet Yılmaz")
    await _person(session, "Ahmet Kaya")
    await _person(session, "Mehmet Duman")

    rows = await queries.search_persons(session, "ahmet")

    assert {r.person.full_name for r in rows} == {"Ahmet Yılmaz", "Ahmet Kaya"}


async def test_search_persons_soyadda_gecenler(session):
    await _person(session, "Furkan Duman")
    await _person(session, "Ahmet Yılmaz")

    rows = await queries.search_persons(session, "duman")

    assert [r.person.full_name for r in rows] == ["Furkan Duman"]


async def test_search_persons_ilcede_gecenler(session):
    await _person(session, "Bergamalı Ahmet", district="Bergama")
    await _person(session, "İzmirli Mehmet", district="İzmir")

    rows = await queries.search_persons(session, "bergama")

    assert [r.person.full_name for r in rows] == ["Bergamalı Ahmet"]


async def test_search_persons_eslesme_yoksa_bos_liste(session):
    await _person(session, "Ahmet Yılmaz")

    rows = await queries.search_persons(session, "zzz-yok")

    assert rows == []


async def test_search_persons_bos_terim_bos_liste(session):
    await _person(session, "Ahmet Yılmaz")

    assert await queries.search_persons(session, "") == []
    assert await queries.search_persons(session, "   ") == []


async def test_search_persons_bakiyeleriyle_doner(session):
    ahmet = await _person(session, "Ahmet Yılmaz")
    await add_debt(session, ahmet.id, [], meta(), amount_override=Decimal("1000"))

    rows = await queries.search_persons(session, "ahmet")

    assert rows[0].balance_try == Decimal("1000.00")


# --------------------------------------------------------------- list_person_transactions
# (CLAUDE.md > "Bot sorgu anlama" Grup 1, madde 2: bakiye tablo çıktısı)


async def test_list_person_transactions_kronolojik_sirali(session):
    kisi = await _person(session, "Kronoloji Kişi")
    await add_debt(session, kisi.id, [], meta(), amount_override=Decimal("1000"))
    await add_payment(session, kisi.id, Decimal("400"), meta())

    rows, total = await queries.list_person_transactions(session, kisi.id)

    assert total == 2
    assert [r.kind for r in rows] == [TxKind.DEBIT, TxKind.CREDIT]
    assert rows[0].amount_try == Decimal("1000.00")
    assert rows[1].amount_try == Decimal("400.00")


async def test_list_person_transactions_limit_ve_toplam(session):
    kisi = await _person(session, "Çok Hareketli Kişi")
    for i in range(5):
        await add_debt(session, kisi.id, [], meta(), amount_override=Decimal("100"))

    rows, total = await queries.list_person_transactions(session, kisi.id, limit=3)

    assert total == 5
    assert len(rows) == 3


async def test_list_person_transactions_kalemli_hareket(session):
    kisi = await _person(session, "Kalemli Kişi")
    saman = Product(name="Saman", base_unit="balya")
    session.add(saman)
    await session.flush()
    await add_debt(
        session, kisi.id, [LineInput(product_id=saman.id, qty=Decimal("20"), line_total=Decimal("1000"))], meta()
    )

    rows, _total = await queries.list_person_transactions(session, kisi.id)

    assert rows[0].lines == [("Saman", Decimal("20"), "balya")]


async def test_list_person_transactions_hareketsiz_kisi(session):
    kisi = await _person(session, "Hareketsiz Kişi")

    rows, total = await queries.list_person_transactions(session, kisi.id)

    assert rows == []
    assert total == 0


# --------------------------------------------------------------- toplam bakiye
# (CLAUDE.md > "Toplam bakiye niyeti"): defterin TAMAMININ özeti.


async def test_toplam_bakiye_bos_defterde_sifir(session):
    total = await queries.total_balance(session)
    assert total.kisi_sayisi == 0
    assert total.toplam_alacak == Decimal("0.00")
    assert total.toplam_borc == Decimal("0.00")
    assert total.net == Decimal("0.00")


async def test_toplam_bakiye_borclu_ve_alacaklilari_ayri_toplar(session):
    borclu = await _person(session, "Borçlu Kişi")
    alacakli = await _person(session, "Alacaklı Kişi")
    sifir = await _person(session, "Sıfır Kişi")

    await add_debt(session, borclu.id, [], meta(), amount_override=Decimal("1000"))
    await add_debt(session, alacakli.id, [], meta(), amount_override=Decimal("500"))
    await add_payment(session, alacakli.id, Decimal("700"), meta())
    await add_debt(session, sifir.id, [], meta(), amount_override=Decimal("300"))
    await add_payment(session, sifir.id, Decimal("300"), meta())

    total = await queries.total_balance(session)

    assert total.kisi_sayisi == 3
    assert total.borclu_sayisi == 1
    assert total.alacakli_sayisi == 1
    assert total.toplam_alacak == Decimal("1000.00")
    assert total.toplam_borc == Decimal("200.00")
    assert total.net == Decimal("800.00")


async def test_toplam_bakiye_net_negatif_olabilir(session):
    alacakli = await _person(session, "Peşin Ödeyen")
    await add_payment(session, alacakli.id, Decimal("2500"), meta())

    total = await queries.total_balance(session)

    assert total.toplam_alacak == Decimal("0.00")
    assert total.toplam_borc == Decimal("2500.00")
    assert total.net == Decimal("-2500.00")
