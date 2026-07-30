"""raw_message -> parser -> (gerekirse LLM) -> intent_resolver -> ledger.

Kural motoru (app/services/parser.py) her zaman önce denenir; çözerse
yüksek güven sayılır ve net/güvenli eşleşme doğrudan CONFIRMED kaydedilir.
Kural parser çözemezse (None) ve LLM aktifse (CLAUDE.md > "Faz 4 — LLM")
fallback olarak LLM'e sorulur. LLM'in çıktısı da AYNI intent_resolver'dan
geçer (kişi eşleştirme, ürün, güvenlik kuralları aynen uygulanır) ama
kaynağı düşük güven sayılır: kayıt (borç/tahsilat) öncesi kullanıcıdan
"bunu mu demek istediniz?" onayı istenir — bakiye/liste sorguları salt
okunur olduğu için onay gerekmez. Kural parser da LLM de çözemezse hiçbir
şey kaydedilmez; sonucun outcome'u botun ne soracağını belirler.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawMessage, Transaction, TxSource
from app.services import llm_provider, parser, report
from app.services.intent_resolver import LIST_KINDS, ResolutionStatus, ResolvedIntent, resolve
from app.services.ledger import Balance, LineInput, TxMeta, add_debt, add_payment, balance_of
from app.services.queries import (
    PersonBalanceRow,
    PersonTransactionRow,
    list_person_transactions,
    list_persons_with_balance,
    search_persons,
)

TELEGRAM_ACTOR = "telegram-bot"

# Bakiye sorgusunda Telegram'a gönderilen tablo en fazla bu kadar hareket
# gösterir, gerisi "...ve N kayıt daha" ile özetlenir (CLAUDE.md > "Bot
# sorgu anlama" Grup 1, madde 2).
BALANCE_TABLE_LIMIT = 15

_LIST_SCOPE_BY_KIND = {
    "list_all": "all",
    "list_debtors": "debtors",
    "list_creditors": "creditors",
    "list_district": "all",
}


class ProcessOutcome(str, enum.Enum):
    RECORDED = "recorded"
    CREATE_PERSON = "create_person"
    BALANCE = "balance"
    LIST = "list"
    SEARCH = "search"
    REPORT_MENU = "report_menu"
    REPORT_DAILY = "report_daily"
    REPORT_GENERAL = "report_general"
    REPORT_PERSON = "report_person"
    PERSON_CONTACT = "person_contact"
    INFO_MENU = "info_menu"
    ARCHIVE_CONFIRM = "archive_confirm"
    EDIT_PERSON_CONFIRM = "edit_person_confirm"
    EDIT_PERSON_MENU = "edit_person_menu"
    PRODUCT_NEEDS_CONFIRMATION = "product_needs_confirmation"
    LLM_CONFIRMATION = "llm_confirmation"
    NEEDS_CONFIRMATION = "needs_confirmation"
    PERSON_NOT_FOUND = "person_not_found"
    UNRECOGNIZED = "unrecognized"


@dataclass(slots=True)
class ProcessResult:
    outcome: ProcessOutcome
    resolved: ResolvedIntent
    transaction_id: int | None = None
    balance: Balance | None = None
    balance_before: Balance | None = None
    persons: list[PersonBalanceRow] | None = None
    transactions: list[PersonTransactionRow] | None = None
    transactions_total: int | None = None
    report_pdf: bytes | None = None
    report_stats: report.DailyStats | report.GeneralStats | None = None


async def process_raw_message(session: AsyncSession, raw: RawMessage, text: str) -> ProcessResult:
    intent = parser.parse(text)
    source = "rule"

    if intent is None:
        provider = llm_provider.get_provider()
        if provider is not None:
            intent = await provider.parse(text)
            source = "llm"

    resolved = await resolve(session, intent)
    return await handle_resolved(session, raw, resolved, text, source=source)


async def handle_resolved(
    session: AsyncSession,
    raw: RawMessage,
    resolved: ResolvedIntent,
    text: str,
    source: str = "rule",
) -> ProcessResult:
    """Zaten çözülmüş bir niyeti işler. Bot'un onay callback'leri (kişi
    oluşturuldu / aday seçildi / LLM önizlemesi onaylandı) de bu yolu
    tekrar kullanır — bu durumlarda kullanıcı zaten onay verdiği için
    source="rule" (varsayılan) ile çağrılır, doğrudan kaydeder."""
    if resolved.status == ResolutionStatus.UNRECOGNIZED:
        return ProcessResult(outcome=ProcessOutcome.UNRECOGNIZED, resolved=resolved)
    if resolved.status == ResolutionStatus.NEEDS_CONFIRMATION:
        return ProcessResult(outcome=ProcessOutcome.NEEDS_CONFIRMATION, resolved=resolved)
    if resolved.status == ResolutionStatus.PERSON_NOT_FOUND:
        return ProcessResult(outcome=ProcessOutcome.PERSON_NOT_FOUND, resolved=resolved)
    if resolved.status == ResolutionStatus.PRODUCT_NEEDS_CONFIRMATION:
        # Ürün adı bulanık (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)") —
        # kişi zaten netleşti ama ürün otomatik bağlanmadı/oluşturulmadı,
        # bot Evet/Hayır yeni ürün/İptal sormalı (bkz. app/bot/main.py).
        return ProcessResult(outcome=ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION, resolved=resolved)

    if resolved.kind == "report_menu":
        return ProcessResult(outcome=ProcessOutcome.REPORT_MENU, resolved=resolved)

    if resolved.kind == "report_daily":
        isletme = await report.isletme_adi(session)
        stats = await report.gunluk_ozet(session)
        pdf = await report.rapor_gunluk(session, isletme)
        return ProcessResult(
            outcome=ProcessOutcome.REPORT_DAILY, resolved=resolved, report_pdf=pdf, report_stats=stats
        )

    if resolved.kind == "report_general":
        isletme = await report.isletme_adi(session)
        stats = await report.genel_ozet(session)
        pdf = await report.rapor_genel(session, isletme)
        return ProcessResult(
            outcome=ProcessOutcome.REPORT_GENERAL, resolved=resolved, report_pdf=pdf, report_stats=stats
        )

    if resolved.kind in LIST_KINDS:
        rows = await list_persons_with_balance(
            session, scope=_LIST_SCOPE_BY_KIND[resolved.kind], district=resolved.district
        )
        return ProcessResult(outcome=ProcessOutcome.LIST, resolved=resolved, persons=rows)

    if resolved.kind == "search":
        rows = await search_persons(session, resolved.query or "")
        return ProcessResult(outcome=ProcessOutcome.SEARCH, resolved=resolved, persons=rows)

    assert resolved.person is not None

    if resolved.kind == "balance_query":
        bal = await balance_of(session, resolved.person.id)
        txs, total = await list_person_transactions(session, resolved.person.id, limit=BALANCE_TABLE_LIMIT)
        return ProcessResult(
            outcome=ProcessOutcome.BALANCE,
            resolved=resolved,
            balance=bal,
            transactions=txs,
            transactions_total=total,
        )

    if resolved.kind == "report_person":
        isletme = await report.isletme_adi(session)
        pdf = await report.rapor_kisi(session, isletme, resolved.person.id)
        bal = await balance_of(session, resolved.person.id)
        return ProcessResult(
            outcome=ProcessOutcome.REPORT_PERSON, resolved=resolved, balance=bal, report_pdf=pdf
        )

    if resolved.kind == "person_contact":
        return ProcessResult(outcome=ProcessOutcome.PERSON_CONTACT, resolved=resolved)

    if resolved.kind == "info_menu":
        # Belirsiz "bilgi ver": kişi zaten net (READY), hangi bilgi
        # istendiği belirsiz — bot buton ile sorar (CLAUDE.md > "DÜZELTME —
        # 'bilgi ver' belirsiz, SOR"). Seçime göre bakiye/kişi
        # bilgileri/ekstre ayrı callback'lerde üretilir.
        return ProcessResult(outcome=ProcessOutcome.INFO_MENU, resolved=resolved)

    if resolved.kind == "create_person":
        # SADECE kişi oluşturma, borç/tahsilat kaydı YOK (CLAUDE.md > "Bot
        # kayıt akışı — Grup 2"). Kişi "create_person" ile NO_AMOUNT_KINDS
        # üzerinden normal person-resolution akışından geçtiği için burada
        # iki farklı durum aynı outcome'a düşer:
        #   - Kişi zaten mevcutsa (birebir eşleşme) resolve() PERSON_NOT_FOUND'a
        #     hiç gitmeden READY döner: bot bunu "zaten kayıtlı" diye
        #     yorumlar (bkz. app/bot/main.py > _reply_result).
        #   - Kişi yeniyse PERSON_NOT_FOUND -> Evet/Hayır -> adım adım bilgi
        #     toplama akışı sonunda BURAYA, yeni oluşturulmuş kişiyle gelinir
        #     (bkz. _complete_new_person): bot "eklendi" der.
        return ProcessResult(outcome=ProcessOutcome.CREATE_PERSON, resolved=resolved)

    if resolved.kind in ("archive_person", "archive_and_recreate"):
        # Kişi netleşti (READY) ama HENÜZ arşivlenmedi — gerçek arşivleme
        # yazarak onaydan sonra bot tarafında (person_archive.archive_person)
        # yapılır (CLAUDE.md > "Bot kişi silme = arşivleme — Grup 3"). Burada
        # yalnızca onay mesajında gösterilecek bakiye hesaplanır.
        bal = await balance_of(session, resolved.person.id)
        return ProcessResult(outcome=ProcessOutcome.ARCHIVE_CONFIRM, resolved=resolved, balance=bal)

    if resolved.kind == "edit_person":
        # Kişi netleşti (READY) ama HENÜZ hiçbir şey güncellenmedi (CLAUDE.md
        # > "Silme mesajı + kişi düzenleme — Grup 4"). NET komutta (alan VE
        # değer belli, bkz. parser._try_edit_person_net) yalnızca Evet/Hayır
        # onayı kalır; BELİRSİZ komutta (field_name None) bot alan menüsü
        # sorar — gerçek güncelleme bot tarafında (person_edit.update_person_field)
        # yapılır.
        if resolved.field_name is not None and resolved.new_value is not None:
            return ProcessResult(outcome=ProcessOutcome.EDIT_PERSON_CONFIRM, resolved=resolved)
        return ProcessResult(outcome=ProcessOutcome.EDIT_PERSON_MENU, resolved=resolved)

    if source == "llm":
        # Kayıt (borç/tahsilat) niyeti LLM'den geldi: kişi/ürün/tutar net
        # olsa da LLM sonucu düşük güven sayılır, doğrudan kaydetmeden
        # önce kullanıcıdan "bunu mu demek istediniz?" onayı istenir.
        return ProcessResult(outcome=ProcessOutcome.LLM_CONFIRMATION, resolved=resolved)

    # Onay mesajı önceki->güncel bakiyeyi gösterir (CLAUDE.md > "Bot kayıt
    # akışı — Grup 2"): kayıttan ÖNCEKİ bakiye burada, kayıttan hemen sonra.
    balance_before = await balance_of(session, resolved.person.id)
    tx = await record_resolved(session, raw, resolved, text)
    bal = await balance_of(session, resolved.person.id)
    return ProcessResult(
        outcome=ProcessOutcome.RECORDED,
        resolved=resolved,
        transaction_id=tx.id,
        balance=bal,
        balance_before=balance_before,
    )


async def record_resolved(
    session: AsyncSession, raw: RawMessage, resolved: ResolvedIntent, text: str
) -> Transaction:
    """READY durumundaki bir borç/tahsilat niyetini deftere yazar."""
    lines: list[LineInput] = []
    if resolved.product is not None and resolved.qty is not None:
        # Kullanıcının yazdığı tutar esas: line_total veriliyor, birim fiyat
        # ondan türetilir (price_history'ye bakılmaz).
        lines = [
            LineInput(
                product_id=resolved.product.id,
                qty=resolved.qty,
                unit=resolved.unit,
                line_total=resolved.amount,
            )
        ]

    meta = TxMeta(
        created_by=TELEGRAM_ACTOR,
        source=TxSource.TELEGRAM_TEXT,
        raw_text=text,
        trace_id=str(raw.id) if raw.id is not None else None,
    )

    if resolved.kind == "debt":
        amount_override = resolved.amount if not lines else None
        tx = await add_debt(session, resolved.person.id, lines, meta, amount_override=amount_override)
    else:
        tx = await add_payment(session, resolved.person.id, resolved.amount, meta, lines=lines or None)

    raw.processed_at = datetime.now(timezone.utc)
    raw.transaction_id = tx.id
    await session.flush()
    return tx
