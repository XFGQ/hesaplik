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
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawMessage, Transaction, TxSource
from app.services import llm_provider, message_trace, parser, report
from app.services.intent_resolver import LIST_KINDS, ResolutionStatus, ResolvedIntent, resolve
from app.services.ledger import Balance, LineInput, TxMeta, add_debt, add_payment, balance_of
from app.services.queries import (
    PersonBalanceRow,
    PersonTransactionRow,
    TotalBalance,
    list_person_transactions,
    list_persons_with_balance,
    search_persons,
    total_balance,
)

log = logging.getLogger(__name__)

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
    TOTAL_BALANCE = "total_balance"
    REPORT_MENU = "report_menu"
    REPORT_DAILY = "report_daily"
    REPORT_GENERAL = "report_general"
    REPORT_PERSON = "report_person"
    PERSON_CONTACT = "person_contact"
    INFO_MENU = "info_menu"
    ARCHIVE_CONFIRM = "archive_confirm"
    DELETE_AMBIGUOUS = "delete_ambiguous"
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
    total: TotalBalance | None = None  # yalnızca outcome == TOTAL_BALANCE
    transactions: list[PersonTransactionRow] | None = None
    transactions_total: int | None = None
    report_pdf: bytes | None = None
    report_stats: report.DailyStats | report.GeneralStats | None = None


async def process_raw_message(session: AsyncSession, raw: RawMessage, text: str) -> ProcessResult:
    # Parse süresi ölçülür (admin paneli "İşlem Akışı"): regex 15 ms
    # dolayında, LLM saniyeler sürer — hangi mesajların yavaş yola düştüğü
    # ancak ölçülürse görünür.
    started = time.perf_counter()
    intent = parser.parse(text)
    source = "rule"

    if intent is None:
        provider = await llm_provider.get_active_provider(session)
        if provider is not None:
            intent = await provider.parse(text)
            source = "llm"

    parse_ms = int((time.perf_counter() - started) * 1000)
    if intent is None:
        parse_source = message_trace.SOURCE_NONE
    elif source == "llm":
        parse_source = message_trace.SOURCE_LLM
    else:
        parse_source = message_trace.SOURCE_REGEX

    resolved = await resolve(session, intent)
    result = await handle_resolved(
        session, raw, resolved, text, source=source, parse_ms=parse_ms, parse_source=parse_source
    )

    if source == "rule" and result.outcome == ProcessOutcome.SEARCH and not result.persons:
        result = await _retry_empty_search_with_llm(session, raw, text, result, parse_ms)

    return result


async def _retry_empty_search_with_llm(
    session: AsyncSession,
    raw: RawMessage,
    text: str,
    fallback: ProcessResult,
    parse_ms: int | None,
) -> ProcessResult:
    """Kural parser'ın tek kelimelik "arama" yakalayıcısı (parser.py >
    _try_single_word_search) HER eşleşmeyen kelimeyi bir isim/ilçe araması
    sayar — bu da parse() hiçbir zaman None dönmediği için LLM fallback'in
    hiç devreye girmemesine yol açıyordu (bkz. "kişileer", "ahmetbeylilier"
    gibi yazım hataları: kural parser "search" ile "çözdüm" sanıyor).

    Arama SONUÇSUZ kaldığında (kimseyle eşleşmedi) bu düşük güvenli bir
    çözüm sayılır ve LLM'e bir şans daha verilir — belki kelime aslında
    bilinen bir komutun yazım hatasıydı. LLM de bir şey çıkaramazsa
    (None ya da UNRECOGNIZED) orijinal "eşleşen kişi yok" sonucu aynen
    kalır; gerçek (kayıtlı kimseyle eşleşmeyen) bir arama için ekstra bir
    LLM çağrısı dışında hiçbir davranış değişikliği olmaz."""
    provider = await llm_provider.get_active_provider(session)
    if provider is None:
        return fallback

    llm_intent = await provider.parse(text)
    if llm_intent is None:
        return fallback

    llm_resolved = await resolve(session, llm_intent)
    if llm_resolved.status == ResolutionStatus.UNRECOGNIZED:
        return fallback

    return await handle_resolved(
        session, raw, llm_resolved, text,
        source="llm", parse_ms=parse_ms, parse_source=message_trace.SOURCE_LLM,
    )


