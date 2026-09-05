"""Web sohbet orkestrasyonu — app/bot/main.py'nin durumsuz (HTTP) karşılığı.

Telegram botu onay akışlarını (hangi kişi? hangi ürün? LLM onayı mı? kişi
bilgisi adım adım toplama? düzenleme alanı seçimi? yazarak-silme onayı?)
`context.chat_data` içinde bellekte tutar — python-telegram-bot süreç ömrü
boyunca chat başına kalıcı. Web HTTP istekleri arası durumsuz olduğundan aynı
durum burada DB'de tutulur: `web_chat_pending` "bir soruya cevap bekliyorum"
alt-durumunu, `pending_requests` çoklu-mesaj kuyruğunu taşır (bkz.
app/services/web_chat_state.py, app/services/request_queue.py).

Biçimlendirme metinleri ve saf state-geçiş yardımcıları (Telegram nesnesine
bağımlı OLMAYANLAR) app.bot.main'den DOĞRUDAN import edilir — o dosyaya TEK
SATIR dokunulmaz, Telegram botu için sıfır regresyon riski (CLAUDE.md >
"Web'e chat asistanı ekle": "İki mantık OLMAYACAK"). Yalnızca "Telegram
nesnesi gönderen" kabuk (reply_text/InlineKeyboardMarkup/context.chat_data)
burada JSON/DB karşılığıyla yeniden yazılır.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.main import (
    ANLASILAMADI_METNI,
    UNDO_WINDOW_SECONDS,
    _archive_onay_kelimesi,
    _edit_display_value,
    _FIELD_TITLE_NAMES,
    _format_archive_confirm,
    _format_balance,
    _format_daily_report_caption,
    _format_delete_ambiguous,
    _format_edit_confirm,
    _format_edit_field_prompt,
    _format_edit_result,
    _format_general_report_caption,
    _format_list_messages,
    _format_llm_preview,
    _format_person_card,
    _format_close_debt_preview,
    _format_person_report_caption,
    _format_product_query_unsupported,
    _format_product_suggestion,
    _format_record_confirmation,
    _format_running_amount_prompt,
    _format_running_mismatch,
    _format_search_messages,
    _format_total_balance,
    _llm_pending_from_resolved,
    _new_person_prompt,
    _new_person_set_field_and_next,
    _pending_from_resolved,
    _QUERY_ONLY_KINDS,
    _running_ok_label,
    parse_amount_reply,
    _title_tr,
    _turkce_buyuk,
)
from app.models import Person, PendingRequest, Product, RawMessage
from app.schemas import ChatButton, ChatMessage, ChatResponse
from app.services import catalog, message_splitter, parser, person_archive, person_edit, report, request_queue, web_chat_state, web_intake
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent
from app.services.ledger import LedgerError, balance_of
from app.services.ledger import reverse as ledger_reverse
from app.services.message_processor import (
    BALANCE_TABLE_LIMIT,
    ProcessOutcome,
    ProcessResult,
    handle_resolved,
    process_raw_message,
)
from app.services.person_edit import PersonEditError
from app.services.queries import list_person_transactions

# Bot'un ProcessOutcome değerlerine karşılık gelmeyen, yalnızca web tarafında
# üretilen "sentetik" durumlar (buton/onay akışının ara adımları).
OUTCOME_CANCELLED = "cancelled"
OUTCOME_EXPIRED = "expired"
OUTCOME_INFO = "info"
OUTCOME_NEW_PERSON_STEP = "new_person_step"
OUTCOME_EDIT_FIELD_PROMPT = "edit_field_prompt"
OUTCOME_UNDONE = "undone"
OUTCOME_UNKNOWN_ACTION = "unknown_action"

_EDIT_FIELD_ORDER = ["full_name", "phone", "city", "district", "address"]


# --------------------------------------------------------------- serileştirme

def _jsonable(d: dict) -> dict:
    """JSONB'ye yazılabilir hâle getirir — Decimal alanları (qty/amount)
    string'e çevrilir, geri okurken _decimal() ile Decimal'e döner."""
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}


def _decimal(value) -> Decimal | None:
    return Decimal(value) if value is not None else None


# --------------------------------------------------------------- butonlar
# Action string'leri botun callback_data sözlüğüyle AYNI (CLAUDE.md > "Web'e
# chat asistanı ekle") — hem davranış paritesini belgeler hem test
# edilebilirliği kolaylaştırır.

def _yes_no_buttons() -> list[ChatButton]:
    return [ChatButton(label="Evet", action="person:yes"), ChatButton(label="Hayır", action="person:no")]


def _candidates_buttons(candidates: list[Person]) -> list[ChatButton]:
    buttons = [ChatButton(label=p.full_name, action=f"person:pick:{p.id}") for p in candidates]
    buttons.append(ChatButton(label="+ Yeni kişi ekle", action="person:new"))
    return buttons


def _running_fix_buttons(resolved: ResolvedIntent) -> list[ChatButton]:
    """Koşan formatın matematiği tutmuyor (CLAUDE.md > "Koşan format"):
    tek düzeltme önerisi + iptal. Ortadaki sayı doğruysa hangi ucun yanlış
    olduğunu BİLEMEYİZ, o yüzden "25 olsun" diye bir seçenek YOK —
    kullanıcı iptal edip doğru sayılarla yeniden yazar."""
    return [
        ChatButton(label=_running_ok_label(resolved), action="running:fix"),
        ChatButton(label="İptal", action="running:cancel"),
    ]


def _report_menu_buttons() -> list[ChatButton]:
    return [ChatButton(label="Günlük", action="report:daily"), ChatButton(label="Genel", action="report:general")]


def _info_menu_buttons() -> list[ChatButton]:
    return [
        ChatButton(label="Bakiye / borç", action="info:balance"),
        ChatButton(label="Kişi bilgileri", action="info:card"),
        ChatButton(label="Ekstre (PDF)", action="info:report"),
    ]


def _edit_confirm_buttons() -> list[ChatButton]:
    return [ChatButton(label="Evet", action="edit:yes"), ChatButton(label="Hayır", action="edit:no")]


def _edit_field_buttons() -> list[ChatButton]:
    return [ChatButton(label=_FIELD_TITLE_NAMES[f], action=f"editfield:{f}") for f in _EDIT_FIELD_ORDER]


def _delete_ambiguous_buttons() -> list[ChatButton]:
    return [
        ChatButton(label="Tahsilat gir", action="delete:payment"),
        ChatButton(label="Kişiyi sil", action="delete:person"),
        ChatButton(label="İptal", action="delete:cancel"),
    ]


def _product_confirm_buttons() -> list[ChatButton]:
    return [
        ChatButton(label="Evet", action="product:yes"),
        ChatButton(label="Hayır, yeni ürün", action="product:new"),
        ChatButton(label="İptal", action="product:cancel"),
    ]


def _llm_confirm_buttons() -> list[ChatButton]:
    return [
        ChatButton(label="Evet", action="llm:yes"),
        ChatButton(label="Düzelt", action="llm:fix"),
        ChatButton(label="İptal", action="llm:cancel"),
    ]


def _undo_buttons(tx_id: int) -> list[ChatButton]:
    return [ChatButton(label="↩ Geri al", action=f"undo:{tx_id}")]


def _new_person_name_buttons() -> list[ChatButton]:
    return [
        ChatButton(label="Onayla", action="newperson:confirm"),
        ChatButton(label="Düzelt", action="newperson:edit"),
        ChatButton(label="Hepsini geç", action="newperson:skip_all"),
        ChatButton(label="İptal", action="newperson:cancel"),
    ]


def _new_person_optional_buttons() -> list[ChatButton]:
    return [ChatButton(label="Geç", action="newperson:skip"), ChatButton(label="İptal", action="newperson:cancel")]


def _new_person_prompt_and_buttons(step: str, name: str = "") -> tuple[str, list[ChatButton]]:
    text, _kb = _new_person_prompt(step, name)  # keyboard (Telegram-specific) yok sayılır, metin aynen kullanılır
    return text, (_new_person_name_buttons() if step == "name" else _new_person_optional_buttons())


def _web_balance_text(person: Person, bal, txs, total) -> str:
    """_format_balance Telegram HTML'i (<pre>...</pre>) döner — web'de düz
    metin yeterli (monospace CSS ile gösterilir), sarmalayıcı soyulur."""
    raw = _format_balance(person, bal, txs, total)
    inner = raw.removeprefix("📋 <pre>").removesuffix("</pre>")
    return "📋 " + html.unescape(inner)


def _undo_expired(undo: dict | None) -> bool:
    if not undo:
        return True
    try:
        expires_at = datetime.fromisoformat(undo["expires_at"])
    except (KeyError, ValueError, TypeError):
        return True
    return datetime.now(timezone.utc) >= expires_at


async def _current_request_id(session: AsyncSession, chat_id: str) -> int | None:
    """En fazla bir satır aynı anda 'isleniyor' olur (bkz. _advance_all) —
    bu yüzden payload'lara ayrıca request_id taşımaya gerek yok."""
    stmt = select(PendingRequest.id).where(
        PendingRequest.chat_id == chat_id, PendingRequest.durum == request_queue.ISLENIYOR
    )
    return (await session.execute(stmt)).scalars().first()


