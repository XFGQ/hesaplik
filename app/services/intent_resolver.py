"""Ayrıştırılmış niyeti (ParsedIntent) gerçek kayda bağlar.

Kişi eşleştirme güvenliği (CLAUDE.md > "Kişi eşleştirme güvenliği", 2026-07-25
kritik bug düzeltmesi): "furkan yılmaz" yazıldığında sistem "furkan duman"a
para yazmıştı çünkü pg_trgm eşiği gevşekti ve tek fuzzy aday "net eşleşme"
sayılıyordu. Artık NET eşleşme yalnızca:
  - Girdi bir kişinin adıyla BİREBİR (normalize) eşleşiyorsa, VEYA
  - Girdi TEK kelimeyse ve tek bir aday SIMILARITY_STRONG üstünde VE bir
    sonraki adaydan belirgin şekilde (SIMILARITY_GAP) daha yakınsa.
Girdi iki (veya daha çok) kelimeyse (ad+soyad) ve birebir eşleşme yoksa,
soyad ayırt edicidir: fuzzy eşleşme ne kadar güçlü olursa olsun asla
otomatik bağlanmaz — kısmi eşleşmeler aday olarak sunulur, kullanıcı karar
verir. Şüphede sor: yanlış kişiye borç yazmak bir sorudan çok daha pahalı.

Ürün: catalog.resolve_or_create — yalnızca kişi netleşince (READY) çağrılır;
belirsiz durumda henüz yeni ürün açılmaz.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person, Product
from app.services import catalog
from app.services.name_utils import strip_turkish_suffix
from app.services.parser import ParsedIntent

# Aday listesine girmek için alt sınır (gevşek — "hiç ilgisiz" olanları eler).
SIMILARITY_CANDIDATE = 0.35
# Otomatik bağlama için üst sınır (yalnızca tek kelimeli girdide kullanılır).
SIMILARITY_STRONG = 0.7
# En yakın adayın ikinciden bu kadar önde olması gerekir ki "belirgin" sayılsın.
SIMILARITY_GAP = 0.15


class ResolutionStatus(str, enum.Enum):
    READY = "ready"
    NEEDS_CONFIRMATION = "needs_confirmation"
    PERSON_NOT_FOUND = "person_not_found"
    UNRECOGNIZED = "unrecognized"


# Kişi/tutar gerektirmeyen sorgu niyetleri (Telegram sorgu komutları).
LIST_KINDS = {"list_all", "list_debtors", "list_creditors", "list_district"}
# Kişi gerektirmeyen rapor niyetleri: report_menu ("rapor ver" -> bot
# günlük/genel seçimini buton ile sorar), report_general/report_daily
# (tür zaten net, PDF doğrudan üretilir). Kişiye özel ekstre isteği
# (report_person) kişi çözümü gerektirdiği için burada değil, balance_query
# gibi NO_AMOUNT_KINDS'te. "search" de kişi gerektirmez — belirli bir kişiye
# değil, bir arama terimine (query) bağlanır (bkz. queries.search_persons).
NO_PERSON_KINDS = LIST_KINDS | {"report_menu", "report_general", "report_daily", "search"}
# person_contact/info_menu (CLAUDE.md > "DÜZELTME — 'bilgi ver' belirsiz,
# SOR") de kişi gerektirir ama tutar gerektirmez, balance_query/
# report_person ile aynı kategoride. "create_person" de burada (CLAUDE.md >
# "Bot kayıt akışı — Grup 2"): amaç zaten kişiyi bulmak/oluşturmak, tutar
# hiç gerekmez. Aynı find_person_match akışından geçtiği için mevcut
# güvenlik davranışları BEDAVA gelir — birebir eşleşme READY (zaten var),
# fuzzy adaylar NEEDS_CONFIRMATION ("hangisi?" + "+ Yeni kişi ekle" butonu),
# hiç eşleşme yoksa PERSON_NOT_FOUND (Evet/Hayır -> adım adım oluşturma).
# "archive_person"/"archive_and_recreate" (CLAUDE.md > "Bot kişi silme =
# arşivleme — Grup 3") de burada: kişi netleşmeden arşivleme YOK, çoklu aday
# varsa aynı "hangisi?" güvenlik akışından geçer. Kişi bulunamazsa bot
# bunlarda "Ekleyeyim mi?" SORMAZ (bkz. app/bot/main.py > _QUERY_ONLY_KINDS)
# — olmayan birini arşivlemek anlamsız.
NO_AMOUNT_KINDS = {
    "balance_query", "report_person", "person_contact", "info_menu",
    "create_person", "archive_person", "archive_and_recreate",
}


@dataclass(slots=True)
class ResolvedIntent:
    status: ResolutionStatus
    kind: str | None = None
    person: Person | None = None
    person_candidates: list[Person] = field(default_factory=list)
    person_name_raw: str | None = None
    qty: Decimal | None = None
    unit: str | None = None
    product_name_raw: str | None = None
    product: Product | None = None
    amount: Decimal | None = None
    district: str | None = None
    query: str | None = None


async def find_person_match(
    session: AsyncSession, name_raw: str
) -> tuple[Person | None, list[Person]]:
    """(net_eşleşme, adaylar) döner.

    net_eşleşme dolu ise doğrudan kullanılabilir (adaylar bu durumda boştur).
    net_eşleşme None ise adaylar listesi kullanıcıya sorulacak seçenekleri
    taşır (boş liste = hiç aday yok -> kişi bulunamadı).

    Eşleştirmeden önce ismin SON kelimesindeki Türkçe çekim eki (iyelik,
    ayrılma, yönelme) koddan soyulur (CLAUDE.md > "KRİTİK — LLM isim
    bozuyor") — hem regex parser'dan hem LLM'den gelen isimde aynı şekilde,
    LLM'in ek temizlemesine güvenilmez."""
    key = strip_turkish_suffix(name_raw)
    if not key:
        return None, []

    exact_stmt = select(Person).where(
        func.lower(Person.full_name) == key, Person.is_active.is_(True)
    )
    exact = (await session.execute(exact_stmt)).scalar_one_or_none()
    if exact is not None:
        return exact, []

    score = func.similarity(func.lower(Person.full_name), key)
    stmt = (
        select(Person, score.label("score"))
        .where(Person.is_active.is_(True), score > SIMILARITY_CANDIDATE)
        .order_by(score.desc())
        .limit(5)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return None, []

    candidates = [row[0] for row in rows]

    # Soyad ayırt edicidir: birden çok kelimeli girdide fuzzy eşleşme ne
    # kadar güçlü olursa olsun asla otomatik bağlanmaz.
    if len(key.split()) == 1:
        top_score = rows[0][1]
        second_score = rows[1][1] if len(rows) > 1 else 0.0
        if top_score >= SIMILARITY_STRONG and (top_score - second_score) >= SIMILARITY_GAP:
            return candidates[0], []

    return None, candidates


async def resolve(session: AsyncSession, intent: ParsedIntent | None) -> ResolvedIntent:
    if intent is None:
        return ResolvedIntent(status=ResolutionStatus.UNRECOGNIZED)

    if intent.kind in NO_PERSON_KINDS:
        return ResolvedIntent(
            status=ResolutionStatus.READY, kind=intent.kind, district=intent.district, query=intent.query
        )

    if intent.kind not in NO_AMOUNT_KINDS and intent.amount is None:
        return ResolvedIntent(status=ResolutionStatus.UNRECOGNIZED, kind=intent.kind)

    person, candidates = await find_person_match(session, intent.person_name or "")

    if person is None:
        if candidates:
            return ResolvedIntent(
                status=ResolutionStatus.NEEDS_CONFIRMATION,
                kind=intent.kind,
                person_candidates=candidates,
                person_name_raw=intent.person_name,
                qty=intent.qty,
                unit=intent.unit,
                product_name_raw=intent.product,
                amount=intent.amount,
            )
        return ResolvedIntent(
            status=ResolutionStatus.PERSON_NOT_FOUND,
            kind=intent.kind,
            person_name_raw=intent.person_name,
            qty=intent.qty,
            unit=intent.unit,
            product_name_raw=intent.product,
            amount=intent.amount,
        )

    product = None
    if intent.product and intent.kind not in NO_AMOUNT_KINDS:
        product, _created = await catalog.resolve_or_create(session, intent.product, intent.unit)

    return ResolvedIntent(
        status=ResolutionStatus.READY,
        kind=intent.kind,
        person=person,
        qty=intent.qty,
        unit=intent.unit,
        product_name_raw=intent.product,
        product=product,
        amount=intent.amount,
    )
