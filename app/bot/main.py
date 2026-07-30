"""Telegram bot — yerelde uzun yoklama (long polling).

Faz 3: kural tabanlı parser + kayıt + onay akışı. Faz 4: kural parser
çözemezse (None) LLM (Ollama) fallback olarak devreye girer — kural
parser kaldırılmaz, LLM yalnızca ek bir yol. Düz metin geldiğinde önce
save_raw_message ile raw_messages'a yazılır (mesaj hiçbir zaman
kaybolmaz), sonra message_processor ile parser + (gerekirse) LLM +
intent_resolver çalıştırılır.

Onay akışı (CLAUDE.md > Faz 3):
  - Net ve güvenliyse (kural parser): anında kaydet + 60 sn süreli
    "Geri al" butonu.
  - Kişi bulunamazsa: "Ekleyeyim mi?" + Evet/Hayır.
  - Kişi adayı birden fazlaysa: TEK soru, seçenekler buton olarak.
  - LLM'den gelen bir kayıt (borç/tahsilat) niyeti: kişi/ürün/tutar net
    olsa da düşük güven sayılır, "Bunu mu demek istediniz?" +
    Evet/Düzelt/İptal (Faz 4).
  - Anlaşılmazsa: örnekli kısa açıklama, ikinci soru sorulmaz.
  - Kişi silme ("furkanı sil"): HİÇBİR ŞEY hemen silinmez. Kişi netleşince
    YAZARAK onay istenir (işletme adının ilk kelimesi, Türkçe büyük harf);
    doğru yazılırsa person_archive.archive_person çağrılır (arşivle +
    pasifleştir), yanlış/eksik onayda iptal edilir. Geri getirme bot'tan
    YAPILMAZ (CLAUDE.md > "Bot kişi silme = arşivleme — Grup 3").

Müşteriye teknik terim (provider, güven skoru, LLM) asla gösterilmez.

Admin/müşteri ayrımı: /durum yalnızca TELEGRAM_ADMIN_IDS içindeki
chat_id'lere yanıt verir. Yetkisiz kişi yazarsa hiç cevap verilmez —
komutun varlığı bile sızmasın.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
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
from telegram.constants import ChatAction, ParseMode
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
from app.models import Person, Product, RawMessage, TxKind
from app.services import catalog, llm_provider, message_processor, person_archive, person_edit, report
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent
from app.services.ledger import Balance, LedgerError, balance_of
from app.services.ledger import reverse as ledger_reverse
from app.services.message_processor import BALANCE_TABLE_LIMIT, ProcessOutcome, ProcessResult
from app.services.person_edit import PersonEditError
from app.services.queries import PersonBalanceRow, PersonTransactionRow, list_person_transactions
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

# Salt okunur sorgu niyetleri: bulunamayan kişi için "Ekleyeyim mi?"
# sorulmaz (bkz. PERSON_NOT_FOUND kolları), sadece "defterde yok" denir —
# bunlar hiçbir şey kaydetmediği için yeni kişi açmak anlamsız. archive_*
# de burada: olmayan birini arşivlemek/silmek anlamsız, "Ekleyeyim mi?"
# sorusu kafa karıştırır (CLAUDE.md > "Bot kişi silme = arşivleme — Grup 3").
# edit_person de aynı sebeple burada (CLAUDE.md > "Silme mesajı + kişi
# düzenleme — Grup 4"): olmayan birinin bilgisini düzenlemek anlamsız.
_QUERY_ONLY_KINDS = (
    "balance_query", "report_person", "person_contact", "info_menu",
    "archive_person", "archive_and_recreate", "edit_person",
)

_BACK_VOWELS = "aıou"
_FRONT_VOWELS = "eiöü"


# --------------------------------------------------------------- "yazıyor..." göstergesi

TYPING_REFRESH_SECONDS = 4  # Telegram'da typing durumu ~5 sn sürer, süresi dolmadan yenilenir


class _TypingIndicator:
    """LLM'e düşen mesaj işleme veya rapor üretimi gibi uzun sürebilecek
    işlemler boyunca arka planda periyodik send_chat_action gönderir;
    kullanıcı botu donmuş sanmasın diye. `async with` bloğu bitince (işlem
    tamamlanınca) arka plan görevi durur."""

    def __init__(self, bot, chat_id: int, action: str) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._action = action
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "_TypingIndicator":
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            try:
                await self._bot.send_chat_action(chat_id=self._chat_id, action=self._action)
            except Exception:
                logger.debug("typing action gönderilemedi", exc_info=True)
            await asyncio.sleep(TYPING_REFRESH_SECONDS)


def typing_action(bot, chat_id: int, action: str = ChatAction.TYPING) -> _TypingIndicator:
    return _TypingIndicator(bot, chat_id, action)


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


def _locative(name: str) -> str:
    lowered = name.lower()
    vowels = _BACK_VOWELS + _FRONT_VOWELS
    last_vowel = next((c for c in reversed(lowered) if c in vowels), "e")
    suffix = "da" if last_vowel in _BACK_VOWELS else "de"
    return f"{name}'{suffix}"


def _genitive(name: str) -> str:
    """İyelik onay mesajı için ("Mehmet'in ilçesi Ahmetbeyler yapılsın mı?")
    — 4 yönlü ünlü uyumu (ın/in/un/ün), _dative/_locative'in 2 yönlü
    (a/e) basitleştirmesinden farklı olarak burada gerekli çünkü ekin ilk
    harfi (ı/i/u/ü) tek başına anlamı bozar ("Mehmet'ın" yanlış olurdu)."""
    lowered = name.lower()
    vowels = _BACK_VOWELS + _FRONT_VOWELS
    last_vowel = next((c for c in reversed(lowered) if c in vowels), "e")
    if last_vowel in "aı":
        suffix = "ın"
    elif last_vowel in "ei":
        suffix = "in"
    elif last_vowel in "ou":
        suffix = "un"
    else:  # ö/ü
        suffix = "ün"
    if lowered and lowered[-1] in vowels:
        # İyelik ekinin tampon ünsüzü "n"dir (Kaya+nın), _dative/_locative'deki
        # "y" tamponuyla (Kaya'ya) KARIŞTIRILMAMALI — farklı ek, farklı tampon.
        suffix = "n" + suffix
    return f"{name}'{suffix}"


def _format_item_summary(resolved: ResolvedIntent) -> str:
    if resolved.product is not None and resolved.qty is not None:
        unit = f"{resolved.unit} " if resolved.unit else ""
        return f"{_fmt_decimal(resolved.qty)} {unit}{resolved.product.name}".strip()
    return f"{_fmt_decimal(resolved.amount)} TL"


def _format_record_summary(resolved: ResolvedIntent) -> str:
    """Kayıt onay mesajındaki özet satırı: kalem VARSA hem kalemi hem tutarı
    birlikte gösterir ("30 balya saman · 5.000,00 TL") — _format_item_summary
    (yalnızca LLM önizlemesinde kullanılır, bkz. _format_llm_preview) ikisini
    ayrı ayrı gösterir, burada CLAUDE.md > "Bot kayıt akışı — Grup 2"
    örneğindeki gibi İKİSİ birden gerekiyor."""
    amount_str = f"{_fmt_try(resolved.amount)} TL"
    if resolved.product is not None and resolved.qty is not None:
        unit = f"{resolved.unit} " if resolved.unit else ""
        item = f"{_fmt_decimal(resolved.qty)} {unit}{resolved.product.name}".strip()
        return f"{item} · {amount_str}"
    return amount_str


def _format_record_confirmation(
    resolved: ResolvedIntent, balance_before: Balance, balance_after: Balance
) -> str:
    """Kayıt onay mesajı: ÖNCEKİ ve GÜNCEL bakiyeyi birlikte gösterir
    (CLAUDE.md > "Bot kayıt akışı — Grup 2") — kullanıcı değişimi görsün.
    "Önceki bakiye" yalnızca tutarı gösterir, "Güncel bakiye" ayrıca
    borçlu/alacaklı/sıfır durumunu da ekler (CLAUDE.md'deki örnekle aynı
    asimetri: önceki yalnızca referans, güncel kullanıcının asıl ilgilendiği
    değer)."""
    kind_word = "borç" if resolved.kind == "debt" else "tahsilat"
    if balance_after.balance_try > 0:
        durum = "borçlu"
    elif balance_after.balance_try < 0:
        durum = "alacaklı"
    else:
        durum = "sıfır"
    return (
        f"✅ {resolved.person.full_name}\n"
        f"{_format_record_summary(resolved)} {kind_word} eklendi\n"
        f"Önceki bakiye: {_fmt_try(abs(balance_before.balance_try))} TL\n"
        f"Güncel bakiye: {_fmt_try(abs(balance_after.balance_try))} TL {durum}"
    )


# Kısa ay adı (report.py'deki tarih biçimiyle aynı, yıl olmadan — bakiye
# tablosu güncel yılı zaten göstermeye gerek duymaz).
_AY_KISA = {
    1: "Oca", 2: "Şub", 3: "Mar", 4: "Nis", 5: "May", 6: "Haz",
    7: "Tem", 8: "Ağu", 9: "Eyl", 10: "Eki", 11: "Kas", 12: "Ara",
}


def _tarih_kisa(dt) -> str:
    d = dt.astimezone(report.TR_TZ)
    return f"{d.day} {_AY_KISA[d.month]}"


def _format_balance(
    person: Person,
    bal: Balance,
    txs: list[PersonTransactionRow],
    txs_total: int,
) -> str:
    """Bakiye sorgusu düz metin değil, hizalı bir TABLO döner (CLAUDE.md >
    "Bot sorgu anlama" Grup 1, madde 2): her hareket için tarih/ürün-adet/
    tutar, sonda güncel bakiye. Telegram'da <pre> (monospace) ile hizalı
    gösterilir — çağıran taraf reply_text'i ParseMode.HTML ile göndermeli.
    Çok hareket varsa yalnızca en SON BALANCE_TABLE_LIMIT kayıt gösterilir,
    kalanı "...ve N kayıt daha" ile özetlenir."""
    lines = [html.escape(person.full_name), "─" * 24]

    if not txs:
        lines.append("Hareket yok.")
    for t in txs:
        tarih = _tarih_kisa(t.occurred_at)
        if t.lines:
            aciklama = " · ".join(
                f"{html.escape(name)} {_fmt_decimal(qty)} {html.escape(unit)}" for name, qty, unit in t.lines
            )
        else:
            aciklama = "Tahsilat" if t.kind == TxKind.CREDIT else "Borç"
        isaret = "+" if t.kind == TxKind.DEBIT else "−"
        tutar = f"{isaret}{_fmt_try(t.amount_try)}"
        lines.append(f"{tarih:<7}{aciklama:<28}{tutar}")

    if txs_total > len(txs):
        lines.append(f"...ve {txs_total - len(txs)} kayıt daha")

    lines.append("─" * 24)
    if bal.balance_try > 0:
        lines.append(f"Güncel bakiye: {_fmt_try(bal.balance_try)} TL borçlu")
    elif bal.balance_try < 0:
        lines.append(f"Güncel bakiye: {_fmt_try(-bal.balance_try)} TL alacaklı")
    else:
        lines.append("Güncel bakiye: hesabı sıfır")

    return "📋 <pre>" + "\n".join(lines) + "</pre>"


def _format_person_card(person: Person) -> str:
    """Kişi bilgileri kartı: ad/telefon/il/ilçe, boş alanlar gösterilmez
    (CLAUDE.md > "Kişi bilgi sorgusu + hitap kelimeleri")."""
    lines = [person.full_name]
    if person.phone:
        lines.append(f"Telefon: {person.phone}")
    if person.city:
        lines.append(f"İl: {person.city}")
    if person.district:
        lines.append(f"İlçe: {person.district}")
    if len(lines) == 1:
        lines.append("Kayıtlı iletişim/konum bilgisi yok.")
    return "\n".join(lines)


TELEGRAM_MAX_LEN = 4096

_LIST_TITLES = {"list_all": "Kişiler", "list_debtors": "Borçlular", "list_creditors": "Alacaklılar"}
_LIST_EMPTY_MESSAGES = {
    "list_all": "Defterde kayıtlı kimse yok.",
    "list_debtors": "Şu anda borçlu kimse yok.",
    "list_creditors": "Şu anda alacaklı kimse yok.",
}


def _fmt_try(value: Decimal) -> str:
    """1500.5 -> "1.500,50" (Türkçe: binlik nokta, ondalık virgül)."""
    q = value.quantize(Decimal("0.01"))
    neg = q < 0
    formatted = f"{abs(q):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-{formatted}" if neg else formatted


def _format_person_row(row: PersonBalanceRow) -> str:
    bal = row.balance_try
    if bal > 0:
        line = f"{row.person.full_name} — {_fmt_try(bal)} TL borçlu"
    elif bal < 0:
        line = f"{row.person.full_name} — {_fmt_try(-bal)} TL alacaklı"
    else:
        line = f"{row.person.full_name} — hesabı sıfır"
    if row.items:
        extra = "\n".join(f"  {_fmt_decimal(qty)} {unit} {name}" for name, qty, unit in row.items)
        line = f"{line}\n{extra}"
    return line


def _format_daily_report_caption(stats: report.DailyStats) -> str:
    net = stats.net
    yon = "borç" if net >= 0 else "alacak"
    return f"📄 Günlük rapor — {stats.count} kayıt, net {_fmt_try(abs(net))} TL {yon}."


def _format_general_report_caption(stats: report.GeneralStats) -> str:
    net = stats.net_alacak
    yon = "alacak" if net >= 0 else "borç"
    return f"📄 Genel durum raporu — {stats.kisi_sayisi} kişi, net {_fmt_try(abs(net))} TL {yon}."


def _format_person_report_caption(person: Person, bal: Balance) -> str:
    if bal.balance_try > 0:
        durum = f"{_fmt_try(bal.balance_try)} TL borçlu"
    elif bal.balance_try < 0:
        durum = f"{_fmt_try(-bal.balance_try)} TL alacaklı"
    else:
        durum = "hesabı sıfır"
    return f"📄 {person.full_name} ekstresi — {durum}."


def _split_for_telegram(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _format_list_messages(kind: str, district: str | None, rows: list[PersonBalanceRow]) -> list[str]:
    if kind == "list_district":
        title = _title_tr(district or "")
        if not rows:
            return [f"{_locative(title)} kayıtlı kimse yok."]
    else:
        title = _LIST_TITLES[kind]
        if not rows:
            return [_LIST_EMPTY_MESSAGES[kind]]

    header = f"📋 {title} ({len(rows)} kişi)"
    body = "\n".join(_format_person_row(r) for r in rows)
    return _split_for_telegram(f"{header}\n{body}")


def _format_search_messages(term: str | None, rows: list[PersonBalanceRow]) -> list[str]:
    """Tek kelime = arama (CLAUDE.md > "Bot sorgu anlama" Grup 1, madde 5):
    "hangisi?" diye SORMAZ, isim/soyad/ilçede eşleşen HERKESİ bakiyeleriyle
    listeler — liste sorguları (_format_list_messages) ile aynı görünüm."""
    if not rows:
        return [f"'{term}' ile eşleşen kişi yok."]

    header = f"🔍 '{term}' ({len(rows)} kişi)"
    body = "\n".join(_format_person_row(r) for r in rows)
    return _split_for_telegram(f"{header}\n{body}")


def _undo_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩ Geri al", callback_data=f"undo:{tx_id}")]])


def _yes_no_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Evet", callback_data="person:yes"),
            InlineKeyboardButton("Hayır", callback_data="person:no"),
        ]]
    )


def _report_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Günlük", callback_data="report:daily"),
            InlineKeyboardButton("Genel", callback_data="report:general"),
        ]]
    )


def _info_menu_keyboard() -> InlineKeyboardMarkup:
    """Belirsiz "bilgi ver" isteğinde sorulan üç seçenek (CLAUDE.md >
    "DÜZELTME — 'bilgi ver' belirsiz, SOR")."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Bakiye / borç", callback_data="info:balance")],
            [InlineKeyboardButton("Kişi bilgileri", callback_data="info:card")],
            [InlineKeyboardButton("Ekstre (PDF)", callback_data="info:report")],
        ]
    )


