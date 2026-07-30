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

Ürün yazım düzeltme (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)", Grup 5):
`resolve_with_suggestion`/`resolve_product_or_suggest` — Telegram bot
kayıt akışında `resolve_or_create`'in yerine geçer, hatalı yazılmış ("samaan",
"saman 15") ürün adlarının sessizce yeni ürün olarak açılmasını engeller.
Web API (`app/api/routes.py`) hâlâ eski `resolve_or_create`'i kullanır —
bu davranış yalnızca bot kayıt akışı için, mevcut API'yi bozmaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductAlias

# Turkce "I" sorunu: str.lower() "I" -> "i" yapar, dogrusu "ı"dir.
# Karsilastirmayi tek yonlu ve tutarli yapmak icin ozel normalize kullaniyoruz.
_TR_MAP = str.maketrans({"I": "ı", "İ": "i"})

# Ürün fuzzy eşleştirme eşikleri (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)").
# Kişi eşleştirmedeki (intent_resolver.SIMILARITY_*) mantığa paralel isimli
# ama ürün ASLA otomatik bağlanmaz (üstteki modül docstring'i) — bu eşikler
# yalnızca "hiç aday yok, sormadan geç" (CANDIDATE altı) ile "aday var, sor"
# (CANDIDATE üstü) ayrımı için kullanılır. STRONG, CANDIDATE'in üstünde ayrı
# bir "çok güçlü eşleşme" bandı — davranışı değiştirmez (ikisi de sorar),
# yalnızca eşik sınırlarının adlandırılmış sabitler olması istendiği için var.
SIMILARITY_CANDIDATE = 0.35
SIMILARITY_STRONG = 0.7

_DIGIT_RE = re.compile(r"\d+")


def normalize(text: str) -> str:
    return text.translate(_TR_MAP).lower().strip()


def _strip_digits(text: str) -> str:
    """"saman 15" -> "saman" (rakamları ayıklar, fazla boşluğu toplar)."""
    return " ".join(_DIGIT_RE.sub(" ", text).split())


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
        .where(Product.is_active.is_(True), score > SIMILARITY_CANDIDATE)
        .order_by(score.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def _best_match(session: AsyncSession, key: str) -> Product | None:
    """En yakın tek ürün, SIMILARITY_CANDIDATE üstündeyse. Yoksa None
    ("hiç benzer yok" — CLAUDE.md > "Ürün yazım düzeltme (fuzzy)")."""
    if len(key) < 2:
        return None
    score = func.similarity(func.lower(Product.name), key)
    stmt = (
        select(Product)
        .where(Product.is_active.is_(True), score > SIMILARITY_CANDIDATE)
        .order_by(score.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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


@dataclass(slots=True)
class ProductResolution:
    status: str  # "found" | "suggestion" | "new"
    name_raw: str
    product: Product | None = None       # yalnızca status == "found"
    suggestion: Product | None = None    # yalnızca status == "suggestion" (henüz BAĞLANMADI)


async def resolve_with_suggestion(session: AsyncSession, name_raw: str) -> ProductResolution:
    """Ürünü tam eşleşmeyle bulmayı dener; bulamazsa fuzzy öneriye bakar
    (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)", Grup 5). HİÇBİR ŞEYİ
    otomatik oluşturmaz/bağlamaz — karar çağırana (bot) bırakılır:
      "found"      -> birebir eşleşme (ad ya da alias), doğrudan kullanılır.
      "suggestion" -> yakın bir ürün var (SIMILARITY_CANDIDATE üstü), kullanıcıya
                      "X mi demek istediniz?" sorulmalı, otomatik bağlanmaz.
      "new"        -> hiç benzer ürün yok, NET yeni ürün sayılır — sormadan
                      oluşturulabilir (CLAUDE.md: "tam eşleşme veya hiç
                      benzer yok" = sormadan devam).
    "saman 15" gibi rakam/çöp içeren adlar temizlenip önce tam eşleşme,
    sonra fuzzy eşleşme buna göre denenir."""
    name = " ".join(name_raw.split())
    if not name:
        raise ValueError("Ürün adı boş olamaz")

    product = await find_product(session, name)
    if product is not None:
        return ProductResolution(status="found", name_raw=name, product=product)

    cleaned = _strip_digits(name)
    if cleaned and cleaned != name:
        cleaned_product = await find_product(session, cleaned)
        if cleaned_product is not None:
            return ProductResolution(status="suggestion", name_raw=name, suggestion=cleaned_product)

    key = normalize(cleaned or name)
    best = await _best_match(session, key)
    if best is not None:
        return ProductResolution(status="suggestion", name_raw=name, suggestion=best)

    return ProductResolution(status="new", name_raw=name)


async def resolve_product_or_suggest(
    session: AsyncSession, name_raw: str, unit: str | None = None
) -> tuple[Product | None, Product | None]:
    """`resolve_with_suggestion`'ı sarar: (ürün, öneri) döner.
    - ürün dolu, öneri None  -> doğrudan kullan ("found" ya da "new" — "new"
      durumunda burada gerçekten OLUŞTURULUR, çünkü NET sayılır).
    - ürün None, öneri dolu  -> otomatik bağlama, kullanıcıya sor."""
    resolution = await resolve_with_suggestion(session, name_raw)
    if resolution.status == "found":
        return resolution.product, None
    if resolution.status == "suggestion":
        return None, resolution.suggestion
    product, _created = await resolve_or_create(session, name_raw, unit)
    return product, None