# --------------------------------------------------------------- ortak sonuç uygulama
#
# BALANCE/PERSON_CONTACT/CREATE_PERSON/INFO_MENU/ARCHIVE_CONFIRM/
# EDIT_PERSON_CONFIRM/EDIT_PERSON_MENU/PRODUCT_NEEDS_CONFIRMATION/
# REPORT_PERSON/RECORDED — hem TAZE bir mesajdan (_build_fresh_reply) hem
# bir onaydan sonra ZİNCİRLEME gelen bir sonuçtan (kişi seçildi, yeni kişi
# oluşturuldu, LLM onaylandı) ortaya çıkabilir; botun _reply_outcome/
# _finish_pending/_complete_new_person'daki AYNI (üç kez tekrarlanan) if/elif
# zincirinin TEK paylaşılan hâli.

async def _apply_result(
    session: AsyncSession,
    chat_id: str,
    result: ProcessResult,
    *,
    raw_message_id: int,
    raw_text: str,
    just_created: bool = False,
) -> tuple[ChatMessage, bool]:
    resolved = result.resolved
    outcome = result.outcome

    if outcome == ProcessOutcome.BALANCE:
        txt = _web_balance_text(resolved.person, result.balance, result.transactions or [], result.transactions_total or 0)
        return ChatMessage(reply=txt, outcome=outcome.value), False

    if outcome == ProcessOutcome.PERSON_CONTACT:
        return ChatMessage(reply=_format_person_card(resolved.person), outcome=outcome.value), False

    if outcome == ProcessOutcome.CREATE_PERSON:
        if just_created:
            txt = f"✅ {resolved.person.full_name} eklendi."
        else:
            txt = f"ℹ️ {resolved.person.full_name} zaten kayıtlı."
        return ChatMessage(reply=txt, outcome=outcome.value), False

    if outcome == ProcessOutcome.INFO_MENU:
        await web_chat_state.set_pending(session, chat_id, "info_menu", {"person_id": resolved.person.id})
        return ChatMessage(reply="Ne bilgisi?", outcome=outcome.value, buttons=_info_menu_buttons()), True

    if outcome == ProcessOutcome.ARCHIVE_CONFIRM:
        assert result.balance is not None
        onay = await _archive_onay_kelimesi(session)
        await web_chat_state.set_pending(
            session, chat_id, "archive_confirm",
            {"person_id": resolved.person.id, "kind": resolved.kind, "onay_kelimesi": onay},
        )
        txt = _format_archive_confirm(resolved.person.full_name, result.balance, onay)
        return ChatMessage(reply=txt, outcome=outcome.value, awaits_text=True), True

    if outcome == ProcessOutcome.DELETE_AMBIGUOUS:
        # "sil" + para/mal bağlamı: niyet belirsiz, HİÇBİR ŞEY yapılmadan
        # sorulur (CLAUDE.md > "'sil' bağlam ayrımı"). Silme fiili ayıklanmış
        # metin şimdi saklanır — "Tahsilat gir" seçilirse normal akıştan
        # yeniden geçirilir.
        await web_chat_state.set_pending(
            session, chat_id, "delete_ambiguous",
            {
                "person_id": resolved.person.id,
                "raw_message_id": raw_message_id,
                "text": parser.strip_delete_words(raw_text),
            },
        )
        return (
            ChatMessage(
                reply=_format_delete_ambiguous(resolved.person.full_name),
                outcome=outcome.value,
                buttons=_delete_ambiguous_buttons(),
            ),
            True,
        )

    if outcome == ProcessOutcome.EDIT_PERSON_CONFIRM:
        value = _edit_display_value(resolved.field_name, resolved.new_value)
        await web_chat_state.set_pending(
            session, chat_id, "edit_confirm",
            {"person_id": resolved.person.id, "field": resolved.field_name, "value": value},
        )
        txt = _format_edit_confirm(resolved.person.full_name, resolved.field_name, value)
        return ChatMessage(reply=txt, outcome=outcome.value, buttons=_edit_confirm_buttons()), True

    if outcome == ProcessOutcome.EDIT_PERSON_MENU:
        await web_chat_state.set_pending(
            session, chat_id, "edit_field_flow",
            {"person_id": resolved.person.id, "person_name": resolved.person.full_name},
        )
        return ChatMessage(reply="Hangi bilgiyi düzenlemek istersin?", outcome=outcome.value, buttons=_edit_field_buttons()), True

    if outcome == ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION:
        await web_chat_state.set_pending(
            session, chat_id, "product_confirm",
            _jsonable({
                "kind": resolved.kind,
                "person_id": resolved.person.id,
                "qty": resolved.qty,
                "unit": resolved.unit,
                "product_name_raw": resolved.product_name_raw,
                "suggestion_id": resolved.product_suggestion.id,
                "amount": resolved.amount,
                "raw_message_id": raw_message_id,
                "raw_text": raw_text,
                "running": resolved.running,
            }),
        )
        txt = _format_product_suggestion(resolved.product_name_raw, resolved.product_suggestion.name)
        return ChatMessage(reply=txt, outcome=outcome.value, buttons=_product_confirm_buttons()), True

    if outcome == ProcessOutcome.RUNNING_AMOUNT_NEEDED:
        # Koşan format YALNIZCA adedi söyler; TL ayrı girilir (CLAUDE.md >
        # "Koşan format"). Kişi/ürün çözülmüş hâlde bekletilir, tutar
        # yazarak sorulur — uydurulmaz.
        payload = _jsonable(_llm_pending_from_resolved(resolved, raw_message_id, raw_text))
        await web_chat_state.set_pending(session, chat_id, "running_amount", payload)
        return (
            ChatMessage(
                reply=_format_running_amount_prompt(resolved),
                outcome=outcome.value,
                awaits_text=True,
            ),
            True,
        )

    if outcome == ProcessOutcome.REPORT_PERSON:
        assert result.balance is not None
        txt = _format_person_report_caption(resolved.person, result.balance)
        return ChatMessage(reply=txt, outcome=outcome.value, report_path=f"/reports/person/{resolved.person.id}"), False

    # RECORDED — kural parser'la ya da bir onaydan sonra doğrudan kaydedilmiş.
    txt = _format_record_confirmation(resolved, result.balance_before, result.balance)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=UNDO_WINDOW_SECONDS)).isoformat()
    await web_chat_state.set_undo(session, chat_id, result.transaction_id, expires_at)
    return ChatMessage(reply=txt, outcome=outcome.value, buttons=_undo_buttons(result.transaction_id)), False