def _candidates_keyboard(candidates: list[Person]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(p.full_name, callback_data=f"person:pick:{p.id}")] for p in candidates]
    rows.append([InlineKeyboardButton("+ Yeni kişi ekle", callback_data="person:new")])
    return InlineKeyboardMarkup(rows)


# --------------------------------------------------------------- kişi silme = arşivleme
#
# "furkanı sil" gibi bir komut hiçbir şeyi hemen silmez: kişi netleşince
# (candidate akışından da geçebilir) YAZARAK onay istenir — settings.
# business_name'in ilk kelimesi, Türkçe kurallarla büyük harfe çevrilmiş
# (CLAUDE.md > "Yazarak onay her silmede" ve "Bot kişi silme = arşivleme —
# Grup 3"). Doğru kelime yazılınca person_archive.archive_person çağrılır;
# GERİ GETİRME bot'tan yapılmaz (kaza riski, yalnızca komut satırı).

def _turkce_buyuk(s: str) -> str:
    """Türkçe kurallarla büyük harf: "i" -> "İ" (Python'un varsayılan
    .upper()'ı "i"yi noktasız "I" yapar, Türkçe'de yanlış). "İ".upper()
    zaten "İ" olduğundan bu değişim güvenlidir; diğer Türkçe harfler
    (ç/ğ/ö/ş/ü) zaten .upper() ile doğru eşlenir."""
    return s.replace("i", "İ").upper()


