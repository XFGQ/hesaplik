import pytest
import pytest_asyncio

from app.models import Product, ProductAlias
from app.services import catalog


@pytest_asyncio.fixture(loop_scope="session")
async def saman(session):
    p = Product(name="Saman", base_unit="balya")
    session.add(p)
    await session.flush()
    session.add(ProductAlias(product_id=p.id, alias="saman balyası"))
    await session.flush()
    return p


async def test_buyuk_kucuk_harf_onemsiz(session, saman):
    for yazim in ("saman", "Saman", "SAMAN", "  saman  "):
        found = await catalog.find_product(session, yazim)
        assert found is not None and found.id == saman.id


async def test_alias_ile_bulunur(session, saman):
    found = await catalog.find_product(session, "Saman Balyası")
    assert found is not None and found.id == saman.id


async def test_turkce_i_dogru_kucultulur(session):
    p = Product(name="İpek", base_unit="kg")
    session.add(p)
    await session.flush()
    assert catalog.normalize("İPEK") == "ipek"
    assert catalog.normalize("IPEK") == "ıpek"  # farklı kelime, karışmamalı
    assert (await catalog.find_product(session, "İpek")) is not None


async def test_yeni_urun_olusturulur(session):
    product, created = await catalog.resolve_or_create(session, "Kepek", "çuval")
    assert created is True
    assert product.name == "Kepek"
    assert product.base_unit == "çuval"

    again, created2 = await catalog.resolve_or_create(session, "kepek")
    assert created2 is False
    assert again.id == product.id  # ikinci kez aynı ürüne bağlanır


async def test_birim_verilmezse_adet(session):
    product, _ = await catalog.resolve_or_create(session, "Tuz")
    assert product.base_unit == "adet"


async def test_bos_isim_reddedilir(session):
    with pytest.raises(ValueError):
        await catalog.resolve_or_create(session, "   ")


async def test_benzer_urun_onerilir_ama_otomatik_baglanmaz(session, saman):
    assert await catalog.find_product(session, "samn") is None  # otomatik bağlamaz
    oneri = await catalog.suggest_products(session, "samn")
    assert any(p.id == saman.id for p in oneri)  # ama önerir
