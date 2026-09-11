"""Varsayılan saman balya fiyatı (CLAUDE.md > "Varsayılan saman fiyatı").

Kural 4'ün ("tutarı kullanıcı yazar, fiyat listesi bağlamaz") TEK istisnası.
Kullanıcı tutar yazdıysa her zaman o esastır. Yazmadıysa ve ürün SAMAN,
birim BALYA ise tutar = adet × bu fiyat. Arpa vb. hiçbir ürün etkilenmez;
onlarda tutar yine kullanıcıdan istenir.

Fiyat settings tablosunda `saman_birim_fiyat` anahtarında kanonik Decimal
metni olarak durur ("180.00"). Değişiklik yalnızca set_saman_price'tan
geçer: doğrulanır ve audit_log'a eski → yeni yazılır.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Setting
from app.services import catalog, parser
from app.services.ledger import money

SAMAN_FIYAT_KEY = "saman_birim_fiyat"
# Satır hiç yoksa (migration çalışmamış eski bir veritabanı) kullanılan
# değer — db/schema.sql ve 013 migration'ının tohumladığı değerle AYNI.
SAMAN_FIYAT_DEFAULT = Decimal("180.00")
# Yazım hatasına karşı üst sınır (fazladan sıfır: 1800000).
SAMAN_FIYAT_MAX = Decimal("1000000")

SAMAN_URUN = parser.SAMAN_URUN
SAMAN_BIRIM = parser.SAMAN_BIRIM

_RECORD_KINDS = ("debt", "payment")


class SamanFiyatError(ValueError):
    """Geçersiz fiyat (sıfır, negatif, sayı değil, üst sınırın üstünde)."""


def is_saman(name: str | None) -> bool:
    return bool(name) and catalog.normalize(name) == SAMAN_URUN


def is_candidate(intent: parser.ParsedIntent) -> bool:
    """Ayrıştırılmış (regex ya da LLM) bir kayıt niyeti varsayılan fiyata
    aday mı? DB'ye bakmaz. Tutar yazılmışsa ASLA aday değildir — kullanıcının
    tutarı her zaman kazanır. Birim yazılmışsa balya olmalı: balya fiyatı
    kilo adediyle çarpılmaz."""
    return (
        intent.kind in _RECORD_KINDS
        and intent.amount is None
        and not intent.close_debt
        and intent.qty is not None
        and intent.qty > 0
        and is_saman(intent.product)
        and intent.unit in (None, SAMAN_BIRIM)
    )


def applies(resolved) -> bool:
    """Çözülmüş (kişi + ürün netleşmiş) bir kayıt, tutarını varsayılan saman
    fiyatından alacak mı? Ürün kataloğun saman ürünü, etkin birim balya."""
    product = resolved.product
    return (
        resolved.kind in _RECORD_KINDS
        and resolved.amount is None
        and not resolved.close_debt
        and resolved.qty is not None
        and resolved.qty > 0
        and product is not None
        and is_saman(product.name)
        and (resolved.unit or product.base_unit) == SAMAN_BIRIM
    )


def _valid(value) -> Decimal | None:
    try:
        price = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not price.is_finite() or price <= 0 or price > SAMAN_FIYAT_MAX:
        return None
    return money(price)


def parse_price(text: str) -> Decimal | None:
    """Kullanıcının yazdığı fiyat: "180", "180,50", "1.250". Türkçe biçim
    parser'daki sayı çözücüyle aynı. Geçersizse None."""
    value = parser.parse_turkish_number(text or "")
    return _valid(value) if value is not None else None


async def get_saman_price(session: AsyncSession) -> Decimal | None:
    """Güncel saman balya fiyatı. Satır yoksa tohum değeri (180). Satır var
    ama bozuksa None: bozuk bir fiyattan tutar HESAPLANMAZ, sistem eski
    davranışa (tutarı kullanıcıdan iste) düşer."""
    row = await session.get(Setting, SAMAN_FIYAT_KEY)
    if row is None:
        return SAMAN_FIYAT_DEFAULT
    return _valid(row.value)


async def set_saman_price(session: AsyncSession, price: Decimal, actor: str) -> Decimal:
    value = _valid(price)
    if value is None:
        raise SamanFiyatError(
            f"Saman fiyatı 0'dan büyük ve en fazla {SAMAN_FIYAT_MAX:,.0f} TL olmalı".replace(",", ".")
        )

    setting = await session.get(Setting, SAMAN_FIYAT_KEY)
    before = setting.value if setting is not None else None
    if setting is None:
        session.add(Setting(key=SAMAN_FIYAT_KEY, value=str(value)))
    else:
        setting.value = str(value)
        setting.updated_at = datetime.now(timezone.utc)
    session.add(
        AuditLog(
            actor=actor,
            action="set_saman_price",
            entity="settings",
            entity_id=SAMAN_FIYAT_KEY,
            before={"value": before},
            after={"value": str(value)},
        )
    )
    await session.flush()
    return value