async def _build_fresh_reply(
    session: AsyncSession, chat_id: str, result: ProcessResult, raw: RawMessage, text: str, is_multi: bool
) -> tuple[ChatMessage, bool]:
    """Botun _reply_outcome'unun JSON karşılığı: TAZE bir process_raw_message
    sonucunu (henüz hiçbir onaydan geçmemiş) yorumlar. NEEDS_CONFIRMATION/
    PERSON_NOT_FOUND/LIST/SEARCH/REPORT_MENU/REPORT_DAILY/REPORT_GENERAL/
    LLM_CONFIRMATION yalnızca BURADAN gelir (zincirleme onay sonrası asla
    tekrar üretilmez); geri kalan tüm outcome'lar _apply_result'a devredilir."""
    resolved = result.resolved
    outcome = result.outcome

    if outcome == ProcessOutcome.UNRECOGNIZED:
        reply = f"Şu kısmı anlayamadım: '{text}'" if is_multi else ANLASILAMADI_METNI
        return ChatMessage(reply=reply, outcome=outcome.value), False

    if outcome == ProcessOutcome.NEEDS_CONFIRMATION:
        payload = _jsonable(_pending_from_resolved(resolved, raw.id, text))
        await web_chat_state.set_pending(session, chat_id, "pending", payload)
        return (
            ChatMessage(reply="Hangisini demek istedin?", outcome=outcome.value, buttons=_candidates_buttons(resolved.person_candidates)),
            True,
        )

    if outcome == ProcessOutcome.PERSON_NOT_FOUND:
        isim = _title_tr(resolved.person_name_raw or "")
        if resolved.kind in _QUERY_ONLY_KINDS:
            return ChatMessage(reply=f"{isim} defterde yok.", outcome=outcome.value), False
        payload = _jsonable(_pending_from_resolved(resolved, raw.id, text))
        await web_chat_state.set_pending(session, chat_id, "pending", payload)
        return (
            ChatMessage(reply=f"{isim} defterde yok. Ekleyeyim mi?", outcome=outcome.value, buttons=_yes_no_buttons()),
            True,
        )

    if outcome == ProcessOutcome.LIST:
        reply = "\n\n".join(_format_list_messages(resolved.kind, resolved.district, result.persons or []))
        return ChatMessage(reply=reply, outcome=outcome.value), False

    if outcome == ProcessOutcome.SEARCH:
        reply = "\n\n".join(_format_search_messages(resolved.query, result.persons or []))
        return ChatMessage(reply=reply, outcome=outcome.value), False

    if outcome == ProcessOutcome.TOTAL_BALANCE:
        assert result.total is not None
        return ChatMessage(reply=_format_total_balance(result.total), outcome=outcome.value), False

    if outcome == ProcessOutcome.REPORT_MENU:
        await web_chat_state.set_pending(session, chat_id, "report_menu", {})
        return ChatMessage(reply="Hangi raporu istersin?", outcome=outcome.value, buttons=_report_menu_buttons()), True

    if outcome == ProcessOutcome.REPORT_DAILY:
        assert result.report_stats is not None
        return ChatMessage(reply=_format_daily_report_caption(result.report_stats), outcome=outcome.value, report_path="/reports/daily"), False

    if outcome == ProcessOutcome.REPORT_GENERAL:
        assert result.report_stats is not None
        return ChatMessage(reply=_format_general_report_caption(result.report_stats), outcome=outcome.value, report_path="/reports/general"), False

    if outcome == ProcessOutcome.LLM_CONFIRMATION:
        payload = _jsonable(_llm_pending_from_resolved(resolved, raw.id, text))
        await web_chat_state.set_pending(session, chat_id, "llm_confirm", payload)
        return ChatMessage(reply=_format_llm_preview(resolved), outcome=outcome.value, buttons=_llm_confirm_buttons()), True

    if outcome == ProcessOutcome.CLOSE_DEBT_CONFIRM:
        # "ali borcunu ödedi": tutar söylenmemiş, güncel bakiye teklif
        # ediliyor. Bekleyen kayıt ve butonlar LLM önizlemesiyle AYNI
        # ("llm_confirm"), yalnızca sorulan cümle farklı.
        assert result.balance is not None
        payload = _jsonable(_llm_pending_from_resolved(resolved, raw.id, text))
        await web_chat_state.set_pending(session, chat_id, "llm_confirm", payload)
        return (
            ChatMessage(
                reply=_format_close_debt_preview(resolved, result.balance),
                outcome=outcome.value,
                buttons=_llm_confirm_buttons(),
            ),
            True,
        )

    if outcome == ProcessOutcome.RUNNING_MISMATCH:
        # Koşan üçlünün matematiği tutmuyor: hiçbir şey kaydedilmedi, kişi
        # bile çözülmedi. "Fark N olsun" denirse cümle düzeltilip NORMAL
        # akıştan yeniden geçirilir (bkz. _handle_running_fix).
        await web_chat_state.set_pending(
            session, chat_id, "running_fix",
            {"raw_message_id": raw.id, "raw_text": text},
        )
        return (
            ChatMessage(
                reply=_format_running_mismatch(resolved),
                outcome=outcome.value,
                buttons=_running_fix_buttons(resolved),
            ),
            True,
        )

    if outcome == ProcessOutcome.PRODUCT_QUERY_UNSUPPORTED:
        return ChatMessage(reply=_format_product_query_unsupported(resolved), outcome=outcome.value), False

    return await _apply_result(session, chat_id, result, raw_message_id=raw.id, raw_text=text)