async def _archive_onay_kelimesi() -> str:
    async with SessionLocal() as session:
        isletme = await report.isletme_adi(session)
    ilk_kelime = (isletme or "Hesaplık").split()[0]
    return _turkce_buyuk(ilk_kelime)


def _format_archive_confirm(person_full_name: str, bal: Balance, onay_kelimesi: str) -> str:
    if bal.balance_try > 0:
        bakiye = f"{_fmt_try(bal.balance_try)} TL borçlu"
        uyari = f"\n⚠️ {person_full_name}'in {_fmt_try(bal.balance_try)} TL borcu var."
    elif bal.balance_try < 0:
        bakiye = f"{_fmt_try(-bal.balance_try)} TL alacaklı"
        uyari = f"\n⚠️ {person_full_name}'in {_fmt_try(-bal.balance_try)} TL alacağı var."
    else:
        bakiye = "sıfır"
        uyari = ""
    return (
        f"{person_full_name} silinecek. Bakiyesi {bakiye}.{uyari}\n"
        f"Onaylıyorsan {onay_kelimesi} yaz."
    )


async def _prompt_archive_confirm(reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent, bal: Balance) -> None:
    """`reply`, hem Message.reply_text hem CallbackQuery.edit_message_text
    olabilir (ikisi de aynı imzayla metin gönderir) — direkt metinden mi
    ("furkanı sil") yoksa aday seçiminden mi (_finish_pending) geldiği fark
    etmez, onay isteme mantığı tek yerde."""
    onay = await _archive_onay_kelimesi()
    context.chat_data["archive_confirm"] = {
        "person_id": resolved.person.id,
        "kind": resolved.kind,
        "onay_kelimesi": onay,
    }
    await reply(_format_archive_confirm(resolved.person.full_name, bal, onay))


# --------------------------------------------------------------- kişi düzenleme
#
# "mehmet ilçe ahmetbeyler yap" gibi NET bir komut, alan ve yeni değer belli
# olsa da hiçbir şeyi hemen güncellemez — kısa bir Evet/Hayır onayı ister
# (chat_data["edit_confirm"]). "mehmet düzenle" gibi BELİRSİZ bir komutta ise
# hangi alanın düzenleneceği belli olmadığından bot buton menüsü sunar
# (chat_data["edit_field_flow"]), seçim yapılınca yeni değeri düz metinle
# sorar. İkisinde de gerçek güncelleme person_edit.update_person_field ile
# yapılır (CLAUDE.md > "Silme mesajı + kişi düzenleme — Grup 4").

