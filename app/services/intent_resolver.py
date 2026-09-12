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

Ürün: catalog.resolve_product_or_suggest — yalnızca kişi netleşince (READY)
çağrılır; belirsiz durumda henüz yeni ürün açılmaz. Ürün adı da bulanıksa
(CLAUDE.md > "Ürün yazım düzeltme (fuzzy)", Grup 5 — "samaan" gibi yazım
hataları) PRODUCT_NEEDS_CONFIRMATION dönülür, otomatik bağlanmaz/oluşturulmaz.

İsim eşleştirme + öngörücü teyit (CLAUDE.md, 2026-08): pg_trgm HİÇBİR aday
bulamadığında (ör. "doman" -> "Duman" benzerliği SIMILARITY_CANDIDATE'in
altında kalıyor) son çare olarak LLM'e danışılır (bkz. find_person_match ->
llm_provider.suggest_person_match). LLM kayıtlı isim listesinden bir aday
önerirse, bu aday TEK bir "candidate" olarak mevcut NEEDS_CONFIRMATION
akışına sokulur — kullanıcı yine "hangisini demek istedin?" sorusuyla
karşılaşır, LLM'in önerisi asla sormadan otomatik bağlanmaz. LLM de bir şey
bulamazsa (ya da erişilemezse) davranış aynen eskisi gibi kalır: PERSON_NOT_FOUND.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person, Product
from app.services import catalog, llm_provider, saman_fiyat
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
    PRODUCT_NEEDS_CONFIRMATION = "product_needs_confirmation"
    UNRECOGNIZED = "unrecognized"


# Kişi/tutar gerektirmeyen sorgu niyetleri (Telegram sorgu komutları).
LIST_KINDS = {"list_all", "list_debtors", "list_creditors", "list_district"}
# Kişi gerektirmeyen rapor niyetleri: report_menu ("rapor ver" -> bot
# günlük/genel seçimini buton ile sorar), report_general/report_daily
# (tür zaten net, PDF doğrudan üretilir). Kişiye özel ekstre isteği
# (report_person) kişi çözümü gerektirdiği için burada değil, balance_query
# gibi NO_AMOUNT_KINDS'te. "search" de kişi gerektirmez — belirli bir kişiye
# değil, bir arama terimine (query) bağlanır (bkz. queries.search_persons).
# "total_balance" da kişisizdir (CLAUDE.md > "Toplam bakiye niyeti"):
# "toplam borç" defterin TAMAMININ özetidir, belirli bir kişiye bağlanmaz —
# eskiden "tüm"/"total" kişi adı sanılıp "defterde yok" deniyordu.
# "product_query" (ürün/stok/fiyat sorgusu) da kişisizdir — üstelik defterin
# tutmadığı bir bilgi soruluyor, bu yüzden bot "bu özellik henüz yok" der
# (bkz. message_processor > PRODUCT_QUERY_UNSUPPORTED). Yine de NİYET olarak
# tanınır: tanınmazsa cümle bir kişi adı ya da bir kayıt sanılabilirdi.
# "running_mismatch" (CLAUDE.md > "Koşan format"): koşan üçlünün matematiği
# tutmuyor ("70-25-50": 70'ten 50'ye fark 20 olmalı ama 25 yazılmış). Sorulan
# şey saf aritmetiktir, kişiye bağlı değildir — bu yüzden kişi ÇÖZÜLMEZ (ve
# yanlış yazılmış bir isim yüzünden asıl uyarı gölgelenmez). Kullanıcı "fark
# N olsun" derse cümle DÜZELTİLİP normal akıştan yeniden geçirilir; kişi/ürün
# çözümü orada, her zamanki güvenlik kurallarıyla yapılır.
NO_PERSON_KINDS = LIST_KINDS | {
    "report_menu", "report_general", "report_daily", "search", "total_balance",
    "product_query", "running_mismatch",
}
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
# — olmayan birini arşivlemek anlamsız. "edit_person" (CLAUDE.md > "Silme
# mesajı + kişi düzenleme — Grup 4") de burada: amaç kişi bilgisini
# güncellemek, tutar hiç gerekmez; kişi netleşmeden düzenleme yapılmaz ve
# olmayan biri de "Ekleyeyim mi?" sorusuna düşmez (aynı _QUERY_ONLY_KINDS).
# "delete_ambiguous" (CLAUDE.md > "'sil' bağlam ayrımı"): silme fiili +
# para/mal bağlamı içeren cümle — henüz ne tahsilat ne de silme yapılır,
# yalnızca KİŞİ çözülür ki bot "tahsilat mı, kişiyi silmek mi?" diye
# sorabilsin. Tutar gerekmez, aynı kişi eşleştirme güvenliğinden geçer.
NO_AMOUNT_KINDS = {
    "balance_query", "report_person", "person_contact", "info_menu",
    "create_person", "archive_person", "archive_and_recreate", "edit_person",
    "delete_ambiguous",
}