# --------------------------------------------------------------- kuyruk ilerletme

async def _advance_all(session: AsyncSession, chat_id: str, is_multi: bool) -> list[ChatMessage]:
    """Botun _advance_queue'suyla aynı mantık: bekleyen bir sonraki parça
    varsa işler; onay gerektirmeyen parçalar zincirleme (chained) devam eder,
    biri onay isteyince durur (satır 'isleniyor' kalır, _current_request_id
    onu bulur)."""
    messages: list[ChatMessage] = []
    while True:
        req = await request_queue.next_pending(session, chat_id)
        if req is None:
            break
        await request_queue.mark(session, req.id, request_queue.ISLENIYOR)
        raw = await session.get(RawMessage, req.raw_message_id)
        result = await process_raw_message(session, raw, req.raw_text)
        msg, needs_followup = await _build_fresh_reply(session, chat_id, result, raw, req.raw_text, is_multi)
        messages.append(msg)
        if needs_followup:
            break
        await request_queue.mark(session, req.id, request_queue.TAMAMLANDI, sonuc=msg.outcome[:200])
    return messages


async def _conclude(
    session: AsyncSession, chat_id: str, msg: ChatMessage, needs_followup: bool, durum: str | None = None
) -> list[ChatMessage]:
    """Bir onay/seçim TAM bittiyse (needs_followup=False) o parçayı kuyrukta
    tamamlanmış işaretler ve kuyruktaki bir sonraki parçaya otomatik geçer
    (botun _advance_queue çağrılarıyla aynı an) — zincirleme yeni bir onay
    başlattıysa (needs_followup=True) kuyruk İLERLETİLMEZ."""
    if needs_followup:
        return [msg]
    req_id = await _current_request_id(session, chat_id)
    if req_id is not None:
        await request_queue.mark(session, req_id, durum or request_queue.TAMAMLANDI, sonuc=msg.outcome[:200])
    return [msg] + await _advance_all(session, chat_id, is_multi=True)


# --------------------------------------------------------------- aday seçimi / yeni kişi zinciri

async def _resolve_and_process(
    session: AsyncSession, person: Person, pending_data: dict
) -> tuple[ResolvedIntent, ProcessResult]:
    resolved = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind=pending_data["kind"],
        person=person,
        qty=_decimal(pending_data.get("qty")),
        unit=pending_data.get("unit"),
        product_name_raw=pending_data.get("product_name"),
        amount=_decimal(pending_data.get("amount")),
        field_name=pending_data.get("field"),
        new_value=pending_data.get("new_value"),
        running=pending_data.get("running", False),
    )
    if pending_data.get("product_name") and pending_data["kind"] != "balance_query":
        product, suggestion = await catalog.resolve_product_or_suggest(
            session, pending_data["product_name"], pending_data.get("unit")
        )
        if suggestion is not None:
            resolved.status = ResolutionStatus.PRODUCT_NEEDS_CONFIRMATION
            resolved.product_suggestion = suggestion
            return resolved, ProcessResult(outcome=ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION, resolved=resolved)
        resolved.product = product

    raw = await session.get(RawMessage, pending_data["raw_message_id"])
    result = await handle_resolved(session, raw, resolved, pending_data["raw_text"])
    return resolved, result


async def _finish_pending(
    session: AsyncSession, chat_id: str, person: Person, pending_data: dict, *, just_created: bool = False
) -> tuple[ChatMessage, bool]:
    resolved, result = await _resolve_and_process(session, person, pending_data)
    return await _apply_result(
        session, chat_id, result,
        raw_message_id=pending_data["raw_message_id"], raw_text=pending_data["raw_text"],
        just_created=just_created,
    )


async def _handle_person_pick(session: AsyncSession, chat_id: str, pending, person_id: int) -> list[ChatMessage]:
    if pending.kind != "pending" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    pending_data = dict(pending.payload)
    await web_chat_state.clear_pending(session, chat_id)

    person = await session.get(Person, person_id)
    if person is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)

    msg, needs_followup = await _finish_pending(session, chat_id, person, pending_data)
    return await _conclude(session, chat_id, msg, needs_followup)


