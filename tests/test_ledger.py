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


# ---------------------------------------------------------- kullanıcı tutarı

async def test_kullanici_tutari_fiyat_listesini_yener(session, ahmet, saman):
    """Pazarlık gerçeği: 20 balya listede 1500 TL ama 1200'e anlaşıldı."""
    tx = await ledger.add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal(20), line_total=Decimal("1200.00"))],
        meta(),
    )
    assert tx.amount_try == Decimal("1200.00")
    assert tx.lines[0].unit_price == Decimal("60.00")  # 1200/20, gösterim için türetildi
    assert tx.lines[0].unit == "balya"  # üründen alındı


async def test_bolunmeyen_tutar_kurusa_yuvarlanir(session, ahmet, saman):
    tx = await ledger.add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal(3), line_total=Decimal("1000.00"))],
        meta(),
    )
    assert tx.amount_try == Decimal("1000.00")          # tutar bozulmadı
    assert tx.lines[0].unit_price == Decimal("333.33")  # birim fiyat türev, yuvarlandı


async def test_fiyatsiz_urun_tutar_verilirse_calisir(session, ahmet):
    """Yeni ürün, fiyat listesi yok. Kullanıcı tutarı yazdıysa kayıt açılmalı."""
    from app.models import Product

    kepek = Product(name="Kepek", base_unit="çuval")
    session.add(kepek)
    await session.flush()

    tx = await ledger.add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=kepek.id, qty=Decimal(5), line_total=Decimal("850.00"))],
        meta(),
    )
    assert tx.amount_try == Decimal("850.00")
    assert tx.lines[0].unit == "çuval"


# ---------------------------------------------------- mal sayacı (tahsilatta ürün)

async def test_aldigindan_fazlasinin_parasini_veren_mal_alacaklisi_olur(session, ahmet, saman):
    """30 balya aldı, 50 balyanın parasını verdi → 20 balya alacaklı."""
    await ledger.add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal(30), line_total=Decimal("2250.00"))],
        meta(),
    )
    await ledger.add_payment(
        session,
        ahmet.id,
        Decimal("3750.00"),
        meta(),
        lines=[LineInput(product_id=saman.id, qty=Decimal(50), line_total=Decimal("3750.00"))],
    )

    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("-1500.00")               # para: alacaklı
    assert ("Saman", Decimal("-20.000"), "balya") in bal.items  # mal: 20 balya alacaklı


async def test_urunsuz_tahsilat_mal_sayacina_dokunmaz(session, ahmet, saman):
    """Nakit ödeme malı azaltmaz; iki defter ayrıdır."""
    await ledger.add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal(30), line_total=Decimal("2250.00"))],
        meta(),
    )
    await ledger.add_payment(session, ahmet.id, Decimal("1000.00"), meta())

    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("1250.00")
    assert ("Saman", Decimal("30.000"), "balya") in bal.items


async def test_tahsilat_kalem_toplami_tutara_esit_olmali(session, ahmet, saman):
    with pytest.raises(LedgerError, match="eşit değil"):
        await ledger.add_payment(
            session,
            ahmet.id,
            Decimal("1000.00"),
            meta(),
            lines=[LineInput(product_id=saman.id, qty=Decimal(10), line_total=Decimal("900.00"))],
        )


# ---------------------------------------------------------- kalemli tahsilat

async def test_kalemsiz_tahsilat_mal_miktarini_degistirmez(session, ahmet, saman):
    """Borcu varken düz para verdi: bakiye düşer, saman borcu durur."""
    await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(30))], meta()
    )
    await ledger.add_payment(session, ahmet.id, Decimal("1000.00"), meta())

    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("1250.00")  # 30*75 - 1000
    assert ("Saman", Decimal("30.000"), "balya") in bal.items  # mal borcu duruyor


async def test_fazla_odeme_mal_borcunu_ters_cevirir(session, ahmet, saman):
    """30 balya borcu var, 50 balyalık ödeme yaptı: artık biz 20 balya borçluyuz."""
    await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(30))], meta()
    )
    await ledger.add_payment(
        session,
        ahmet.id,
        Decimal("3750.00"),
        meta(),
        lines=[LineInput(product_id=saman.id, qty=Decimal(50), line_total=Decimal("3750.00"))],
    )

    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("-1500.00")           # 2250 - 3750
    assert bal.items == [("Saman", Decimal("-20.000"), "balya")]  # 30 - 50, biz borçluyuz
    assert bal.is_receivable is False


async def test_tam_kapanan_hesap_kalem_birakmaz(session, ahmet, saman):
    await ledger.add_debt(
        session, ahmet.id, [LineInput(product_id=saman.id, qty=Decimal(30))], meta()
    )
    await ledger.add_payment(
        session,
        ahmet.id,
        Decimal("2250.00"),
        meta(),
        lines=[LineInput(product_id=saman.id, qty=Decimal(30), line_total=Decimal("2250.00"))],
    )
    bal = await ledger.balance_of(session, ahmet.id)
    assert bal.balance_try == Decimal("0.00")
    assert bal.items == []  # sıfırlanan kalem listede görünmez
