import pytest
import pytest_asyncio
from sqlalchemy import func, select

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


# --------------------------------------------------------------- Ürün yazım düzeltme (fuzzy)
# (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)", Grup 5). Hatalı yazılmış ürün
# adları ("samaan", "saman 15") sessizce yeni ürün olarak açılmamalı —
# resolve_with_suggestion/resolve_product_or_suggest bu kararı çağırana
# (bot) bırakır, kendisi hiçbir şey oluşturmaz/bağlamaz.


async def test_tam_eslesme_found_doner_onay_gerekmez(session, saman):
    resolution = await catalog.resolve_with_suggestion(session, "saman")
    assert resolution.status == "found"
    assert resolution.product.id == saman.id
    assert resolution.suggestion is None


async def test_alias_ile_de_tam_eslesme_found_sayilir(session, saman):
    resolution = await catalog.resolve_with_suggestion(session, "Saman Balyası")
    assert resolution.status == "found"
    assert resolution.product.id == saman.id


async def test_samaan_yaziminda_saman_onerilir(session, saman):
    # "samaan" -> "saman" trigram benzerliği CANDIDATE ile STRONG arasında
    # (~0.625) — hâlâ bir öneri, otomatik bağlanmaz.
    resolution = await catalog.resolve_with_suggestion(session, "samaan")
    assert resolution.status == "suggestion"
    assert resolution.suggestion.id == saman.id
    assert resolution.product is None


async def test_guclu_eslesme_de_otomatik_baglanmaz_oneri_kalir(session):
    # Ürün eşleştirmesi kişi eşleştirmenin aksine SIMILARITY_STRONG üstünde
    # bile otomatik bağlanmaz (CLAUDE.md > catalog.py modül docstring'i,
    # "Fuzzy eşleşme KASTEN otomatik değil") — yalnızca daha güçlü bir öneri.
    p = Product(name="Arpa Kırığı", base_unit="çuval")
    session.add(p)
    await session.flush()

    resolution = await catalog.resolve_with_suggestion(session, "arpa kırığıı")
    assert resolution.status == "suggestion"
    assert resolution.suggestion.id == p.id


async def test_saman_15_rakam_ayiklanip_onerilir(session, saman):
    resolution = await catalog.resolve_with_suggestion(session, "saman 15")
    assert resolution.status == "suggestion"
    assert resolution.suggestion.id == saman.id


async def test_hic_benzemeyen_urun_yeni_sayilir_sormadan(session, saman):
    resolution = await catalog.resolve_with_suggestion(session, "tamamen alakasiz urun xyz")
    assert resolution.status == "new"
    assert resolution.suggestion is None
    assert resolution.product is None


async def test_fuzzy_esik_siniri_candidate_altinda_new_sayilir(session, saman):
    # "smaan" -> "saman" skoru SIMILARITY_CANDIDATE'in (0.35) altında (~0.2)
    # — hiçbir aday sayılmaz, NET yeni ürün.
    resolution = await catalog.resolve_with_suggestion(session, "smaan")
    assert resolution.status == "new"


async def test_fuzzy_esik_siniri_candidate_ustunde_suggestion_sayilir(session, saman):
    # "samn" -> "saman" skoru SIMILARITY_CANDIDATE'in hemen üstünde (~0.375)
    # — zayıf ama yine de bir aday, öneri sunulur.
    resolution = await catalog.resolve_with_suggestion(session, "samn")
    assert resolution.status == "suggestion"
    assert resolution.suggestion.id == saman.id


async def test_resolve_product_or_suggest_found_dogrudan_kullanilir(session, saman):
    product, suggestion = await catalog.resolve_product_or_suggest(session, "saman")
    assert product.id == saman.id
    assert suggestion is None


async def test_resolve_product_or_suggest_suggestion_urun_hic_olusturmaz(session, saman):
    product, suggestion = await catalog.resolve_product_or_suggest(session, "samaan")
    assert product is None
    assert suggestion.id == saman.id

    # Hiçbir yeni ürün sessizce açılmamış olmalı.
    count = (await session.execute(select(func.count(Product.id)))).scalar_one()
    assert count == 1  # yalnızca "saman" fixture'ı


async def test_resolve_product_or_suggest_new_gercekten_olusturur(session):
    product, suggestion = await catalog.resolve_product_or_suggest(session, "Kepek Unu", "çuval")
    assert suggestion is None
    assert product.name == "Kepek Unu"
    assert product.base_unit == "çuval"

    again = await catalog.find_product(session, "kepek unu")
    assert again is not None and again.id == product.id


async def test_bos_isim_resolve_with_suggestion_reddedilir(session):
    with pytest.raises(ValueError):
        await catalog.resolve_with_suggestion(session, "   ")