async def _begin_new_person_flow(session: AsyncSession, chat_id: str, pending) -> ChatMessage:
    if pending.kind != "pending" or not pending.payload:
        return ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)
    data = dict(pending.payload)
    name = _title_tr(data.get("person_name_raw") or "")
    flow = {"step": "name", "name": name, "phone": None, "city": None, "district": None, "pending": data}
    await web_chat_state.set_pending(session, chat_id, "new_person_flow", flow)
    text, buttons = _new_person_prompt_and_buttons("name", name)
    return ChatMessage(reply=text, outcome=OUTCOME_NEW_PERSON_STEP, buttons=buttons, awaits_text=True)


async def _new_person_confirm_name(session: AsyncSession, chat_id: str, pending) -> ChatMessage:
    if pending.kind != "new_person_flow" or not pending.payload:
        return ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)
    flow = dict(pending.payload)
    flow["step"] = "phone"
    await web_chat_state.set_pending(session, chat_id, "new_person_flow", flow)
    text, buttons = _new_person_prompt_and_buttons("phone")
    return ChatMessage(reply=text, outcome=OUTCOME_NEW_PERSON_STEP, buttons=buttons, awaits_text=True)


async def _new_person_edit_name(session: AsyncSession, chat_id: str, pending) -> ChatMessage:
    if pending.kind != "new_person_flow" or not pending.payload:
        return ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)
    return ChatMessage(reply="Ad soyadı yazar mısın?", outcome=OUTCOME_NEW_PERSON_STEP, awaits_text=True)


async def _new_person_skip_all(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    if pending.kind != "new_person_flow" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    return await _complete_new_person(session, chat_id, dict(pending.payload))


async def _new_person_skip_field(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    if pending.kind != "new_person_flow" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    flow = dict(pending.payload)
    next_step = _new_person_set_field_and_next(flow, None)
    if next_step is None:
        return await _complete_new_person(session, chat_id, flow)
    await web_chat_state.set_pending(session, chat_id, "new_person_flow", flow)
    text, buttons = _new_person_prompt_and_buttons(next_step)
    return [ChatMessage(reply=text, outcome=OUTCOME_NEW_PERSON_STEP, buttons=buttons, awaits_text=True)]


async def _new_person_cancel(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    await web_chat_state.clear_pending(session, chat_id)
    msg = ChatMessage(reply="Kişi ekleme iptal edildi.", outcome=OUTCOME_CANCELLED)
    return await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL)


async def _complete_new_person(session: AsyncSession, chat_id: str, flow: dict) -> list[ChatMessage]:
    pending_data = flow.get("pending") or {}
    await web_chat_state.clear_pending(session, chat_id)

    person = Person(full_name=flow["name"], phone=flow.get("phone"), city=flow.get("city"), district=flow.get("district"))
    session.add(person)
    await session.flush()

    msg, needs_followup = await _finish_pending(session, chat_id, person, pending_data, just_created=True)
    return await _conclude(session, chat_id, msg, needs_followup)


async def _new_person_text(session: AsyncSession, chat_id: str, pending, text: str) -> list[ChatMessage]:
    flow = dict(pending.payload or {})
    step = flow.get("step")
    text = (text or "").strip()

    if step == "name":
        if not text:
            return [ChatMessage(reply="Ad soyad boş olamaz, yazar mısın?", outcome=OUTCOME_NEW_PERSON_STEP, awaits_text=True)]
        value: str | None = _title_tr(text)
    else:
        value = text or None

    next_step = _new_person_set_field_and_next(flow, value)
    if next_step is None:
        return await _complete_new_person(session, chat_id, flow)

    await web_chat_state.set_pending(session, chat_id, "new_person_flow", flow)
    prompt, buttons = _new_person_prompt_and_buttons(next_step, flow.get("name", ""))
    return [ChatMessage(reply=prompt, outcome=OUTCOME_NEW_PERSON_STEP, buttons=buttons, awaits_text=True)]


# --------------------------------------------------------------- ürün fuzzy onayı

async def _handle_product_confirm(session: AsyncSession, chat_id: str, pending, use_suggestion: bool) -> list[ChatMessage]:
    if pending.kind != "product_confirm" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    payload = dict(pending.payload)
    await web_chat_state.clear_pending(session, chat_id)

    person = await session.get(Person, payload["person_id"])
    if person is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)

    if use_suggestion:
        product = await session.get(Product, payload["suggestion_id"])
    else:
        product, _created = await catalog.resolve_or_create(session, payload["product_name_raw"], payload.get("unit"))

    resolved = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind=payload["kind"],
        person=person,
        qty=_decimal(payload.get("qty")),
        unit=payload.get("unit"),
        product_name_raw=payload.get("product_name_raw"),
        product=product,
        amount=_decimal(payload.get("amount")),
        running=payload.get("running", False),
    )
    raw = await session.get(RawMessage, payload["raw_message_id"])
    result = await handle_resolved(session, raw, resolved, payload["raw_text"], source="rule")

    msg, needs_followup = await _apply_result(
        session, chat_id, result, raw_message_id=payload["raw_message_id"], raw_text=payload["raw_text"]
    )
    return await _conclude(session, chat_id, msg, needs_followup)


# --------------------------------------------------------------- koşan format
#
# Botun _handle_running_fix / _handle_running_amount_text karşılığı; mantık
# aynı, yalnızca durum chat_data yerine web_chat_pending'de (CLAUDE.md >
# "Koşan format").


async def _handle_running_fix(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    """"Fark N olsun": orta sayı |ilk - son|'a eşitlenmiş metin NORMAL
    akıştan yeniden geçirilir — ikinci bir kayıt mantığı yazılmaz."""
    if pending.kind != "running_fix" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    payload = dict(pending.payload)
    await web_chat_state.clear_pending(session, chat_id)

    duzeltilmis = parser.correct_running_text(payload["raw_text"])
    if duzeltilmis is None:
        msg = ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)

    raw = await session.get(RawMessage, payload["raw_message_id"])
    result = await process_raw_message(session, raw, duzeltilmis)
    msg, needs_followup = await _build_fresh_reply(
        session, chat_id, result, raw, duzeltilmis, is_multi=False
    )
    return await _conclude(session, chat_id, msg, needs_followup)