# LLM'e isim önerisi SORULMAYAN niyetler (2026-08-31 hız düzeltmesi):
# create_person'da amaç zaten YENİ bir kişi açmaktır — pg_trgm'in "böyle
# biri yok" demesi tek başına yeterli ve kesin bir cevaptır. Buluta ayrıca
# "en yakın kişi kim?" diye sormak (~5 sn) hem gereksiz bekletiyor hem de
# yeni kişi eklerken alakasız bir adayı gündeme getiriyordu. LLM önerisi
# yalnızca MEVCUT bir kişiyle işlem yapılırken (borç/tahsilat/bakiye/ekstre)
# ve isim hiçbir kayda benzemediğinde devreye girer.
NO_LLM_SUGGESTION_KINDS = {"create_person"}


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
    field_name: str | None = None  # yalnızca kind == "edit_person"
    new_value: str | None = None  # yalnızca kind == "edit_person", NET komutta dolu
    product_suggestion: Product | None = None  # yalnızca status == PRODUCT_NEEDS_CONFIRMATION
    # Tutarı söylenmemiş borç kapanışı ("ali borcunu ödedi"): tutar burada
    # UYDURULMAZ, message_processor güncel bakiyeyi teklif edip onaylatır.
    close_debt: bool = False
    # Koşan format ("70-20-50" — CLAUDE.md > "Koşan format"): qty üç sayının
    # FARKI, running_before/change/after kullanıcıya gösterilecek üçlü.
    # running=True olan bir kayıt niyetinde tutar boş kalabilir (uydurulmaz,
    # message_processor sorar).
    running: bool = False
    running_before: Decimal | None = None
    running_change: Decimal | None = None
    running_after: Decimal | None = None
    # Varsayılan saman fiyatı (CLAUDE.md > "Varsayılan saman fiyatı"):
    # assumed_* parser'dan gelir (bkz. ParsedIntent); default_unit_price
    # tutar varsayılan fiyattan hesaplandıysa message_processor doldurur —
    # önizleme ve kayıt onayı kullanılan fiyatı gösterir.
    assumed_product: bool = False
    assumed_kind: bool = False
    default_unit_price: Decimal | None = None


