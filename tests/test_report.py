"""PDF rapor testleri (CLAUDE.md > "Faz 4b — PDF Raporlar").

PDF'in görsel doğruluğunu test etmek yerine (o onaylandı, rapor_taslak.py'de
elle kontrol edildi) burada gerçek DB verisiyle üretilen PDF'in: (1) geçerli
bir PDF olduğunu (%PDF header), (2) içeriğin doğru olduğunu (yürüyen bakiye,
sıralama) doğruluyoruz. İçerik kontrolü için pdftotext (poppler-utils) ile
metne çevrilir; sistemde poppler-utils bulunmazsa bu testler atlanır.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from app.models import Person, Product, Setting
from app.services import report
from app.services.ledger import LineInput, TxMeta, add_debt, add_payment

pytestmark = pytest.mark.skipif(
    shutil.which("pdftotext") is None, reason="poppler-utils (pdftotext) yok"
)


def _pdf_to_text(pdf_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf_bytes)
        f.flush()
        result = subprocess.run(
            ["pdftotext", "-layout", f.name, "-"], capture_output=True, check=True
        )
    return result.stdout.decode("utf-8")


@pytest_asyncio.fixture(loop_scope="session")
async def saman(session):
    p = Product(name="Saman", base_unit="balya")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    p = Person(full_name="Ahmet Yılmaz", phone="0555 111 22 33", district="Bergama", city="İzmir")
    session.add(p)
    await session.flush()
    return p


# ---------------------------------------------------------------- isletme_adi

async def test_isletme_adi_ayarlanmamissa_varsayilan(session):
    # db/schema.sql varsayılan olarak business_name='Hesaplık' tohumlar.
    assert await report.isletme_adi(session) == "Hesaplık"


async def test_isletme_adi_settings_tablosundan_okunur(session):
    # Satır schema.sql'den zaten var; get-or-create yerine güncelle (routes.py
    # update_setting ile aynı desen).
    setting = await session.get(Setting, "business_name")
    setting.value = "Duman Holding"
    await session.flush()
    assert await report.isletme_adi(session) == "Duman Holding"


# ---------------------------------------------------------------- günlük

async def test_rapor_gunluk_bos_gun_gecerli_pdf_uretir(session):
    pdf = await report.rapor_gunluk(session, "Test İşletme", gun=date(2020, 1, 1))
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 500


async def test_rapor_gunluk_ozet_ve_icerik_dogru(session, ahmet, saman):
    today = report.today_tr()
    meta1 = TxMeta(created_by="test", occurred_at=datetime.now(timezone.utc))
    await add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal("20"), unit="balya", line_total=Decimal("1500.00"))],
        meta1,
    )
    meta2 = TxMeta(created_by="test", occurred_at=datetime.now(timezone.utc))
    await add_payment(session, ahmet.id, Decimal("500.00"), meta2)
    await session.flush()

    stats = await report.gunluk_ozet(session, gun=today)
    assert stats.count == 2
    assert stats.toplam_borc == Decimal("1500.00")
    assert stats.toplam_tahsilat == Decimal("500.00")
    assert stats.net == Decimal("1000.00")

    pdf = await report.rapor_gunluk(session, "Test İşletme", gun=today)
    assert pdf.startswith(b"%PDF")
    text = _pdf_to_text(pdf)
    assert "Ahmet Yılmaz" in text
    assert "1.500,00" in text
    assert "500,00" in text


# ---------------------------------------------------------------- kişi ekstresi

async def test_rapor_kisi_bulunamayan_kisi_hata_verir(session):
    with pytest.raises(report.ReportError):
        await report.rapor_kisi(session, "Test İşletme", 999_999)


async def test_rapor_kisi_yuruyen_bakiye_dogru(session, ahmet, saman):
    t0 = datetime.now(timezone.utc)
    meta1 = TxMeta(created_by="test", occurred_at=t0)
    await add_debt(
        session,
        ahmet.id,
        [LineInput(product_id=saman.id, qty=Decimal("20"), unit="balya", line_total=Decimal("1500.00"))],
        meta1,
    )
    meta2 = TxMeta(created_by="test", occurred_at=t0 + timedelta(seconds=1))
    await add_payment(session, ahmet.id, Decimal("500.00"), meta2)
    await session.flush()

    pdf = await report.rapor_kisi(session, "Test İşletme", ahmet.id)
    assert pdf.startswith(b"%PDF")
    text = _pdf_to_text(pdf)

    assert "Ahmet Yılmaz" in text
    assert "Saman" in text
    # yürüyen bakiye: borçtan sonra 1.500,00, tahsilattan sonra 1.000,00
    assert "1.500,00" in text
    assert "1.000,00" in text


# ---------------------------------------------------------------- genel durum

async def test_rapor_genel_bos_gecerli_pdf_uretir(session):
    pdf = await report.rapor_genel(session, "Test İşletme")
    assert pdf.startswith(b"%PDF")


async def test_rapor_genel_borclu_coktan_aza_siralanir(session):
    az_borclu = Person(full_name="Az Borçlu Kişi")
    cok_borclu = Person(full_name="Çok Borçlu Kişi")
    alacakli = Person(full_name="Alacaklı Kişi")
    session.add_all([az_borclu, cok_borclu, alacakli])
    await session.flush()

    await add_debt(session, az_borclu.id, [], TxMeta(created_by="test"), amount_override=Decimal("100.00"))
    await add_debt(session, cok_borclu.id, [], TxMeta(created_by="test"), amount_override=Decimal("5000.00"))
    await add_payment(session, alacakli.id, Decimal("300.00"), TxMeta(created_by="test"))
    await session.flush()

    stats = await report.genel_ozet(session)
    assert stats.kisi_sayisi == 3
    assert stats.toplam_alacak == Decimal("5100.00")
    assert stats.toplam_borc == Decimal("300.00")

    pdf = await report.rapor_genel(session, "Test İşletme")
    text = _pdf_to_text(pdf)

    idx_cok = text.index("Çok Borçlu Kişi")
    idx_az = text.index("Az Borçlu Kişi")
    idx_alacakli = text.index("Alacaklı Kişi")
    assert idx_cok < idx_az < idx_alacakli