async def _running_amount_text(session: AsyncSession, chat_id: str, pending, text: str) -> list[ChatMessage]:
    """Tutar cevabı. Çözülemezse HİÇBİR ŞEY kaydedilmez ve bekleyen kayıt
    yerinde kalır — soru tekrarlanır."""
    amount = parse_amount_reply(text)
    if amount is None:
        return [
            ChatMessage(
                reply="Tutarı anlayamadım. Sadece rakamla yazar mısın? (örn. 5000)",
                outcome=ProcessOutcome.RUNNING_AMOUNT_NEEDED.value,
                awaits_text=True,
            )
        ]

    payload = dict(pending.payload or {})
    await web_chat_state.clear_pending(session, chat_id)

    person = await session.get(Person, payload["person_id"])
    if person is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)
    product = await session.get(Product, payload["product_id"]) if payload.get("product_id") else None

    resolved = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind=payload["kind"],
        person=person,
        qty=_decimal(payload.get("qty")),
        unit=payload.get("unit"),
        product=product,
        amount=amount,
    )
    raw = await session.get(RawMessage, payload["raw_message_id"])
    result = await handle_resolved(session, raw, resolved, payload["raw_text"], source="rule")

    msg, needs_followup = await _apply_result(
        session, chat_id, result, raw_message_id=payload["raw_message_id"], raw_text=payload["raw_text"]
    )
    return await _conclude(session, chat_id, msg, needs_followup)


# --------------------------------------------------------------- LLM önizleme onayı

async def _handle_llm_confirm_yes(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    if pending.kind != "llm_confirm" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    payload = dict(pending.payload)
    await web_chat_state.clear_pending(session, chat_id)

    person = await session.get(Person, payload["person_id"])
    if person is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)
    product = await session.get(Product, payload["product_id"]) if payload.get("product_id") else None

    resolved = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind=payload["kind"],
        person=person,
        qty=_decimal(payload.get("qty")),
        unit=payload.get("unit"),
        product=product,
        amount=_decimal(payload.get("amount")),
    )
    raw = await session.get(RawMessage, payload["raw_message_id"])
    result = await handle_resolved(session, raw, resolved, payload["raw_text"], source="rule")

    msg, needs_followup = await _apply_result(
        session, chat_id, result, raw_message_id=payload["raw_message_id"], raw_text=payload["raw_text"]
    )
    return await _conclude(session, chat_id, msg, needs_followup)


# --------------------------------------------------------------- kişi düzenleme

async def _apply_edit_person_field(session: AsyncSession, person_id: int, field: str, value: str, actor: str) -> str | None:
    try:
        person = await person_edit.update_person_field(session, person_id, field, value, actor=actor)
    except PersonEditError:
        return None
    return person.full_name


async def _handle_edit_field_pick(session: AsyncSession, chat_id: str, pending, field: str) -> ChatMessage:
    if pending.kind != "edit_field_flow" or not pending.payload:
        return ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)
    payload = dict(pending.payload)
    person = await session.get(Person, payload["person_id"])
    if person is None:
        await web_chat_state.clear_pending(session, chat_id)
        return ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
    payload["field"] = field
    await web_chat_state.set_pending(session, chat_id, "edit_field_flow", payload)
    return ChatMessage(reply=_format_edit_field_prompt(field, getattr(person, field)), outcome=OUTCOME_EDIT_FIELD_PROMPT, awaits_text=True)


async def _edit_field_value_text(session: AsyncSession, chat_id: str, pending, text: str) -> list[ChatMessage]:
    payload = dict(pending.payload or {})
    value = (text or "").strip()
    if not value:
        return [ChatMessage(reply="Değer boş olamaz, tekrar yazar mısın?", outcome=OUTCOME_EDIT_FIELD_PROMPT, awaits_text=True)]
    await web_chat_state.clear_pending(session, chat_id)

    name = await _apply_edit_person_field(session, payload["person_id"], payload["field"], value, actor=chat_id)
    if name is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
    else:
        msg = ChatMessage(reply=_format_edit_result(name, payload["field"], value), outcome=ProcessOutcome.EDIT_PERSON_CONFIRM.value)
    return await _conclude(session, chat_id, msg, False)


async def _handle_edit_confirm_yes(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    if pending.kind != "edit_confirm" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    payload = dict(pending.payload)
    await web_chat_state.clear_pending(session, chat_id)

    name = await _apply_edit_person_field(session, payload["person_id"], payload["field"], payload["value"], actor=chat_id)
    if name is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
    else:
        msg = ChatMessage(reply=_format_edit_result(name, payload["field"], payload["value"]), outcome=ProcessOutcome.EDIT_PERSON_CONFIRM.value)
    return await _conclude(session, chat_id, msg, False)


# --------------------------------------------------------------- kişi silme = arşivleme

async def _archive_confirm_text(session: AsyncSession, chat_id: str, pending, text: str) -> list[ChatMessage]:
    payload = dict(pending.payload or {})
    await web_chat_state.clear_pending(session, chat_id)

    if _turkce_buyuk((text or "").strip()) != payload.get("onay_kelimesi"):
        msg = ChatMessage(reply="İşlem iptal edildi.", outcome=OUTCOME_CANCELLED)
        return await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL)

    person = await session.get(Person, payload["person_id"])
    if person is None or not person.is_active:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)

    name = person.full_name
    await person_archive.archive_person(session, person.id, archived_by=chat_id, reason="Web: kullanıcı isteğiyle arşivlendi")

    if payload.get("kind") == "archive_and_recreate":
        session.add(Person(full_name=name))
        txt = f"{name} silindi, temiz hesap açıldı."
    else:
        txt = f"{name} silindi."
    msg = ChatMessage(reply=txt, outcome=ProcessOutcome.ARCHIVE_CONFIRM.value)
    return await _conclude(session, chat_id, msg, False)


# --------------------------------------------------------------- "sil" belirsizliği

async def _handle_delete_ambiguous_person(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    """"Kişiyi sil" seçildi: normal silme akışına (yazarak onay) girilir —
    kısayol YOK (CLAUDE.md > "Yazarak onay her silmede")."""
    if pending.kind != "delete_ambiguous" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    payload = dict(pending.payload)
    await web_chat_state.clear_pending(session, chat_id)

    person = await session.get(Person, payload["person_id"])
    if person is None or not person.is_active:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)

    bal = await balance_of(session, person.id)
    resolved = ResolvedIntent(status=ResolutionStatus.READY, kind="archive_person", person=person)
    result = ProcessResult(outcome=ProcessOutcome.ARCHIVE_CONFIRM, resolved=resolved, balance=bal)
    msg, needs_followup = await _apply_result(
        session, chat_id, result,
        raw_message_id=payload["raw_message_id"], raw_text=payload["text"],
    )
    return await _conclude(session, chat_id, msg, needs_followup)