async def find_person_match(
    session: AsyncSession, name_raw: str, *, allow_llm_suggestion: bool = True
) -> tuple[Person | None, list[Person]]:
    """(net_eşleşme, adaylar) döner.

    allow_llm_suggestion=False ise pg_trgm hiç aday bulamadığında LLM'e son
    çare olarak danışılmaz (bkz. NO_LLM_SUGGESTION_KINDS) — sonuç doğrudan
    "aday yok" olur, hiçbir ağ çağrısı yapılmaz.

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
        if not allow_llm_suggestion:
            return None, []
        suggestion = await _llm_suggest_person(session, key)
        if suggestion is not None:
            # LLM'in önerisi TEK bir "aday" olarak mevcut NEEDS_CONFIRMATION
            # akışına sokulur — otomatik bağlanmaz, kullanıcı yine "hangisini
            # demek istedin?" (+ "Yeni kişi ekle") ile onaylar/reddeder.
            return None, [suggestion]
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


async def _llm_suggest_person(session: AsyncSession, key: str) -> Person | None:
    """pg_trgm SIFIR aday bulduğunda son çare (CLAUDE.md > "İsim eşleştirme
    + öngörücü teyit"): aktif LLM varsa kayıtlı isim listesini verip
    kullanıcının yazdığı ismin (`key`, ek/hitap/bağlam kelimeleri zaten
    ayıklanmış) hangi kayıtlı kişiye karşılık gelebileceğini sorar. LLM
    erişilemiyorsa, kayıtlı kimse yoksa ya da güvenilir bir öneri
    dönmüyorsa (bkz. llm_provider.suggest_person_match — listede olmayan
    isimler reddedilir) None döner ve çağıran mevcut "kişi bulunamadı"
    davranışına aynen devam eder."""
    provider = await llm_provider.get_active_provider(session)
    if provider is None:
        return None

    names_stmt = (
        select(Person.full_name)
        .where(Person.is_active.is_(True))
        .order_by(Person.full_name)
        .limit(llm_provider.NAME_MATCH_CANDIDATE_LIMIT)
    )
    names = list((await session.execute(names_stmt)).scalars().all())
    if not names:
        return None

    matched_name = await llm_provider.suggest_person_match(provider, key, names)
    if matched_name is None:
        return None

    match_stmt = select(Person).where(
        func.lower(Person.full_name) == matched_name.lower(), Person.is_active.is_(True)
    )
    return (await session.execute(match_stmt)).scalar_one_or_none()


async def resolve(
    session: AsyncSession, intent: ParsedIntent | None, *, known_person: Person | None = None
) -> ResolvedIntent:
    """`known_person`: kişi daha önceki bir adımda seçildiyse (Telegram
    /borc'ta "hangisi?" cevaplandı) isim YENİDEN eşleştirilmez, seçilen kişi
    aynen kullanılır; ürün, tutar ve saman kuralları yine bu yoldan geçer."""
    if intent is None:
        return ResolvedIntent(status=ResolutionStatus.UNRECOGNIZED)

    if intent.kind in NO_PERSON_KINDS:
        return ResolvedIntent(
            status=ResolutionStatus.READY,
            kind=intent.kind,
            district=intent.district,
            query=intent.query,
            product_name_raw=intent.product,
            qty=intent.qty,
            running=intent.running,
            running_before=intent.running_before,
            running_change=intent.running_change,
            running_after=intent.running_after,
        )

    # Tutarsız bir kayıt niyeti normalde çözülemez sayılır. Tek istisna borç
    # KAPANIŞIDIR ("ali borcunu ödedi"): niyet net, yalnızca tutar
    # söylenmemiş — kişi çözüldükten sonra güncel bakiye teklif edilip
    # kullanıcıya onaylatılır (bkz. message_processor).
    # Koşan format (CLAUDE.md > "Koşan format") da tutarsız gelebilir:
    # "70-20-50" yalnızca MAL adedini söyler, TL ayrıca girilir. Tutar
    # uydurulmaz — kişi/ürün çözüldükten sonra message_processor sorar.
    # Saman da tutarsız gelebilir ("furkan 20 saman aldı"): tutar varsayılan
    # saman fiyatından hesaplanacak (CLAUDE.md > "Varsayılan saman fiyatı").
    # Fiyat okunamıyorsa (bozuk ayar) eski davranış aynen sürer. Birim
    # yazılmadıysa balya sayılır — fiyat balya başına, ve saman kataloğda
    # yoksa "adet" birimiyle açılmasın.
    unit = intent.unit
    saman_default = saman_fiyat.is_candidate(intent)
    if saman_default:
        if await saman_fiyat.get_saman_price(session) is None:
            saman_default = False
        else:
            unit = unit or saman_fiyat.SAMAN_BIRIM

    if (
        intent.kind not in NO_AMOUNT_KINDS
        and intent.amount is None
        and not intent.close_debt
        and not intent.running
        and not saman_default
    ):
        return ResolvedIntent(status=ResolutionStatus.UNRECOGNIZED, kind=intent.kind)

    if known_person is not None:
        person, candidates = known_person, []
    else:
        person, candidates = await find_person_match(
            session,
            intent.person_name or "",
            allow_llm_suggestion=intent.kind not in NO_LLM_SUGGESTION_KINDS,
        )

    if person is None:
        if candidates:
            return ResolvedIntent(
                status=ResolutionStatus.NEEDS_CONFIRMATION,
                kind=intent.kind,
                person_candidates=candidates,
                person_name_raw=intent.person_name,
                qty=intent.qty,
                unit=unit,
                product_name_raw=intent.product,
                amount=intent.amount,
                field_name=intent.field,
                new_value=intent.new_value,
                close_debt=intent.close_debt,
                running=intent.running,
                running_before=intent.running_before,
                running_change=intent.running_change,
                running_after=intent.running_after,
                assumed_product=intent.assumed_product,
                assumed_kind=intent.assumed_kind,
            )
        return ResolvedIntent(
            status=ResolutionStatus.PERSON_NOT_FOUND,
            kind=intent.kind,
            person_name_raw=intent.person_name,
            qty=intent.qty,
            unit=unit,
            product_name_raw=intent.product,
            amount=intent.amount,
            field_name=intent.field,
            new_value=intent.new_value,
            close_debt=intent.close_debt,
            running=intent.running,
            running_before=intent.running_before,
            running_change=intent.running_change,
            running_after=intent.running_after,
            assumed_product=intent.assumed_product,
            assumed_kind=intent.assumed_kind,
        )

    product = None
    if intent.product and intent.kind not in NO_AMOUNT_KINDS:
        product, suggestion = await catalog.resolve_product_or_suggest(session, intent.product, unit)
        if suggestion is not None:
            # Ürün adı bulanık ("samaan" gibi) — otomatik bağlanmaz/oluşturulmaz,
            # kullanıcıya sorulmalı (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)").
            return ResolvedIntent(
                status=ResolutionStatus.PRODUCT_NEEDS_CONFIRMATION,
                kind=intent.kind,
                person=person,
                qty=intent.qty,
                unit=unit,
                product_name_raw=intent.product,
                product_suggestion=suggestion,
                amount=intent.amount,
                field_name=intent.field,
                new_value=intent.new_value,
                close_debt=intent.close_debt,
                running=intent.running,
                running_before=intent.running_before,
                running_change=intent.running_change,
                running_after=intent.running_after,
                assumed_product=intent.assumed_product,
                assumed_kind=intent.assumed_kind,
            )

    return ResolvedIntent(
        status=ResolutionStatus.READY,
        kind=intent.kind,
        person=person,
        qty=intent.qty,
        unit=unit,
        product_name_raw=intent.product,
        product=product,
        amount=intent.amount,
        field_name=intent.field,
        new_value=intent.new_value,
        close_debt=intent.close_debt,
        running=intent.running,
        running_before=intent.running_before,
        running_change=intent.running_change,
        running_after=intent.running_after,
        assumed_product=intent.assumed_product,
        assumed_kind=intent.assumed_kind,
    )