_FIELD_LABELS = {
    "full_name": "adı",
    "phone": "telefonu",
    "city": "ili",
    "district": "ilçesi",
    "address": "adresi",
    "note": "notu",
}

# Alan menüsünde eski değeri gösteren başlık ("İlçe bilgisi: Bergama") ve
# değer isteme satırındaki ("Yeni ilçe için yazın:") ad — büyük/küçük harf
# ayrı tutuluyor çünkü Türkçe "İ"/"i" ayrımı .lower()/.upper() ile güvenle
# üretilemiyor (bkz. catalog.normalize, _turkce_buyuk).
_FIELD_TITLE_NAMES = {
    "full_name": "Ad soyad",
    "phone": "Telefon",
    "city": "İl",
    "district": "İlçe",
    "address": "Adres",
    "note": "Not",
}
_FIELD_LOWER_NAMES = {
    "full_name": "ad soyad",
    "phone": "telefon",
    "city": "il",
    "district": "ilçe",
    "address": "adres",
    "note": "not",
}


def _edit_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Evet", callback_data="edit:yes"),
            InlineKeyboardButton("Hayır", callback_data="edit:no"),
        ]]
    )


def _edit_field_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Ad soyad", callback_data="editfield:full_name")],
            [InlineKeyboardButton("Telefon", callback_data="editfield:phone")],
            [InlineKeyboardButton("İl", callback_data="editfield:city")],
            [InlineKeyboardButton("İlçe", callback_data="editfield:district")],
            [InlineKeyboardButton("Adres", callback_data="editfield:address")],
        ]
    )


def _format_edit_confirm(person_full_name: str, field: str, value: str) -> str:
    label = _FIELD_LABELS.get(field, field)
    return f"{_genitive(person_full_name)} {label} {value} yapılsın mı?"


def _format_edit_result(person_full_name: str, field: str, value: str) -> str:
    """Güncelleme sonucu (CLAUDE.md > "Düzenleme mesajları — eski değer
    göster, ne değişti belirt"): "güncellendi" tek başına yetmez, hangi alan
    ne oldu belli olmalı — "Mehmet Kaya'nın ilçesi Ahmetbeyler olarak
    güncellendi." Hem NET komut onayından hem alan menüsünden sonra kullanılır."""
    label = _FIELD_LABELS.get(field, field)
    return f"{_genitive(person_full_name)} {label} {value} olarak güncellendi."


def _format_edit_field_prompt(field: str, current_value: str | None) -> str:
    """Alan menüsünden bir alan seçilince, yeni değeri sormadan ÖNCE mevcut
    değeri gösterir (CLAUDE.md aynı bölüm): "İlçe bilgisi: Bergama\nYeni
    ilçe için yazın:". Alan boşsa "(boş)" gösterilir."""
    title = _FIELD_TITLE_NAMES.get(field, field)
    lower = _FIELD_LOWER_NAMES.get(field, field)
    display = current_value if current_value else "(boş)"
    return f"{title} bilgisi: {display}\nYeni {lower} için yazın:"


# NET komutun değeri parser.normalize() ile küçük harfe çevrilmiş ham
# metinden gelir ("mehmet il izmir yap" -> new_value="izmir") — özel isim
# sayılan alanlarda (ad soyad, il, ilçe) bu _title_tr ile düzeltilir ki
# hem onay mesajında hem kayıtta "İzmir"/"Ahmetbeyler" görünsün, "izmir"
# değil. Adres/not/telefon serbest metin/rakam olduğundan dokunulmaz.
_EDIT_TITLE_CASE_FIELDS = {"full_name", "city", "district"}


def _edit_display_value(field: str, value: str) -> str:
    return _title_tr(value) if field in _EDIT_TITLE_CASE_FIELDS else value


async def _prompt_edit_confirm(reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent) -> None:
    """NET komut (alan+değer belli) için Evet/Hayır onayı. `reply` hem
    Message.reply_text hem CallbackQuery.edit_message_text olabilir (bkz.
    _prompt_archive_confirm'daki aynı desen)."""
    value = _edit_display_value(resolved.field_name, resolved.new_value)
    context.chat_data["edit_confirm"] = {
        "person_id": resolved.person.id,
        "field": resolved.field_name,
        "value": value,
    }
    msg = _format_edit_confirm(resolved.person.full_name, resolved.field_name, value)
    await reply(msg, reply_markup=_edit_confirm_keyboard())


async def _prompt_edit_field_menu(reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent) -> None:
    """BELİRSİZ komut (alan/değer yok) için alan seçim menüsü."""
    context.chat_data["edit_field_flow"] = {
        "person_id": resolved.person.id,
        "person_name": resolved.person.full_name,
    }
    await reply("Hangi bilgiyi düzenlemek istersin?", reply_markup=_edit_field_keyboard())


async def _apply_edit_person_field(person_id: int, field: str, value: str) -> str | None:
    """Günceller, kişinin (güncel) tam adını döner; kişi bulunamazsa None."""
    async with SessionLocal() as session:
        try:
            person = await person_edit.update_person_field(
                session, person_id, field, value, actor=message_processor.TELEGRAM_ACTOR
            )
        except PersonEditError:
            await session.rollback()
            return None
        await session.commit()
        return person.full_name


async def _handle_edit_field_pick(query, context: ContextTypes.DEFAULT_TYPE, field: str) -> None:
    """Alan seçilince yeni değeri sormadan ÖNCE mevcut değeri gösterir
    (CLAUDE.md > "Düzenleme mesajları — eski değer göster, ne değişti
    belirt") — kullanıcı eski değeri görüp ona göre yenisini yazar."""
    flow = context.chat_data.get("edit_field_flow")
    if not flow:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, flow["person_id"])
    if person is None:
        context.chat_data.pop("edit_field_flow", None)
        await query.edit_message_text("Kişi bulunamadı.")
        return

    flow["field"] = field
    await query.edit_message_text(_format_edit_field_prompt(field, getattr(person, field)))


async def _handle_edit_field_value_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, flow: dict, text: str
) -> None:
    value = (text or "").strip()
    if not value:
        await update.message.reply_text("Değer boş olamaz, tekrar yazar mısın?")
        return
    context.chat_data.pop("edit_field_flow", None)

    name = await _apply_edit_person_field(flow["person_id"], flow["field"], value)
    if name is None:
        await update.message.reply_text("Kişi bulunamadı.")
        return
    await update.message.reply_text(_format_edit_result(name, flow["field"], value))