async def _handle_delete_ambiguous_payment(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    """"Tahsilat gir" seçildi: silme fiili ayıklanmış metin NORMAL akıştan
    (kural parser -> gerekirse LLM) yeniden geçirilir — ikinci bir mantık
    yazılmaz."""
    if pending.kind != "delete_ambiguous" or not pending.payload:
        return [ChatMessage(reply="Bu istek artık geçerli değil.", outcome=OUTCOME_EXPIRED)]
    payload = dict(pending.payload)
    await web_chat_state.clear_pending(session, chat_id)

    raw = await session.get(RawMessage, payload["raw_message_id"])
    text = payload["text"]
    result = await process_raw_message(session, raw, text)
    msg, needs_followup = await _build_fresh_reply(session, chat_id, result, raw, text, is_multi=False)
    return await _conclude(session, chat_id, msg, needs_followup)


# --------------------------------------------------------------- geri al / rapor / bilgi menüsü

async def _handle_undo(session: AsyncSession, chat_id: str, tx_id: int) -> ChatMessage:
    pending = await web_chat_state.load(session, chat_id)
    undo = pending.undo
    if not undo or undo.get("tx_id") != tx_id or _undo_expired(undo):
        return ChatMessage(reply="Bu işlemin geri alma süresi geçti.", outcome=OUTCOME_EXPIRED)
    try:
        await ledger_reverse(session, tx_id, actor=chat_id, reason="Web: geri al")
    except LedgerError as e:
        return ChatMessage(reply=f"Geri alınamadı: {e}", outcome=OUTCOME_EXPIRED)
    await web_chat_state.clear_undo(session, chat_id)
    return ChatMessage(reply="Kayıt geri alındı.", outcome=OUTCOME_UNDONE)


async def _handle_report_daily(session: AsyncSession, chat_id: str) -> list[ChatMessage]:
    await web_chat_state.clear_pending(session, chat_id)
    stats = await report.gunluk_ozet(session)
    msg = ChatMessage(reply=_format_daily_report_caption(stats), outcome=ProcessOutcome.REPORT_DAILY.value, report_path="/reports/daily")
    return await _conclude(session, chat_id, msg, False)


async def _handle_report_general(session: AsyncSession, chat_id: str) -> list[ChatMessage]:
    await web_chat_state.clear_pending(session, chat_id)
    stats = await report.genel_ozet(session)
    msg = ChatMessage(reply=_format_general_report_caption(stats), outcome=ProcessOutcome.REPORT_GENERAL.value, report_path="/reports/general")
    return await _conclude(session, chat_id, msg, False)


async def _handle_info_balance(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    payload = dict(pending.payload or {})
    await web_chat_state.clear_pending(session, chat_id)
    person = await session.get(Person, payload["person_id"]) if payload.get("person_id") else None
    if person is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)
    bal = await balance_of(session, person.id)
    txs, total = await list_person_transactions(session, person.id, limit=BALANCE_TABLE_LIMIT)
    msg = ChatMessage(reply=_web_balance_text(person, bal, txs, total), outcome=ProcessOutcome.BALANCE.value)
    return await _conclude(session, chat_id, msg, False)


async def _handle_info_card(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    payload = dict(pending.payload or {})
    await web_chat_state.clear_pending(session, chat_id)
    person = await session.get(Person, payload["person_id"]) if payload.get("person_id") else None
    if person is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)
    msg = ChatMessage(reply=_format_person_card(person), outcome=ProcessOutcome.PERSON_CONTACT.value)
    return await _conclude(session, chat_id, msg, False)


async def _handle_info_report(session: AsyncSession, chat_id: str, pending) -> list[ChatMessage]:
    payload = dict(pending.payload or {})
    await web_chat_state.clear_pending(session, chat_id)
    person = await session.get(Person, payload["person_id"]) if payload.get("person_id") else None
    if person is None:
        msg = ChatMessage(reply="Kişi bulunamadı.", outcome=OUTCOME_EXPIRED)
        return await _conclude(session, chat_id, msg, False)
    bal = await balance_of(session, person.id)
    msg = ChatMessage(
        reply=_format_person_report_caption(person, bal),
        outcome=ProcessOutcome.REPORT_PERSON.value,
        report_path=f"/reports/person/{person.id}",
    )
    return await _conclude(session, chat_id, msg, False)


# --------------------------------------------------------------- genel giriş noktaları

async def handle_text(session: AsyncSession, chat_id: str, text: str) -> ChatResponse:
    """POST /api/chat — botun on_text'inin web karşılığı."""
    pending = await web_chat_state.load(session, chat_id)

    if pending.kind == "new_person_flow" and pending.payload:
        return ChatResponse(messages=await _new_person_text(session, chat_id, pending, text))
    if pending.kind == "archive_confirm" and pending.payload:
        return ChatResponse(messages=await _archive_confirm_text(session, chat_id, pending, text))
    if pending.kind == "edit_field_flow" and (pending.payload or {}).get("field"):
        return ChatResponse(messages=await _edit_field_value_text(session, chat_id, pending, text))
    if pending.kind == "running_amount" and pending.payload:
        return ChatResponse(messages=await _running_amount_text(session, chat_id, pending, text))

    raw = await web_intake.save_web_message(session, chat_id, text)
    pieces = message_splitter.split_into_requests(text)
    is_multi = len(pieces) > 1
    await request_queue.create_batch(session, chat_id, pieces, raw_message_id=raw.id)
    messages = await _advance_all(session, chat_id, is_multi)
    if is_multi:
        intro = ChatMessage(reply=f"{len(pieces)} işlem algılandı, sırayla işliyorum:", outcome=OUTCOME_INFO)
        messages = [intro, *messages]
    return ChatResponse(messages=messages)


async def handle_action(session: AsyncSession, chat_id: str, action: str, text: str | None = None) -> ChatResponse:
    """POST /api/chat/confirm — botun on_callback'inin web karşılığı."""
    if action.startswith("undo:"):
        try:
            tx_id = int(action.split(":", 1)[1])
        except ValueError:
            return ChatResponse(messages=[ChatMessage(reply="Bu işlem tanınmıyor.", outcome=OUTCOME_UNKNOWN_ACTION)])
        return ChatResponse(messages=[await _handle_undo(session, chat_id, tx_id)])

    pending = await web_chat_state.load(session, chat_id)

    if action == "person:no":
        await web_chat_state.clear_pending(session, chat_id)
        msg = ChatMessage(reply="Tamam, iptal ettim.", outcome=OUTCOME_CANCELLED)
        return ChatResponse(messages=await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL))

    if action in ("person:yes", "person:new"):
        return ChatResponse(messages=[await _begin_new_person_flow(session, chat_id, pending)])

    if action.startswith("person:pick:"):
        try:
            person_id = int(action.rsplit(":", 1)[1])
        except ValueError:
            return ChatResponse(messages=[ChatMessage(reply="Bu işlem tanınmıyor.", outcome=OUTCOME_UNKNOWN_ACTION)])
        return ChatResponse(messages=await _handle_person_pick(session, chat_id, pending, person_id))

    if action == "newperson:confirm":
        return ChatResponse(messages=[await _new_person_confirm_name(session, chat_id, pending)])
    if action == "newperson:edit":
        return ChatResponse(messages=[await _new_person_edit_name(session, chat_id, pending)])
    if action == "newperson:skip_all":
        return ChatResponse(messages=await _new_person_skip_all(session, chat_id, pending))
    if action == "newperson:skip":
        return ChatResponse(messages=await _new_person_skip_field(session, chat_id, pending))
    if action == "newperson:cancel":
        return ChatResponse(messages=await _new_person_cancel(session, chat_id, pending))

    if action == "llm:yes":
        return ChatResponse(messages=await _handle_llm_confirm_yes(session, chat_id, pending))
    if action == "llm:fix":
        await web_chat_state.clear_pending(session, chat_id)
        msg = ChatMessage(reply="Tamam, doğrusunu yazar mısın?", outcome=OUTCOME_CANCELLED)
        return ChatResponse(messages=await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL))
    if action == "llm:cancel":
        await web_chat_state.clear_pending(session, chat_id)
        msg = ChatMessage(reply="Tamam, iptal ettim.", outcome=OUTCOME_CANCELLED)
        return ChatResponse(messages=await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL))

    if action == "report:daily":
        return ChatResponse(messages=await _handle_report_daily(session, chat_id))
    if action == "report:general":
        return ChatResponse(messages=await _handle_report_general(session, chat_id))

    if action == "info:balance":
        return ChatResponse(messages=await _handle_info_balance(session, chat_id, pending))
    if action == "info:card":
        return ChatResponse(messages=await _handle_info_card(session, chat_id, pending))
    if action == "info:report":
        return ChatResponse(messages=await _handle_info_report(session, chat_id, pending))

    if action == "edit:yes":
        return ChatResponse(messages=await _handle_edit_confirm_yes(session, chat_id, pending))
    if action == "edit:no":
        await web_chat_state.clear_pending(session, chat_id)
        msg = ChatMessage(reply="Tamam, değişiklik yapılmadı.", outcome=OUTCOME_CANCELLED)
        return ChatResponse(messages=await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL))
    if action.startswith("editfield:"):
        field = action.split(":", 1)[1]
        return ChatResponse(messages=[await _handle_edit_field_pick(session, chat_id, pending, field)])

    if action == "delete:person":
        return ChatResponse(messages=await _handle_delete_ambiguous_person(session, chat_id, pending))
    if action == "delete:payment":
        return ChatResponse(messages=await _handle_delete_ambiguous_payment(session, chat_id, pending))
    if action == "delete:cancel":
        await web_chat_state.clear_pending(session, chat_id)
        msg = ChatMessage(reply="Tamam, iptal ettim.", outcome=OUTCOME_CANCELLED)
        return ChatResponse(messages=await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL))

    if action == "running:fix":
        return ChatResponse(messages=await _handle_running_fix(session, chat_id, pending))
    if action == "running:cancel":
        await web_chat_state.clear_pending(session, chat_id)
        msg = ChatMessage(reply="Tamam, iptal ettim. Doğru sayılarla tekrar yazar mısın?", outcome=OUTCOME_CANCELLED)
        return ChatResponse(messages=await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL))

    if action == "product:yes":
        return ChatResponse(messages=await _handle_product_confirm(session, chat_id, pending, use_suggestion=True))
    if action == "product:new":
        return ChatResponse(messages=await _handle_product_confirm(session, chat_id, pending, use_suggestion=False))
    if action == "product:cancel":
        await web_chat_state.clear_pending(session, chat_id)
        msg = ChatMessage(reply="İşlem iptal edildi.", outcome=OUTCOME_CANCELLED)
        return ChatResponse(messages=await _conclude(session, chat_id, msg, False, durum=request_queue.IPTAL))

    return ChatResponse(messages=[ChatMessage(reply="Bu işlem tanınmıyor.", outcome=OUTCOME_UNKNOWN_ACTION)])