async def handle_resolved(
    session: AsyncSession,
    raw: RawMessage,
    resolved: ResolvedIntent,
    text: str,
    source: str = "rule",
    parse_ms: int | None = None,
    parse_source: str | None = None,
) -> ProcessResult:
    """Zaten çözülmüş bir niyeti işler. Bot'un onay callback'leri (kişi
    oluşturuldu / aday seçildi / LLM önizlemesi onaylandı) de bu yolu
    tekrar kullanır — bu durumlarda kullanıcı zaten onay verdiği için
    source="rule" (varsayılan) ile çağrılır, doğrudan kaydeder.

    Asıl iş `_dispatch`ta; buradaki sarmalayıcı yalnızca izleme verisini
    (admin paneli "İşlem Akışı") raw_messages'a yazar. İzleme YAN ETKİDİR:
    yazılamazsa akış aynen sürer, sonuç değişmez."""
    trace_source = parse_source or (
        message_trace.SOURCE_LLM if source == "llm" else message_trace.SOURCE_REGEX
    )
    try:
        result = await _dispatch(session, raw, resolved, text, source=source)
    except Exception as exc:
        # Hata izi AYRI bir bağlantıda yazılır: bu session birazdan geri
        # alınacak (hata yukarı gidiyor), aynı session'a yazılan iz de
        # onunla birlikte kaybolurdu.
        await _trace_error_out_of_band(raw, resolved, trace_source, parse_ms, exc, text)
        raise

    message_trace.safe_fill(
        raw,
        resolved=result.resolved,
        outcome=result.outcome.value,
        source=trace_source,
        parse_ms=parse_ms,
        text=text,
    )
    await session.flush()
    return result


async def _trace_error_out_of_band(
    raw: RawMessage,
    resolved: ResolvedIntent,
    trace_source: str,
    parse_ms: int | None,
    exc: Exception,
    text: str,
) -> None:
    """İzleme satırını yeni bir session'da UPDATE eder. Asla patlamaz:
    izleme uğruna asıl hatayı gölgelemek olmaz."""
    raw_id = getattr(raw, "id", None)
    if raw_id is None:
        return

    payload = raw.payload
    stub = RawMessage(payload=payload)
    try:
        message_trace.fill_error(
            stub,
            resolved=resolved,
            source=trace_source,
            parse_ms=parse_ms,
            error=f"{type(exc).__name__}: {exc}",
            text=text,
        )
        from app.db import SessionLocal  # yerel import: modül yüklenirken motor kurulmasın

        async with SessionLocal() as trace_session:
            await trace_session.execute(
                update(RawMessage)
                .where(RawMessage.id == raw_id)
                .values(
                    detected_kind=stub.detected_kind,
                    detected_person=stub.detected_person,
                    detected_amount=stub.detected_amount,
                    detected_product=stub.detected_product,
                    detected_qty=stub.detected_qty,
                    detected_unit=stub.detected_unit,
                    parse_source=stub.parse_source,
                    parse_ms=stub.parse_ms,
                    outcome=stub.outcome,
                    outcome_detail=stub.outcome_detail,
                )
            )
            await trace_session.commit()
    except Exception:
        log.exception("hata izi yazılamadı (raw_message_id=%s)", raw_id)


async def _dispatch(
    session: AsyncSession,
    raw: RawMessage,
    resolved: ResolvedIntent,
    text: str,
    source: str = "rule",
) -> ProcessResult:
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

    if resolved.kind == "total_balance":
        # Defterin TAMAMININ özeti (CLAUDE.md > "Toplam bakiye niyeti") —
        # kişi gerektirmez, salt okunur.
        return ProcessResult(
            outcome=ProcessOutcome.TOTAL_BALANCE, resolved=resolved, total=await total_balance(session)
        )

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

    if resolved.kind == "delete_ambiguous":
        # "furkan duman 20 saman borcunu ödedi sil": silme fiili var ama
        # cümlede para/mal bağlamı da var — niyet belirsiz (CLAUDE.md >
        # "'sil' bağlam ayrımı"). Kişi netleşti ama HİÇBİR ŞEY yapılmaz:
        # bot/web "Tahsilat gir / Kişiyi sil / İptal" diye sorar.
        return ProcessResult(outcome=ProcessOutcome.DELETE_AMBIGUOUS, resolved=resolved)

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


def _actor_for(raw: RawMessage) -> str:
    """Kaydı kim yaptı: web sohbetinde JWT kullanıcı adı (save_web_message
    raw.chat_id'ye yazar — bkz. app/services/web_intake.py), Telegram'da
    her zaman sabit bot aktörü. Yeni parametre eklemek yerine (dosyanın
    geri kalanındaki gibi) raw'dan türetilir."""
    if raw.channel == "web" and raw.chat_id:
        return raw.chat_id
    return TELEGRAM_ACTOR


def _tx_source_for(raw: RawMessage) -> TxSource:
    if raw.voice_transcript:
        return TxSource.TELEGRAM_VOICE
    if raw.channel == "web":
        return TxSource.WEB
    return TxSource.TELEGRAM_TEXT


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
        created_by=_actor_for(raw),
        # raw.voice_transcript/raw.channel yalnızca bu satırdan doğan HER
        # kayıt (ilk parça, kuyruktaki sonraki parça, onay sonrası tamamlanan
        # kayıt fark etmez) için aynı olduğundan ayrıca bir parametre
        # taşımaya gerek yok — hepsi raw'dan türetilir.
        source=_tx_source_for(raw),
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
