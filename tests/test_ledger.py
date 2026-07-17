import random
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models import Person, PriceHistory, Product, TxKind, TxSource, TxStatus
from app.services import ledger
from app.services.ledger import LedgerError, LineInput, TxMeta


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    p = Person(full_name="Ahmet Yılmaz")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def saman(session):
    pr = Product(name="Saman", base_unit="balya")
    session.add(pr)
    await session.flush()
    session.add(
        PriceHistory(product_id=pr.id, unit_price=Decimal("75.00"), valid_from=date(2026, 1, 1))
    )
    await session.flush()
    return pr


def meta(**kw):
    return TxMeta(created_by="test", **kw)


async def test_borc_ekleme_tutari_kalemlerden_hesaplar(session, ahmet, saman):
    tx = await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(20))], meta()
    )
    assert tx.amount_try == Decimal("1500.00")  # 20 * 75
    assert tx.kind is TxKind.DEBIT
    assert tx.lines[0].unit == "balya"


async def test_bakiye_borc_eksi_tahsilat(session, ahmet, saman):
    await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(20))], meta()
    )
    await ledger.add_payment(session, ahmet.id, Decimal("500.00"), meta())

    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("1000.00")
    assert bal.is_receivable is True
    assert ("Saman", Decimal("20.000"), "balya") in bal.items


async def test_ters_kayit_bakiyeyi_sifirlar(session, ahmet, saman):
    tx = await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(20))], meta()
    )
    contra = await ledger.reverse(session, tx.id, actor="furkan", reason="yanlış kişi")

    assert contra.kind is TxKind.CREDIT
    assert contra.reverses_id == tx.id

    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("0.00")
    assert bal.items == []  # kalem de sıfırlanır


async def test_ayni_kayit_iki_kez_ters_kaydedilemez(session, ahmet, saman):
    tx = await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(5))], meta()
    )
    await ledger.reverse(session, tx.id, actor="furkan", reason="hata")
    with pytest.raises(LedgerError, match="zaten iptal"):
        await ledger.reverse(session, tx.id, actor="furkan", reason="tekrar")


async def test_transactions_update_edilemez(session, ahmet, saman):
    """Append-only tetikleyicisi DB seviyesinde korur."""
    tx = await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(1))], meta()
    )
    await session.commit()
    with pytest.raises(Exception, match="append-only"):
        await session.execute(
            text("UPDATE transactions SET amount_try = 999 WHERE id = :i"), {"i": tx.id}
        )
        await session.commit()
    await session.rollback()


async def test_transactions_delete_edilemez(session, ahmet, saman):
    tx = await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(1))], meta()
    )
    await session.commit()
    with pytest.raises(Exception, match="append-only"):
        await session.execute(text("DELETE FROM transactions WHERE id = :i"), {"i": tx.id})
        await session.commit()
    await session.rollback()


async def test_pending_onaylanabilir(session, ahmet, saman):
    tx = await ledger.add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal(3))],
        meta(source=TxSource.TELEGRAM_VOICE, llm_confidence=Decimal("0.62")),
        status=TxStatus.PENDING,
    )
    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("0.00")  # PENDING bakiyeye girmez

    await ledger.confirm(session, tx.id, actor="ahmet")
    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("225.00")


async def test_fiyat_tarihe_gore_secilir(session, ahmet, saman):
    session.add(
        PriceHistory(product_id=saman.id, unit_price=Decimal("90.00"), valid_from=date(2026, 6, 1))
    )
    await session.flush()

    eski = await ledger.add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal(10))],
        meta(occurred_at=datetime(2026, 3, 1, tzinfo=timezone.utc)),
    )
    yeni = await ledger.add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal(10))],
        meta(occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc)),
    )
    assert eski.amount_try == Decimal("750.00")
    assert yeni.amount_try == Decimal("900.00")


async def test_negatif_ve_sifir_reddedilir(session, ahmet, saman):
    with pytest.raises(LedgerError):
        await ledger.add_payment(session, ahmet.id, Decimal("0"), meta())
    with pytest.raises(LedgerError):
        await ledger.add_debt(
            session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal("-1"))], meta()
        )


async def test_kurus_kaybi_yok_rastgele_yuz_islem(session, ahmet, saman):
    """En kritik test: float kullanılsaydı burası patlardı."""
    random.seed(1071)
    session.add(
        PriceHistory(product_id=saman.id, unit_price=Decimal("33.33"), valid_from=date(2026, 1, 2))
    )
    await session.flush()

    beklenen = Decimal("0.00")
    for _ in range(100):
        if random.random() < 0.6:
            qty = Decimal(random.randint(1, 40))
            tx = await ledger.add_debt(
                session, ahmet.id, [LineInput(product_id=saman.id, qty=qty)], meta()
            )
            beklenen += tx.amount_try
        else:
            amount = ledger.money(Decimal(random.randint(1, 5000)) / Decimal(3))
            await ledger.add_payment(session, ahmet.id, amount, meta())
            beklenen -= amount

    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == beklenen, "kuruş farkı: Decimal yerine float sızmış"