# --------------------------------------------------------------- yeni kişi — adım adım bilgi toplama
#
# Telegram'dan borç/tahsilat sırasında kişi bulunamayınca (veya "+ Yeni kişi
# ekle" seçilince) kişi tek seferde sadece isimle açılmıyor; ad soyad
# onaylatılıp telefon/il/ilçe adım adım (opsiyonel, [Geç] ile atlanabilir)
# sorulur. Toplanan bilgiler chat_data["new_person_flow"] içinde tutulur;
# akış bitince kişi oluşturulur ve bekleyen borç/tahsilat işlenir
# (CLAUDE.md > "Telegram'dan kişi eklerken detay sorma").

_NEW_PERSON_FIELD_ORDER = ["name", "phone", "city", "district"]


def _new_person_name_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Onayla", callback_data="newperson:confirm")],
            [InlineKeyboardButton("Düzelt", callback_data="newperson:edit")],
            [InlineKeyboardButton("Hepsini geç", callback_data="newperson:skip_all")],
            [InlineKeyboardButton("İptal", callback_data="newperson:cancel")],
        ]
    )


def _new_person_optional_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Geç", callback_data="newperson:skip")],
            [InlineKeyboardButton("İptal", callback_data="newperson:cancel")],
        ]
    )


def _new_person_prompt(step: str, name: str = "") -> tuple[str, InlineKeyboardMarkup]:
    if step == "name":
        return f"Ad soyad: {name} — doğru mu?", _new_person_name_keyboard()
    if step == "phone":
        return "Telefon? (yoksa Geç)", _new_person_optional_keyboard()
    if step == "city":
        return "İl? (yoksa Geç)", _new_person_optional_keyboard()
    if step == "district":
        return "İlçe? (yoksa Geç)", _new_person_optional_keyboard()
    raise ValueError(f"bilinmeyen adım: {step}")


def _new_person_set_field_and_next(flow: dict, value: str | None) -> str | None:
    """flow'daki mevcut adımın değerini yazar, sıradaki adımı döner.
    Son adımdan sonra None döner (toplama tamamlandı demektir)."""
    step = flow["step"]
    flow[step] = value
    idx = _NEW_PERSON_FIELD_ORDER.index(step)
    if idx + 1 < len(_NEW_PERSON_FIELD_ORDER):
        next_step = _NEW_PERSON_FIELD_ORDER[idx + 1]
        flow["step"] = next_step
        return next_step
    return None


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
        "field": resolved.field_name,  # yalnızca edit_person için dolu
        "new_value": resolved.new_value,  # yalnızca edit_person için dolu
    }


def _llm_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Evet", callback_data="llm:yes"),
            InlineKeyboardButton("Düzelt", callback_data="llm:fix"),
            InlineKeyboardButton("İptal", callback_data="llm:cancel"),
        ]]
    )


def _format_llm_preview(resolved: ResolvedIntent) -> str:
    kind_word = "borç" if resolved.kind == "debt" else "tahsilat"
    return (
        f"Bunu mu demek istediniz: {_dative(resolved.person.full_name)} "
        f"{_format_item_summary(resolved)} {kind_word}?"
    )


def _llm_pending_from_resolved(resolved: ResolvedIntent, raw_message_id: int, raw_text: str) -> dict:
    """Kişi/ürün intent_resolver tarafından zaten çözülmüş durumda (READY);
    onay callback'i bunları yeniden fuzzy-eşleştirmeden id ile geri alır."""
    return {
        "kind": resolved.kind,
        "person_id": resolved.person.id,
        "qty": resolved.qty,
        "unit": resolved.unit,
        "product_id": resolved.product.id if resolved.product else None,
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
    chat_id = update.effective_chat.id

    # Yeni kişi bilgi toplama akışı sürüyorsa, gelen metin normal mesaj
    # işlemeye değil bu akışın bir sonraki adımına gider (telefon/il/ilçe
    # ya da isim düzeltmesi).
    flow = context.chat_data.get("new_person_flow")
    if flow is not None:
        await _handle_new_person_text(update, context, flow, text)
        return

    # Arşivleme onayı bekleniyorsa, gelen metin normal mesaj işlemeye değil
    # yazarak-onay kontrolüne gider (CLAUDE.md > "Yazarak onay her silmede").
    archive_confirm = context.chat_data.get("archive_confirm")
    if archive_confirm is not None:
        await _handle_archive_confirm_text(update, context, archive_confirm, text)
        return

    # Kişi düzenleme alan menüsünde bir alan seçilmişse ("field" anahtarı
    # set edilmiş), gelen metin yeni değer olarak işlenir. Henüz alan
    # seçilmemişse (yalnızca menü gösterildi) normal mesaj akışına düşer.
    edit_field_flow = context.chat_data.get("edit_field_flow")
    if edit_field_flow is not None and "field" in edit_field_flow:
        await _handle_edit_field_value_text(update, context, edit_field_flow, text)
        return

    # Mesaj gelir gelmez typing başlar: regex mi LLM'e mi düşeceği henüz
    # belli olmadığından baştan gösterilir. Regex anında çözerse tek bir
    # typing zararsız; LLM'e düşerse (~13 sn) periyodik yenilenir.
    #
    # Aynı session boyunca: save_raw_message hemen commit edilir (mesaj
    # asla kaybolmaz), sonra aynı session'da işlenir — raw nesnesi başka
    # bir session'a taşınırsa flush() processed_at/transaction_id
    # güncellemesini göremez.
    async with typing_action(context.bot, chat_id):
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


async def _handle_new_person_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, flow: dict, text: str
) -> None:
    text = text.strip()
    step = flow["step"]

    if step == "name":
        if not text:
            await update.message.reply_text("Ad soyad boş olamaz, yazar mısın?")
            return
        value: str | None = _title_tr(text)
    else:
        value = text or None

    next_step = _new_person_set_field_and_next(flow, value)
    if next_step is None:
        await _complete_new_person(update.message, context)
        return

    prompt, keyboard = _new_person_prompt(next_step, flow.get("name", ""))
    await update.message.reply_text(prompt, reply_markup=keyboard)


