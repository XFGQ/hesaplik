"""Ürün kataloğu.

Kullanıcı ürünü serbest metin yazar ("saman", "Saman", "saman balyası").
Bu modül onu tek bir ürün kaydına bağlar. Amaç: aynı şeyin defterde
üç ayrı isimle birikmemesi.

Eşleştirme sırası:
  1. products.name birebir (harf büyüklüğü önemsiz)
  2. product_aliases birebir
  3. pg_trgm ile yakın eşleşme (yalnızca öneri; otomatik bağlamaz)
  4. Hiçbiri yoksa yeni ürün açılır ve yazılan hâli alias olarak kaydedilir

Fuzzy eşleşme kasten otomatik değil: "arpa" ile "kepek" karışırsa defter
sessizce yanlışa döner. Öneri kullanıcıya sunulur, kararı o verir.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductAlias

# Turkce "I" sorunu: str.lower() "I" -> "i" yapar, dogrusu "ı"dir.
# Karsilastirmayi tek yonlu ve tutarli yapmak icin ozel normalize kullaniyoruz.
_TR_MAP = str.maketrans({"I": "ı", "İ": "i"})


def normalize(text: str) -> str:
    return text.translate(_TR_MAP).lower().strip()


async def find_product(session: AsyncSession, name_raw: str) -> Product | None:
    """Birebir eşleşme. Bulamazsa None."""
    key = normalize(name_raw)
    if not key:
        return None

    stmt = select(Product).where(func.lower(Product.name) == key, Product.is_active.is_(True))
    product = (await session.execute(stmt)).scalar_one_or_none()
    if product is not None:
        return product

    stmt = (
        select(Product)
        .join(ProductAlias, ProductAlias.product_id == Product.id)
        .where(func.lower(ProductAlias.alias) == key, Product.is_active.is_(True))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def suggest_products(session: AsyncSession, name_raw: str, limit: int = 3) -> list[Product]:
    """Yakın ürünler. Karar kullanıcının."""
    key = normalize(name_raw)
    if len(key) < 2:
        return []
    score = func.similarity(func.lower(Product.name), key)
    stmt = (
        select(Product)
        .where(Product.is_active.is_(True), score > 0.35)
        .order_by(score.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def resolve_or_create(
    session: AsyncSession, name_raw: str, unit: str | None = None
) -> tuple[Product, bool]:
    """Ürünü bul veya oluştur. (ürün, yeni_mi) döndürür."""
    name = " ".join(name_raw.split())
    if not name:
        raise ValueError("Ürün adı boş olamaz")

    product = await find_product(session, name)
    if product is not None:
        return product, False

    product = Product(name=name, base_unit=(unit or "adet").strip() or "adet")
    session.add(product)
    await session.flush()
    session.add(ProductAlias(product_id=product.id, alias=normalize(name)))
    await session.flush()
    return product, True
