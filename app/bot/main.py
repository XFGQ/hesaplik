"""Telegram bot — yerelde uzun yoklama (long polling).

Faz 3'ün ikinci adımı: kural tabanlı parser + kayıt + onay akışı. LLM yok.
Düz metin geldiğinde önce save_raw_message ile raw_messages'a yazılır
(mesaj hiçbir zaman kaybolmaz), sonra message_processor ile parser +
intent_resolver çalıştırılır.

Onay akışı (CLAUDE.md > Faz 3):
  - Net ve güvenliyse: anında kaydet + 60 sn süreli "Geri al" butonu.
  - Kişi bulunamazsa: "Ekleyeyim mi?" + Evet/Hayır.
  - Kişi adayı birden fazlaysa: TEK soru, seçenekler buton olarak.
  - Anlaşılmazsa: örnekli kısa açıklama, ikinci soru sorulmaz.

Müşteriye teknik terim (provider, güven skoru, LLM) asla gösterilmez.

Admin/müşteri ayrımı: /durum yalnızca TELEGRAM_ADMIN_IDS içindeki
chat_id'lere yanıt verir. Yetkisiz kişi yazarsa hiç cevap verilmez —
komutun varlığı bile sızmasın.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

from sqlalchemy import func, select
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings
from app.db import SessionLocal
from app.models import Person, RawMessage
from app.services import catalog, message_processor
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent
from app.services.ledger import Balance, LedgerError
from app.services.ledger import reverse as ledger_reverse
from app.services.message_processor import ProcessOutcome, ProcessResult
from app.services.telegram_intake import save_raw_message

logger = logging.getLogger(__name__)

UNDO_WINDOW_SECONDS = 60

MUSTERI_KARSILAMA = (
    "Merhaba! Ben Hesaplık.\n"
    "Borç ya da tahsilat kaydetmek için buraya yazman yeterli.\n"
    "Yardım için /yardim yazabilirsin."
)

YARDIM_METNI = (
    'Buraya yazdığın mesajları alıp deftere işliyorum.\n'
    'Örnek: "Ahmet 20 balya saman aldı 15000 tl borç".\n'
    'Bakiye sormak için: "Ahmet borcu ne kadar".'
)

ANLASILAMADI_METNI = (
    "Tam anlayamadım. Örnek: \"Ahmet 20 balya saman aldı 1500 lira borç\".\n"
    "Ya da uygulamadan elle girebilirsin."
)

_BACK_VOWELS = "aıou"
_FRONT_VOWELS = "eiöü"


# --------------------------------------------------------------- biçimlendirme

def _fmt_decimal(value: Decimal | None) -> str:
    if value is None:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _title_tr(text: str) -> str:
    words = []
    for w in (text or "").split():
        if not w:
            continue
        head, rest = w[0], w[1:]
        head = "İ" if head == "i" else head.upper()
        words.append(head + rest)
    return " ".join(words)


def _dative(name: str) -> str:
    lowered = name.lower()
    vowels = _BACK_VOWELS + _FRONT_VOWELS
    last_vowel = next((c for c in reversed(lowered) if c in vowels), "e")
    suffix = "a" if last_vowel in _BACK_VOWELS else "e"
    if lowered and lowered[-1] in vowels:
        suffix = "y" + suffix
    return f"{name}'{suffix}"


def _format_item_summary(resolved: ResolvedIntent) -> str:
    if resolved.product is not None and resolved.qty is not None:
        unit = f"{resolved.unit} " if resolved.unit else ""
        return f"{_fmt_decimal(resolved.qty)} {unit}{resolved.product.name}".strip()
    return f"{_fmt_decimal(resolved.amount)} TL"


def _format_balance(person: Person, bal: Balance) -> str:
    amount = _fmt_decimal(abs(bal.balance_try))
    if bal.balance_try > 0:
        durum = f"{amount} TL borcu var"
    elif bal.balance_try < 0:
        durum = f"{amount} TL alacaklı"
    else:
        durum = "hesabı sıfır"
    text = f"{person.full_name}: {durum}."
    if bal.items:
        items = "\n".join(f"  {name}: {_fmt_decimal(qty)} {unit}" for name, qty, unit in bal.items)
        text += f"\nAçık kalemler:\n{items}"
    return text


def _undo_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩ Geri al", callback_data=f"undo:{tx_id}")]])


def _yes_no_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Evet", callback_data="person:yes"),
            InlineKeyboardButton("Hayır", callback_data="person:no"),
        ]]
    )


def _candidates_keyboard(candidates: list[Person]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(p.full_name, callback_data=f"person:pick:{p.id}")] for p in candidates]
    rows.append([InlineKeyboardButton("+ Yeni kişi ekle", callback_data="person:new")])
    return InlineKeyboardMarkup(rows)


def _pending_from_resolved(resolved: ResolvedIntent, raw_message_id: int, raw_text: str) -> dict:
    return {
        "kind": resolved.kind,
        "person_name_raw": resolved.person_name_raw,
        "qty": resolved.qty,
        "unit": resolved.unit,
        "product_name": resolved.product_name_raw,
        "amount": resolved.amount,
        "raw_message_id": raw_message_id,
        "raw_text": raw_text,
    }


def _is_admin(chat_id: int | None) -> bool:
    return chat_id is not None and chat_id in settings.telegram_admin_ids_list


# --------------------------------------------------------------- komutlar

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MUSTERI_KARSILAMA)


async def cmd_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(YARDIM_METNI)


async def cmd_durum(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not _is_admin(chat.id if chat else None):
        return  # yetkisiz kişiye komutun varlığı bile sızmasın

    async with SessionLocal() as session:
        total = (await session.execute(select(func.count(RawMessage.id)))).scalar_one()
        unprocessed = (
            await session.execute(
                select(func.count(RawMessage.id)).where(RawMessage.processed_at.is_(None))
            )
        ).scalar_one()
        db_ok = True
        try:
            await session.execute(select(1))
        except Exception:
            db_ok = False

    await update.message.reply_text(
        f"Toplam ham mesaj: {total}\n"
        f"İşlenmemiş: {unprocessed}\n"
        f"Veritabanı: {'sağlıklı' if db_ok else 'ERİŞİLEMİYOR'}"
    )


# --------------------------------------------------------------- düz metin

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""

    # Aynı session boyunca: save_raw_message hemen commit edilir (mesaj
    # asla kaybolmaz), sonra aynı session'da işlenir — raw nesnesi başka
    # bir session'a taşınırsa flush() processed_at/transaction_id
    # güncellemesini göremez.
    async with SessionLocal() as session:
        raw = await save_raw_message(session, update.to_dict())
        await session.commit()

        result = await message_processor.process_raw_message(session, raw, text)
        await session.commit()

    await _reply_result(update, context, result, raw, text)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with SessionLocal() as session:
        await save_raw_message(session, update.to_dict())
        await session.commit()
    await update.message.reply_text("Ses kaydını aldım, şimdilik yazıyla gönderir misin?")


async def _reply_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: ProcessResult,
    raw: RawMessage,
    text: str,
) -> None:
    resolved = result.resolved

    if result.outcome == ProcessOutcome.RECORDED:
        kind_word = "borç" if resolved.kind == "debt" else "tahsilat"
        msg = (
            f"{_dative(resolved.person.full_name)} {_format_item_summary(resolved)} "
            f"{kind_word} eklendi.\nYeni bakiye: {_fmt_decimal(result.balance.balance_try)} TL."
        )
        context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
        await update.message.reply_text(msg, reply_markup=_undo_keyboard(result.transaction_id))
        return

    if result.outcome == ProcessOutcome.BALANCE:
        await update.message.reply_text(_format_balance(resolved.person, result.balance))
        return

    if result.outcome == ProcessOutcome.PERSON_NOT_FOUND:
        isim = _title_tr(resolved.person_name_raw or "")
        if resolved.kind == "balance_query":
            await update.message.reply_text(f"{isim} defterde yok.")
            return
        context.chat_data["pending"] = _pending_from_resolved(resolved, raw.id, text)
        await update.message.reply_text(
            f"{isim} defterde yok. Ekleyeyim mi?", reply_markup=_yes_no_keyboard()
        )
        return

    if result.outcome == ProcessOutcome.NEEDS_CONFIRMATION:
        context.chat_data["pending"] = _pending_from_resolved(resolved, raw.id, text)
        await update.message.reply_text(
            "Hangisini demek istedin?", reply_markup=_candidates_keyboard(resolved.person_candidates)
        )
        return

    # UNRECOGNIZED — asla ikinci soru sorma, örnekle yönlendir.
    await update.message.reply_text(ANLASILAMADI_METNI)


# --------------------------------------------------------------- callback'ler

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    data = query.data or ""
    await query.answer()

    if data.startswith("undo:"):
        await _handle_undo(query, context, int(data.split(":", 1)[1]))
        return
    if data == "person:no":
        context.chat_data.pop("pending", None)
        await query.edit_message_text("Tamam, iptal ettim.")
        return
    if data in ("person:yes", "person:new"):
        await _handle_create_person(query, context)
        return
    if data.startswith("person:pick:"):
        person_id = int(data.rsplit(":", 1)[1])
        await _handle_person_pick(query, context, person_id)
        return


async def _handle_undo(query, context: ContextTypes.DEFAULT_TYPE, tx_id: int) -> None:
    pending = context.chat_data.get("undo")
    if not pending or pending["tx_id"] != tx_id or time.monotonic() - pending["at"] > UNDO_WINDOW_SECONDS:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Bu işlemin geri alma süresi geçti.")
        return

    async with SessionLocal() as session:
        try:
            await ledger_reverse(session, tx_id, actor=message_processor.TELEGRAM_ACTOR, reason="Telegram: geri al")
            await session.commit()
        except LedgerError as e:
            await session.rollback()
            await query.message.reply_text(f"Geri alınamadı: {e}")
            return

    context.chat_data.pop("undo", None)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Kayıt geri alındı.")


async def _handle_create_person(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.chat_data.pop("pending", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = Person(full_name=_title_tr(pending["person_name_raw"] or ""))
        session.add(person)
        await session.flush()
        await _finish_pending(session, query, context, person, pending)
        await session.commit()


async def _handle_person_pick(query, context: ContextTypes.DEFAULT_TYPE, person_id: int) -> None:
    pending = context.chat_data.pop("pending", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, person_id)
        if person is None:
            await query.edit_message_text("Kişi bulunamadı.")
            return
        await _finish_pending(session, query, context, person, pending)
        await session.commit()


async def _finish_pending(session, query, context, person: Person, pending: dict) -> None:
    resolved = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind=pending["kind"],
        person=person,
        qty=pending["qty"],
        unit=pending["unit"],
        product_name_raw=pending["product_name"],
        amount=pending["amount"],
    )
    if pending["product_name"] and pending["kind"] != "balance_query":
        product, _created = await catalog.resolve_or_create(
            session, pending["product_name"], pending["unit"]
        )
        resolved.product = product

    raw = await session.get(RawMessage, pending["raw_message_id"])
    result = await message_processor.handle_resolved(session, raw, resolved, pending["raw_text"])

    if result.outcome == ProcessOutcome.BALANCE:
        await query.edit_message_text(_format_balance(person, result.balance))
        return

    kind_word = "borç" if resolved.kind == "debt" else "tahsilat"
    msg = (
        f"{_dative(person.full_name)} {_format_item_summary(resolved)} "
        f"{kind_word} eklendi.\nYeni bakiye: {_fmt_decimal(result.balance.balance_try)} TL."
    )
    context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
    await query.edit_message_text(msg, reply_markup=_undo_keyboard(result.transaction_id))


# --------------------------------------------------------------- kurulum

async def _set_commands(application: Application) -> None:
    await application.bot.set_my_commands(
        [BotCommand("start", "Başla"), BotCommand("yardim", "Yardım")],
        scope=BotCommandScopeDefault(),
    )
    for admin_id in settings.telegram_admin_ids_list:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Başla"),
                BotCommand("yardim", "Yardım"),
                BotCommand("durum", "Sistem durumu"),
            ],
            scope=BotCommandScopeChat(chat_id=admin_id),
        )


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN tanımlı değil, bot başlatılamaz")

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_set_commands)
        .build()
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("yardim", cmd_yardim))
    application.add_handler(CommandHandler("durum", cmd_durum))
    application.add_handler(MessageHandler(filters.VOICE, on_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(CallbackQueryHandler(on_callback))

    return application


def main() -> None:
    logging.basicConfig(level=settings.log_level.upper())
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