async def _handle_archive_confirm_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict, text: str
) -> None:
    """Yazarak onay kelimesi doğru geldiyse person_archive.archive_person
    çağrılır (archive_and_recreate ise ayrıca aynı isimle temiz yeni kişi
    açılır). Yanlış/eksik onayda HİÇBİR ŞEY arşivlenmez — "işlem iptal
    edildi" denir, ikinci bir şans için kullanıcı komutu yeniden yazmalı
    (CLAUDE.md > "Yazarak onay her silmede")."""
    context.chat_data.pop("archive_confirm", None)

    if _turkce_buyuk((text or "").strip()) != pending["onay_kelimesi"]:
        await update.message.reply_text("İşlem iptal edildi.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, pending["person_id"])
        if person is None or not person.is_active:
            await update.message.reply_text("Kişi bulunamadı.")
            return
        name = person.full_name

        await person_archive.archive_person(
            session,
            person.id,
            archived_by=message_processor.TELEGRAM_ACTOR,
            reason="Telegram: kullanıcı isteğiyle arşivlendi",
        )

        if pending["kind"] == "archive_and_recreate":
            session.add(Person(full_name=name))

        await session.commit()

    if pending["kind"] == "archive_and_recreate":
        await update.message.reply_text(f"{name} silindi, temiz hesap açıldı.")
    else:
        await update.message.reply_text(f"{name} silindi.")


async def _reply_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: ProcessResult,
    raw: RawMessage,
    text: str,
) -> None:
    resolved = result.resolved

    if result.outcome == ProcessOutcome.RECORDED:
        msg = _format_record_confirmation(resolved, result.balance_before, result.balance)
        context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
        await update.message.reply_text(msg, reply_markup=_undo_keyboard(result.transaction_id))
        return

    if result.outcome == ProcessOutcome.BALANCE:
        await update.message.reply_text(
            _format_balance(resolved.person, result.balance, result.transactions or [], result.transactions_total or 0),
            parse_mode=ParseMode.HTML,
        )
        return

    if result.outcome == ProcessOutcome.PERSON_CONTACT:
        await update.message.reply_text(_format_person_card(resolved.person))
        return

    if result.outcome == ProcessOutcome.CREATE_PERSON:
        # _reply_result yalnızca DÜZ METİNDEN gelen sonuçlar için çağrılır;
        # yeni kişi oluşturma her zaman PERSON_NOT_FOUND -> Evet/Hayır ->
        # adım adım akıştan (_complete_new_person) geçer. Bu yüzden bu
        # outcome'a buradan gelinmesi, kişinin zaten mevcut olduğu
        # (find_person_match birebir eşleşme bulduğu) anlamına gelir.
        await update.message.reply_text(f"ℹ️ {resolved.person.full_name} zaten kayıtlı.")
        return

    if result.outcome == ProcessOutcome.INFO_MENU:
        context.chat_data["info_menu"] = {"person_id": resolved.person.id}
        await update.message.reply_text("Ne bilgisi?", reply_markup=_info_menu_keyboard())
        return

    if result.outcome == ProcessOutcome.ARCHIVE_CONFIRM:
        assert result.balance is not None
        await _prompt_archive_confirm(update.message.reply_text, context, resolved, result.balance)
        return

    if result.outcome == ProcessOutcome.EDIT_PERSON_CONFIRM:
        await _prompt_edit_confirm(update.message.reply_text, context, resolved)
        return

    if result.outcome == ProcessOutcome.EDIT_PERSON_MENU:
        await _prompt_edit_field_menu(update.message.reply_text, context, resolved)
        return

    if result.outcome == ProcessOutcome.LIST:
        for msg in _format_list_messages(resolved.kind, resolved.district, result.persons or []):
            await update.message.reply_text(msg)
        return

    if result.outcome == ProcessOutcome.SEARCH:
        for msg in _format_search_messages(resolved.query, result.persons or []):
            await update.message.reply_text(msg)
        return

    if result.outcome == ProcessOutcome.REPORT_MENU:
        await update.message.reply_text(
            "Hangi raporu istersin?", reply_markup=_report_menu_keyboard()
        )
        return

    if result.outcome == ProcessOutcome.REPORT_DAILY:
        assert result.report_pdf is not None and result.report_stats is not None
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        await update.message.reply_document(
            document=result.report_pdf,
            filename=f"rapor_gunluk_{report.today_tr().isoformat()}.pdf",
            caption=_format_daily_report_caption(result.report_stats),
        )
        return

    if result.outcome == ProcessOutcome.REPORT_GENERAL:
        assert result.report_pdf is not None and result.report_stats is not None
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        await update.message.reply_document(
            document=result.report_pdf,
            filename=f"rapor_genel_{report.today_tr().isoformat()}.pdf",
            caption=_format_general_report_caption(result.report_stats),
        )
        return

    if result.outcome == ProcessOutcome.REPORT_PERSON:
        assert result.report_pdf is not None and result.balance is not None
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        await update.message.reply_document(
            document=result.report_pdf,
            filename=f"rapor_ekstre_{report.slugify(resolved.person.full_name)}.pdf",
            caption=_format_person_report_caption(resolved.person, result.balance),
        )
        return

    if result.outcome == ProcessOutcome.LLM_CONFIRMATION:
        context.chat_data["llm_confirm"] = _llm_pending_from_resolved(resolved, raw.id, text)
        await update.message.reply_text(
            _format_llm_preview(resolved), reply_markup=_llm_confirm_keyboard()
        )
        return

    if result.outcome == ProcessOutcome.PERSON_NOT_FOUND:
        isim = _title_tr(resolved.person_name_raw or "")
        if resolved.kind in _QUERY_ONLY_KINDS:
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
        await _begin_new_person_flow(query, context)
        return
    if data.startswith("person:pick:"):
        person_id = int(data.rsplit(":", 1)[1])
        await _handle_person_pick(query, context, person_id)
        return
    if data == "newperson:confirm":
        await _new_person_confirm_name(query, context)
        return
    if data == "newperson:edit":
        await _new_person_edit_name(query, context)
        return
    if data == "newperson:skip_all":
        await _new_person_skip_all(query, context)
        return
    if data == "newperson:skip":
        await _new_person_skip_field(query, context)
        return
    if data == "newperson:cancel":
        await _new_person_cancel(query, context)
        return
    if data == "llm:yes":
        await _handle_llm_confirm_yes(query, context)
        return
    if data == "llm:fix":
        context.chat_data.pop("llm_confirm", None)
        await query.edit_message_text("Tamam, doğrusunu yazar mısın?")
        return
    if data == "llm:cancel":
        context.chat_data.pop("llm_confirm", None)
        await query.edit_message_text("Tamam, iptal ettim.")
        return
    if data == "report:daily":
        await _handle_report_daily(query, context)
        return
    if data == "report:general":
        await _handle_report_general(query, context)
        return
    if data == "info:balance":
        await _handle_info_balance(query, context)
        return
    if data == "info:card":
        await _handle_info_card(query, context)
        return
    if data == "info:report":
        await _handle_info_report(query, context)
        return
    if data == "edit:yes":
        await _handle_edit_confirm_yes(query, context)
        return
    if data == "edit:no":
        context.chat_data.pop("edit_confirm", None)
        await query.edit_message_text("Tamam, değişiklik yapılmadı.")
        return
    if data.startswith("editfield:"):
        field = data.split(":", 1)[1]
        await _handle_edit_field_pick(query, context, field)
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