async def handle_cancel(session: AsyncSession, chat_id: str) -> ChatResponse:
    """POST /api/chat/cancel — durdurma (⏹) butonunun karşılığı.

    Kullanıcı bir soruya cevap vermek yerine "dur" derse iki şeyin de
    temizlenmesi gerekir: (1) bekleyen soru (`web_chat_pending` — "hangisi?",
    "DUMAN yaz", yeni kişi adımları...), (2) kuyrukta o mesajdan kalan diğer
    parçalar. İkincisi olmazsa bir sonraki komut, iptal edilen batch'in
    kalanını da sürükler.

    `undo` alanına KASTEN dokunulmaz: "Geri al" son KAYDEDİLMİŞ işlemin 60 sn'lik
    ayrı penceresidir, bekleyen bir soru değil — durdurma onu iptal etmez.
    Hiçbir şey kaydedilmez/silinmez; yalnızca "cevap bekliyorum" durumu düşer.
    """
    pending = await web_chat_state.load(session, chat_id)
    had_pending = pending.kind is not None
    await web_chat_state.clear_pending(session, chat_id)

    stmt = select(PendingRequest).where(
        PendingRequest.chat_id == chat_id,
        PendingRequest.durum.in_([request_queue.BEKLEMEDE, request_queue.ISLENIYOR]),
    )
    kalanlar = (await session.execute(stmt)).scalars().all()
    for req in kalanlar:
        await request_queue.mark(session, req.id, request_queue.IPTAL, sonuc="kullanici_durdurdu")

    if not had_pending and not kalanlar:
        return ChatResponse(messages=[ChatMessage(reply="Bekleyen bir işlem yok.", outcome=OUTCOME_INFO)])
    return ChatResponse(
        messages=[ChatMessage(reply="İptal edildi, yeni komut bekliyorum.", outcome=OUTCOME_CANCELLED)]
    )
