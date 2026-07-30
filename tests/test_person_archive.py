from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models import Person, PriceHistory, Product, TxSource
from app.services import ledger, person_archive, queries
from app.services.ledger import LineInput, TxMeta, add_debt, add_payment
from app.services.person_archive import PersonArchiveError


def meta(**kw):
    return TxMeta(created_by="test", source=TxSource.WEB, **kw)


async def _person(session, full_name="Ahmet Yılmaz", **kw):
    p = Person(full_name=full_name, **kw)
    session.add(p)
    await session.flush()
    return p


async def _saman(session):
    pr = Product(name="Saman", base_unit="balya")
    session.add(pr)
    await session.flush()
    session.add(
        PriceHistory(product_id=pr.id, unit_price=Decimal("75.00"), valid_from=date(2026, 1, 1))
    )
    await session.flush()
    return pr


async def test_arsivleme_kart_bakiye_ve_islemleri_snapshotlar(session):
    ahmet = await _person(session, "Ahmet Yılmaz", phone="5551112233", city="İzmir", district="Bergama")
    saman = await _saman(session)
    await add_debt(session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(20))], meta())
    await add_payment(session, ahmet.id, Decimal("500.00"), meta())

    archived = await person_archive.archive_person(
        session, ahmet.id, archived_by="furkan", reason="test arşivleme"
    )

    assert archived.original_person_id == ahmet.id
    assert archived.full_name == "Ahmet Yılmaz"
    assert archived.phone == "5551112233"
    assert archived.district == "Bergama"
    assert archived.archived_by == "furkan"
    assert archived.archive_reason == "test arşivleme"
    assert archived.balance_try == Decimal("1000.00")  # 20*75 - 500
    assert len(archived.transactions_snapshot) == 2
    kinds = {t["kind"] for t in archived.transactions_snapshot}
    assert kinds == {"DEBIT", "CREDIT"}
    debit = next(t for t in archived.transactions_snapshot if t["kind"] == "DEBIT")
    assert debit["lines"][0]["product_name"] == "Saman"
    assert Decimal(debit["lines"][0]["qty"]) == Decimal("20")


async def test_arsivlenen_kisi_is_active_false_olur(session):
    ahmet = await _person(session)
    await person_archive.archive_person(session, ahmet.id, archived_by="furkan", reason="r")

    await session.refresh(ahmet)
    assert ahmet.is_active is False


async def test_gercek_transactions_kayitlari_silinmez(session):
    ahmet = await _person(session)
    saman = await _saman(session)
    tx = await add_debt(session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(5))], meta())
    await session.commit()

    await person_archive.archive_person(session, ahmet.id, archived_by="furkan", reason="r")
    await session.commit()

    live = (
        await session.execute(text("SELECT id FROM transactions WHERE id = :i"), {"i": tx.id})
    ).first()
    assert live is not None


async def test_arsivlenen_kisi_listelemede_gorunmez(session):
    ahmet = await _person(session, "Ahmet Yılmaz")
    mehmet = await _person(session, "Mehmet Kaya")

    await person_archive.archive_person(session, ahmet.id, archived_by="furkan", reason="r")

    rows = await queries.list_persons_with_balance(session, scope="all")
    ids = [r.person.id for r in rows]
    assert ahmet.id not in ids
    assert mehmet.id in ids


async def test_snapshot_bakiye_arsiv_anindaki_bakiyeye_esit(session):
    ahmet = await _person(session)
    saman = await _saman(session)
    await add_debt(session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(10))], meta())

    bal_before = await ledger.balance_of(session, ahmet.id)
    archived = await person_archive.archive_person(session, ahmet.id, archived_by="furkan", reason="r")

    assert archived.balance_try == bal_before.balance_try


async def test_olmayan_kisi_arsivlenemez(session):
    with pytest.raises(PersonArchiveError, match="bulunamadı"):
        await person_archive.archive_person(session, 999999, archived_by="furkan", reason="r")


async def test_zaten_arsivlenen_kisi_tekrar_arsivlenemez(session):
    ahmet = await _person(session)
    await person_archive.archive_person(session, ahmet.id, archived_by="furkan", reason="r")

    with pytest.raises(PersonArchiveError, match="zaten arşivlenmiş"):
        await person_archive.archive_person(session, ahmet.id, archived_by="furkan", reason="r2")