async def _handle_report_daily(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = query.message.chat_id
    async with typing_action(context.bot, chat_id):
        async with SessionLocal() as session:
            isletme = await report.isletme_adi(session)
            stats = await report.gunluk_ozet(session)
            pdf = await report.rapor_gunluk(session, isletme)

    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    await query.message.reply_document(
        document=pdf,
        filename=f"rapor_gunluk_{report.today_tr().isoformat()}.pdf",
        caption=_format_daily_report_caption(stats),
    )


async def _handle_report_general(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = query.message.chat_id
    async with typing_action(context.bot, chat_id):
        async with SessionLocal() as session:
            isletme = await report.isletme_adi(session)
            stats = await report.genel_ozet(session)
            pdf = await report.rapor_genel(session, isletme)

    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    await query.message.reply_document(
        document=pdf,
        filename=f"rapor_genel_{report.today_tr().isoformat()}.pdf",
        caption=_format_general_report_caption(stats),
    )


async def _handle_info_balance(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Belirsiz "bilgi ver" menüsünde "Bakiye / borç" seçilince: kişi
    daha önce netleşmişti (info_menu chat_data'sında person_id olarak
    saklı), burada yeniden kişi eşleştirme yapılmaz."""
    info = context.chat_data.pop("info_menu", None)
    if not info:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, info["person_id"])
        if person is None:
            await query.edit_message_text("Kişi bulunamadı.")
            return
        bal = await balance_of(session, person.id)
        txs, total = await list_person_transactions(session, person.id, limit=BALANCE_TABLE_LIMIT)

    await query.edit_message_text(_format_balance(person, bal, txs, total), parse_mode=ParseMode.HTML)


async def _handle_info_card(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    info = context.chat_data.pop("info_menu", None)
    if not info:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, info["person_id"])
        if person is None:
            await query.edit_message_text("Kişi bulunamadı.")
            return

    await query.edit_message_text(_format_person_card(person))


async def _handle_info_report(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    info = context.chat_data.pop("info_menu", None)
    if not info:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    chat_id = query.message.chat_id
    async with typing_action(context.bot, chat_id):
        async with SessionLocal() as session:
            person = await session.get(Person, info["person_id"])
            if person is None:
                await query.edit_message_text("Kişi bulunamadı.")
                return
            isletme = await report.isletme_adi(session)
            pdf = await report.rapor_kisi(session, isletme, person.id)
            bal = await balance_of(session, person.id)

    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    await query.message.reply_document(
        document=pdf,
        filename=f"rapor_ekstre_{report.slugify(person.full_name)}.pdf",
        caption=_format_person_report_caption(person, bal),
    )


async def _begin_new_person_flow(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kişi bulunamadığında ("Ekleyeyim mi?" → Evet) veya adaylardan
    "+ Yeni kişi ekle" seçilince çağrılır: kişiyi hemen açmak yerine ad
    soyadı onaylatıp telefon/il/ilçeyi adım adım sorar (bkz. CLAUDE.md >
    "Telegram'dan kişi eklerken detay sorma")."""
    pending = context.chat_data.pop("pending", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    name = _title_tr(pending.get("person_name_raw") or "")
    context.chat_data["new_person_flow"] = {
        "step": "name",
        "name": name,
        "phone": None,
        "city": None,
        "district": None,
        "pending": pending,
    }
    prompt, keyboard = _new_person_prompt("name", name)
    await query.edit_message_text(prompt, reply_markup=keyboard)


async def _new_person_confirm_name(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.chat_data.get("new_person_flow")
    if not flow:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return
    flow["step"] = "phone"
    prompt, keyboard = _new_person_prompt("phone")
    await query.edit_message_text(prompt, reply_markup=keyboard)


async def _new_person_edit_name(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.chat_data.get("new_person_flow")
    if not flow:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return
    await query.edit_message_text("Ad soyadı yazar mısın?")


async def _new_person_skip_all(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.chat_data.get("new_person_flow")
    if not flow:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return
    await query.edit_message_reply_markup(reply_markup=None)
    await _complete_new_person(query.message, context)


async def _new_person_skip_field(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.chat_data.get("new_person_flow")
    if not flow:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return
    next_step = _new_person_set_field_and_next(flow, None)
    if next_step is None:
        await query.edit_message_reply_markup(reply_markup=None)
        await _complete_new_person(query.message, context)
        return
    prompt, keyboard = _new_person_prompt(next_step)
    await query.edit_message_text(prompt, reply_markup=keyboard)


async def _new_person_cancel(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data.pop("new_person_flow", None)
    await query.edit_message_text("Kişi ekleme iptal edildi.")


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


async def _resolve_and_process(
    session, context, chat_id: int, person: Person, pending: dict
) -> tuple[ResolvedIntent, ProcessResult]:
    resolved = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind=pending["kind"],
        person=person,
        qty=pending["qty"],
        unit=pending["unit"],
        product_name_raw=pending["product_name"],
        amount=pending["amount"],
        field_name=pending.get("field"),
        new_value=pending.get("new_value"),
    )
    if pending["product_name"] and pending["kind"] != "balance_query":
        product, _created = await catalog.resolve_or_create(
            session, pending["product_name"], pending["unit"]
        )
        resolved.product = product

    raw = await session.get(RawMessage, pending["raw_message_id"])
    async with typing_action(context.bot, chat_id):
        result = await message_processor.handle_resolved(session, raw, resolved, pending["raw_text"])
    return resolved, result


async def _finish_pending(session, query, context, person: Person, pending: dict) -> None:
    resolved, result = await _resolve_and_process(session, context, query.message.chat_id, person, pending)

    if result.outcome == ProcessOutcome.BALANCE:
        await query.edit_message_text(
            _format_balance(person, result.balance, result.transactions or [], result.transactions_total or 0),
            parse_mode=ParseMode.HTML,
        )
        return

    if result.outcome == ProcessOutcome.REPORT_PERSON:
        assert result.report_pdf is not None and result.balance is not None
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await query.message.reply_document(
            document=result.report_pdf,
            filename=f"rapor_ekstre_{report.slugify(person.full_name)}.pdf",
            caption=_format_person_report_caption(person, result.balance),
        )
        return

    if result.outcome == ProcessOutcome.PERSON_CONTACT:
        await query.edit_message_text(_format_person_card(person))
        return

    if result.outcome == ProcessOutcome.INFO_MENU:
        context.chat_data["info_menu"] = {"person_id": person.id}
        await query.edit_message_text("Ne bilgisi?", reply_markup=_info_menu_keyboard())
        return

    if result.outcome == ProcessOutcome.ARCHIVE_CONFIRM:
        assert result.balance is not None
        await _prompt_archive_confirm(query.edit_message_text, context, resolved, result.balance)
        return

    if result.outcome == ProcessOutcome.EDIT_PERSON_CONFIRM:
        await _prompt_edit_confirm(query.edit_message_text, context, resolved)
        return

    if result.outcome == ProcessOutcome.EDIT_PERSON_MENU:
        await _prompt_edit_field_menu(query.edit_message_text, context, resolved)
        return

    if result.outcome == ProcessOutcome.CREATE_PERSON:
        # Adaylardan biri seçildi (mevcut kişi) — "hangisi?" sorusuna cevap
        # verildi, yeni bir kişi oluşturulmadı (bkz. _reply_result'taki aynı
        # outcome yorumu).
        await query.edit_message_text(f"ℹ️ {person.full_name} zaten kayıtlı.")
        return

    msg = _format_record_confirmation(resolved, result.balance_before, result.balance)
    context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
    await query.edit_message_text(msg, reply_markup=_undo_keyboard(result.transaction_id))


async def _complete_new_person(msg, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adım adım toplama akışı bitince (onay/hepsini geç/son alanı geç ya
    da son alanı yazınca) çağrılır: kişiyi toplanan bilgilerle oluşturur,
    sonra bekleyen borç/tahsilat işlemini işler. `msg`, hem CallbackQuery'nin
    hem Update'in taşıdığı `telegram.Message` nesnesidir (ikisi de aynı
    reply_text/reply_document arayüzünü sağlar)."""
    flow = context.chat_data.pop("new_person_flow", None)
    if not flow:
        await msg.reply_text("Bu istek artık geçerli değil.")
        return

    pending = flow["pending"]
    async with SessionLocal() as session:
        person = Person(
            full_name=flow["name"],
            phone=flow.get("phone"),
            city=flow.get("city"),
            district=flow.get("district"),
        )
        session.add(person)
        await session.flush()
        resolved, result = await _resolve_and_process(session, context, msg.chat_id, person, pending)
        await session.commit()

    if result.outcome == ProcessOutcome.BALANCE:
        await msg.reply_text(
            _format_balance(person, result.balance, result.transactions or [], result.transactions_total or 0),
            parse_mode=ParseMode.HTML,
        )
        return

    if result.outcome == ProcessOutcome.REPORT_PERSON:
        assert result.report_pdf is not None and result.balance is not None
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await msg.reply_document(
            document=result.report_pdf,
            filename=f"rapor_ekstre_{report.slugify(person.full_name)}.pdf",
            caption=_format_person_report_caption(person, result.balance),
        )
        return

    if result.outcome == ProcessOutcome.PERSON_CONTACT:
        await msg.reply_text(_format_person_card(person))
        return

    if result.outcome == ProcessOutcome.INFO_MENU:
        context.chat_data["info_menu"] = {"person_id": person.id}
        await msg.reply_text("Ne bilgisi?", reply_markup=_info_menu_keyboard())
        return

    if result.outcome == ProcessOutcome.ARCHIVE_CONFIRM:
        # Nadir yol: "+ Yeni kişi ekle" ile arşivleme niyeti için az önce
        # oluşturulmuş bir kişi üzerinde çalışılıyor demektir.
        assert result.balance is not None
        await _prompt_archive_confirm(msg.reply_text, context, resolved, result.balance)
        return

    if result.outcome == ProcessOutcome.CREATE_PERSON:
        # Bu akışta `person` az önce oluşturuldu (yukarıda) — burada her
        # zaman "yeni eklendi" anlamına gelir (bkz. _reply_result'taki
        # "zaten kayıtlı" yorumuyla karşılaştır: orası asla yeni oluşturmaz).
        await msg.reply_text(f"✅ {person.full_name} eklendi.")
        return

    text = _format_record_confirmation(resolved, result.balance_before, result.balance)
    context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
    await msg.reply_text(text, reply_markup=_undo_keyboard(result.transaction_id))


async def _handle_llm_confirm_yes(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """LLM önizlemesinde "Evet": kişi/ürün zaten intent_resolver tarafından
    çözülmüştü (READY), burada yeniden fuzzy-eşleştirme yapılmaz — id'lerle
    geri alınır ve source="rule" ile kaydedilir (kullanıcı zaten onayladı)."""
    pending = context.chat_data.pop("llm_confirm", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, pending["person_id"])
        if person is None:
            await query.edit_message_text("Kişi bulunamadı.")
            return
        product = await session.get(Product, pending["product_id"]) if pending["product_id"] else None

        resolved = ResolvedIntent(
            status=ResolutionStatus.READY,
            kind=pending["kind"],
            person=person,
            qty=pending["qty"],
            unit=pending["unit"],
            product=product,
            amount=pending["amount"],
        )

        raw = await session.get(RawMessage, pending["raw_message_id"])
        result = await message_processor.handle_resolved(
            session, raw, resolved, pending["raw_text"], source="rule"
        )
        await session.commit()

    msg = _format_record_confirmation(resolved, result.balance_before, result.balance)
    context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
    await query.edit_message_text(msg, reply_markup=_undo_keyboard(result.transaction_id))


async def _handle_edit_confirm_yes(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """NET komut onayında "Evet": alan+değer zaten belliydi
    (chat_data["edit_confirm"]), burada person_edit.update_person_field
    çağrılır (CLAUDE.md > "Silme mesajı + kişi düzenleme — Grup 4")."""
    pending = context.chat_data.pop("edit_confirm", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    name = await _apply_edit_person_field(pending["person_id"], pending["field"], pending["value"])
    if name is None:
        await query.edit_message_text("Kişi bulunamadı.")
        return
    await query.edit_message_text(_format_edit_result(name, pending["field"], pending["value"]))


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


async def _warmup_llm() -> None:
    """Polling başlamadan önce modeli belleğe alır: Ollama boştayken modeli
    düşürüyor (keep_alive), ilk gerçek mesaj bu yüzden soğuk (60sn+) yükleme
    süresine denk geliyordu (CLAUDE.md ölçümü). Bu çağrı sonucu kullanılmaz,
    yalnızca modeli ısıtır. Hata olursa yutulur — bot LLM'siz de başlar."""
    provider = llm_provider.get_provider()
    if provider is None:
        return
    try:
        await provider.parse("ısınma")
    except Exception:
        logger.warning("LLM ısınma çağrısı başarısız oldu", exc_info=True)


async def _on_startup(application: Application) -> None:
    await _set_commands(application)
    await _warmup_llm()


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN tanımlı değil, bot başlatılamaz")

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_on_startup)
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
