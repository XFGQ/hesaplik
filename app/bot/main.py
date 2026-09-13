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
komutun varlığı bile sızmasın. /yedek yalnızca TELEGRAM_ADMIN_CHAT_ID'de
çalışır; başkasına (kullanıcı kararıyla) "Bu komut sadece yönetici içindir."
denir. "/" komut menüsü: aşağıda '"/" komut menüsü' bölümü.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import time
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
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
from app.services import (
    catalog,
    durum,
    llm_provider,
    message_processor,
    message_splitter,
    parser,
    person_archive,
    person_edit,
    report,
    saman_fiyat,
    stt,
    telegram_yedek,
)
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent, find_person_match
from app.services.ledger import Balance, LedgerError, balance_of
from app.services.ledger import reverse as ledger_reverse
from app.services.message_processor import BALANCE_TABLE_LIMIT, ProcessOutcome, ProcessResult
from app.services.parser import ParsedIntent
from app.services.person_edit import PersonEditError
from app.services.queries import (
    PersonBalanceRow,
    PersonTransactionRow,
    TotalBalance,
    list_person_transactions,
)
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
    'Bakiye sormak için: "Ahmet borcu ne kadar".\n'
    '\n'
    'Komutlar ("/" yazınca liste açılır):\n'
    '/borc — borç kaydı, adım adım sorar\n'
    '/tahsilat — tahsilat kaydı, adım adım sorar\n'
    '/bakiye — defter toplamı · /bakiye ahmet — kişinin bakiyesi\n'
    '/listele — tüm kişiler ve bakiyeleri\n'
    '/kisi ahmet — kişi ara\n'
    '/koy bergama — ilçedeki kişiler\n'
    '/kisiekle — yeni kişi ekle'
)

ANLASILAMADI_METNI = (
    "Tam anlayamadım. Örnek: \"Ahmet 20 balya saman aldı 1500 lira borç\".\n"
    "Ya da uygulamadan elle girebilirsin."
)

VOICE_KAPALI_METNI = "Sesli mesaj desteği şu an kapalı, yazarak gönderir misin?"
VOICE_ANLASILAMADI_METNI = "Sesi anlayamadım, yazarak gönderir misin?"

# Salt okunur sorgu niyetleri: bulunamayan kişi için "Ekleyeyim mi?"
# sorulmaz (bkz. PERSON_NOT_FOUND kolları), sadece "defterde yok" denir —
# bunlar hiçbir şey kaydetmediği için yeni kişi açmak anlamsız. archive_*
# de burada: olmayan birini arşivlemek/silmek anlamsız, "Ekleyeyim mi?"
# sorusu kafa karıştırır (CLAUDE.md > "Bot kişi silme = arşivleme — Grup 3").
# edit_person de aynı sebeple burada (CLAUDE.md > "Silme mesajı + kişi
# düzenleme — Grup 4"): olmayan birinin bilgisini düzenlemek anlamsız.
# "delete_ambiguous" de burada (CLAUDE.md > "'sil' bağlam ayrımı"): kişi
# defterde yoksa "Ekleyeyim mi?" sorulmaz — ortada ne bir tahsilat ne bir
# silme vardır, yeni kişi açmak anlamsız olurdu.
_QUERY_ONLY_KINDS = (
    "balance_query", "report_person", "person_contact", "info_menu",
    "archive_person", "archive_and_recreate", "edit_person", "delete_ambiguous",
)

# Tek mesajda birden çok işlem (CLAUDE.md > "Tek mesajda birden çok istek",
# Grup 5): işlemler SIRAYLA işlenir. Bir işlem kullanıcıdan onay/seçim
# beklemeye başlarsa (chat_data'ya "pending" benzeri bir anahtar yazan her
# outcome) SIRADAKİ işlem OTOMATİK işlenmez — karmaşık bir kuyruk kurulmaz
# (CLAUDE.md: "ilk sürümde onay gerektirmeyen net işlemler sırayla
# işlensin, onay gerekeni ayrıca ele al"). Bu küme, chat_data'da bekleyen
# bir şey BIRAKMADAN tamamlanan outcome'ların AÇIK listesidir (allowlist) —
# ileride eklenecek yeni bir outcome buraya eklenmediği sürece güvenli
# tarafta kalır: çoklu-işlem döngüsü onda durur, otomatik devam etmez.
_COMPLETES_WITHOUT_INPUT = frozenset({
    ProcessOutcome.RECORDED,
    ProcessOutcome.CREATE_PERSON,
    ProcessOutcome.BALANCE,
    ProcessOutcome.LIST,
    ProcessOutcome.SEARCH,
    ProcessOutcome.TOTAL_BALANCE,
    ProcessOutcome.REPORT_DAILY,
    ProcessOutcome.REPORT_GENERAL,
    ProcessOutcome.REPORT_PERSON,
    ProcessOutcome.PERSON_CONTACT,
    ProcessOutcome.PRODUCT_QUERY_UNSUPPORTED,
    ProcessOutcome.UNRECOGNIZED,
})

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
    # Tutar varsayılan saman fiyatından geldiyse kullanılan fiyat açıkça
    # yazılır (CLAUDE.md > "Varsayılan saman fiyatı"): yanlışsa kullanıcı
    # "Geri al" ile düzeltir.
    fiyat = ""
    if resolved.default_unit_price is not None:
        fiyat = (
            f"Birim fiyat: {_fmt_try(resolved.default_unit_price)} TL/"
            f"{resolved.unit or parser.SAMAN_BIRIM} (varsayılan saman fiyatı)\n"
        )
    return (
        f"✅ {resolved.person.full_name}\n"
        f"{_format_record_summary(resolved)} {kind_word} eklendi\n"
        f"{fiyat}"
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


def _format_total_balance(total: TotalBalance) -> str:
    """Defterin TAMAMININ özeti (CLAUDE.md > "Toplam bakiye niyeti"):
    "toplam borç"/"tüm bakiye" sorularının cevabı. İşaret yönü kişi
    satırlarıyla aynı — pozitif bakiye kişinin SANA borcu (senin alacağın),
    negatif bakiye senin ona borcun."""
    net = total.net
    if net > 0:
        net_satir = f"Net: {_fmt_try(net)} TL alacaklısın"
    elif net < 0:
        net_satir = f"Net: {_fmt_try(-net)} TL borçlusun"
    else:
        net_satir = "Net: hesap sıfır"
    return (
        f"📊 Defter toplamı ({total.kisi_sayisi} kişi)\n"
        f"Toplam alacak: {_fmt_try(total.toplam_alacak)} TL "
        f"({total.borclu_sayisi} kişi sana borçlu)\n"
        f"Toplam borç: {_fmt_try(total.toplam_borc)} TL "
        f"({total.alacakli_sayisi} kişi senden alacaklı)\n"
        f"{net_satir}"
    )


def _format_delete_ambiguous(person_full_name: str) -> str:
    """"{isim} ... borcunu ödedi sil" — niyet belirsiz (CLAUDE.md > "'sil'
    bağlam ayrımı"). ASLA sessizce kişi silme sanılmaz, sorulur."""
    return (
        f"{person_full_name} için ne yapayım?\n"
        "Borcu kapatan bir tahsilat mı girmek istedin, yoksa kişiyi mi silmek?"
    )


def _delete_ambiguous_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Tahsilat gir", callback_data="delete:payment")],
            [InlineKeyboardButton("Kişiyi sil", callback_data="delete:person")],
            [InlineKeyboardButton("İptal", callback_data="delete:cancel")],
        ]
    )


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


def _candidates_keyboard(candidates: list[Person], prefix: str = "person") -> InlineKeyboardMarkup:
    """`prefix`: "person" bekleyen niyeti tamamlar (_handle_person_pick);
    "komut" /borc-/tahsilat'ta kişiyi seçip mal sorusuna geçer."""
    rows = [[InlineKeyboardButton(p.full_name, callback_data=f"{prefix}:pick:{p.id}")] for p in candidates]
    rows.append([InlineKeyboardButton("+ Yeni kişi ekle", callback_data=f"{prefix}:new")])
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


async def _archive_onay_kelimesi(session: AsyncSession) -> str:
    """Onay kelimesi ÇAĞIRANIN session'ıyla okunur — bu fonksiyon kendi
    bağlantısını AÇMAZ: web chat (app/services/web_chat.py) kendi
    session'ında çalışır ve buradan açılacak ikinci bir bağlantı hem
    tutarsız olur hem de test ortamında yanlış veritabanına gider."""
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


async def _prompt_archive_confirm(session: AsyncSession, reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent, bal: Balance) -> None:
    """`reply`, hem Message.reply_text hem CallbackQuery.edit_message_text
    olabilir (ikisi de aynı imzayla metin gönderir) — direkt metinden mi
    ("furkanı sil") yoksa aday seçiminden mi (_finish_pending) geldiği fark
    etmez, onay isteme mantığı tek yerde."""
    onay = await _archive_onay_kelimesi(session)
    context.chat_data["archive_confirm"] = {
        "person_id": resolved.person.id,
        "kind": resolved.kind,
        "onay_kelimesi": onay,
    }
    await reply(_format_archive_confirm(resolved.person.full_name, bal, onay))


async def _prompt_delete_ambiguous(
    reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent,
    raw_message_id: int, raw_text: str,
) -> None:
    """"sil" + para/mal bağlamı: niyet belirsiz, üç butonla sorulur
    (CLAUDE.md > "'sil' bağlam ayrımı"). `reply` hem Message.reply_text hem
    CallbackQuery.edit_message_text olabilir — cümleden mi ("...ödedi sil")
    yoksa aday seçiminden mi (_finish_pending) gelindiği fark etmez.

    Silme fiili ayıklanmış metin ŞİMDİ saklanır: kullanıcı "Tahsilat gir"
    derse o metin normal akıştan yeniden geçirilir."""
    context.chat_data["delete_ambiguous"] = {
        "person_id": resolved.person.id,
        "raw_message_id": raw_message_id,
        "text": parser.strip_delete_words(raw_text),
    }
    await reply(
        _format_delete_ambiguous(resolved.person.full_name),
        reply_markup=_delete_ambiguous_keyboard(),
    )


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
        await _advance_queue(context, update.message)
        return
    await update.message.reply_text(_format_edit_result(name, flow["field"], value))
    await _advance_queue(context, update.message)


# --------------------------------------------------------------- ürün yazım düzeltme (fuzzy)
#
# "ahmet 20 samaan aldı 5000 tl borç" gibi bir kayıtta ürün adı mevcut bir
# ürüne yakınsa ("saman") sessizce yeni ürün AÇILMAZ — kullanıcıya sorulur:
# "'samaan' → 'saman' mı? [Evet] [Hayır, yeni ürün] [İptal]" (CLAUDE.md >
# "Ürün yazım düzeltme (fuzzy)", Grup 5). Kişi bu noktada zaten netleşmiş
# durumda (chat_data["product_confirm"] person_id taşır), yalnızca ürün
# bekletiliyor — kayıt hiç yapılmadı, onay/redde göre tamamlanır.


def _product_suggestion_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Evet", callback_data="product:yes")],
            [InlineKeyboardButton("Hayır, yeni ürün", callback_data="product:new")],
            [InlineKeyboardButton("İptal", callback_data="product:cancel")],
        ]
    )


def _format_product_suggestion(raw_name: str, suggestion_name: str) -> str:
    return f"'{raw_name}' → '{suggestion_name}' mı?"


async def _prompt_product_confirm(
    reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent, raw_message_id: int, raw_text: str
) -> None:
    """`reply` hem Message.reply_text hem CallbackQuery.edit_message_text
    olabilir (bkz. _prompt_archive_confirm'daki aynı desen). Kişi zaten
    netleşmiş (resolved.person dolu); yalnızca ürün bekletiliyor."""
    context.chat_data["product_confirm"] = {
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
    }
    msg = _format_product_suggestion(resolved.product_name_raw, resolved.product_suggestion.name)
    await reply(msg, reply_markup=_product_suggestion_keyboard())


async def _handle_product_confirm(query, context: ContextTypes.DEFAULT_TYPE, use_suggestion: bool) -> None:
    """"Evet" -> öneriye bağla (yeni ürün AÇMADAN). "Hayır, yeni ürün" ->
    kullanıcının yazdığı ham adla gerçekten yeni ürün açar (kullanıcı öneriyi
    reddettiği için artık otomatik temizleme/eşleştirme uygulanmaz)."""
    pending = context.chat_data.pop("product_confirm", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, pending["person_id"])
        if person is None:
            await query.edit_message_text("Kişi bulunamadı.")
            await _advance_queue(context, query.message)
            return

        if use_suggestion:
            product = await session.get(Product, pending["suggestion_id"])
        else:
            product, _created = await catalog.resolve_or_create(
                session, pending["product_name_raw"], pending["unit"]
            )

        resolved = ResolvedIntent(
            status=ResolutionStatus.READY,
            kind=pending["kind"],
            person=person,
            qty=pending["qty"],
            unit=pending["unit"],
            product_name_raw=pending["product_name_raw"],
            product=product,
            amount=pending["amount"],
            running=pending.get("running", False),
        )

        raw = await session.get(RawMessage, pending["raw_message_id"])
        result = await message_processor.handle_resolved(
            session, raw, resolved, pending["raw_text"], source="rule"
        )
        await session.commit()

    if result.outcome == ProcessOutcome.SAMAN_PRICE_CONFIRM:
        await _prompt_saman_price_confirm(
            query.edit_message_text, context, resolved,
            pending["raw_message_id"], pending["raw_text"],
        )
        return

    if result.outcome == ProcessOutcome.RUNNING_AMOUNT_NEEDED:
        # Koşan formatta ürün de netleşti ama tutar hâlâ söylenmemiş: kayıt
        # yapılmadan tutar sorulur (kuyruk İLERLETİLMEZ, parça hâlâ açık).
        await _prompt_running_amount(
            query.edit_message_text, context, resolved,
            pending["raw_message_id"], pending["raw_text"],
        )
        return

    msg = _format_record_confirmation(resolved, result.balance_before, result.balance)
    context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
    await query.edit_message_text(msg, reply_markup=_undo_keyboard(result.transaction_id))
    await _advance_queue(context, query.message)


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
        # Koşan format: kişi seçildikten SONRA da tutarın sorulması gerekir
        # (bkz. _resolve_and_process) — bayrak taşınmazsa adedi belli ama
        # tutarı belli olmayan bir kayıt sessizce fiyat listesine düşerdi.
        "running": resolved.running,
        # Varsayılan saman fiyatı: "furkan 20" iki Furkan'a uyuyorsa kişi
        # seçildikten SONRA da "saman borç mu?" sorusu sorulmalı — bayraklar
        # taşınmazsa varsayılan ürün/yön sessizce kaydedilirdi.
        "assumed_product": resolved.assumed_product,
        "assumed_kind": resolved.assumed_kind,
    }


def _llm_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Evet", callback_data="llm:yes"),
            InlineKeyboardButton("Düzelt", callback_data="llm:fix"),
            InlineKeyboardButton("İptal", callback_data="llm:cancel"),
        ]]
    )


PRODUCT_QUERY_UNSUPPORTED_MESSAGE = (
    "Ürün sorguları (stok, fiyat, toplam satış) henüz yok. "
    "Şu an defter kişi bazlı borç, tahsilat ve raporlar tutuyor."
)


def _format_product_query_unsupported(resolved: ResolvedIntent) -> str:
    """Ürün/stok/fiyat sorgusu (Grup C): niyet ANLAŞILDI ama karşılığı yok.
    Sessizce yanlış bir cevap vermektense açıkça söylenir — kullanıcı ne
    sorduğunun anlaşıldığını görsün diye ürün adı da tekrarlanır."""
    urun = _title_tr(resolved.product_name_raw or "")
    if urun:
        return f"{urun}: {PRODUCT_QUERY_UNSUPPORTED_MESSAGE}"
    return PRODUCT_QUERY_UNSUPPORTED_MESSAGE


def _format_close_debt_preview(resolved: ResolvedIntent, balance: Balance) -> str:
    """Tutarı söylenmemiş borç kapanışı ("ali borcunu ödedi"): tutar
    uydurulmaz, güncel bakiye TEKLİF edilir ve onaylatılır."""
    return (
        f"{_genitive(resolved.person.full_name)} borcu {_fmt_try(balance.balance_try)} TL. "
        "Tamamı tahsilat olarak yazılsın mı?"
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
        # Onaylanınca kayıt mesajı kullanılan varsayılan fiyatı göstersin.
        "default_unit_price": resolved.default_unit_price,
    }


def _format_saman_price_preview(resolved: ResolvedIntent) -> str:
    """Tutar varsayılan saman fiyatından hesaplandı ama kayıttan önce
    soruluyor (CLAUDE.md > "Varsayılan saman fiyatı"). Fiyat ve tutar
    görünür; yanlışsa kullanıcı Düzelt'e basıp tutarıyla yeniden yazar.
    Ürün hiç yazılmadıysa ("furkan 20") soru "... mu demek istediniz?"."""
    kind_word = "borç" if resolved.kind == "debt" else "tahsilat"
    fiyat = (
        f"{_fmt_try(resolved.default_unit_price)} TL/{resolved.unit or parser.SAMAN_BIRIM} = "
        f"{_fmt_try(resolved.amount)} TL"
    )
    kime = _dative(resolved.person.full_name)
    kalem = _format_item_summary(resolved)
    if resolved.assumed_product:
        soru = "mu" if resolved.kind == "debt" else "mı"
        return f"{kime} {kalem} {kind_word} {soru} demek istediniz?\n({fiyat})"
    return f"{kime} {kalem} ({fiyat}) {kind_word} ekleyeyim mi?"


async def _prompt_saman_price_confirm(
    reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent, raw_message_id: int, raw_text: str
) -> None:
    """Onay makinesi LLM önizlemesiyle AYNI (chat_data["llm_confirm"],
    Evet/Düzelt/İptal) — yalnızca sorulan cümle farklı."""
    context.chat_data["llm_confirm"] = _llm_pending_from_resolved(resolved, raw_message_id, raw_text)
    await reply(_format_saman_price_preview(resolved), reply_markup=_llm_confirm_keyboard())

# --------------------------------------------------------------- koşan format
#
# CLAUDE.md > "Koşan format": "ahmet 70-20-50 saman" = 70 vardı, 20 değişti,
# 50 oldu -> 20 balya saman BORÇ (azalış). Üçlü YALNIZCA mal adedini söyler;
# TL ayrı girilir. Bu yüzden iki ayrı soru doğabilir:
#
#   RUNNING_MISMATCH      "70-25-50" — 70'ten 50'ye fark 20 olmalı ama 25
#                         yazılmış. Hiçbir şey kaydedilmez. [Fark 20 olsun]
#                         denirse cümle parser.correct_running_text ile
#                         düzeltilip NORMAL akıştan yeniden geçirilir —
#                         ikinci bir kayıt mantığı yazılmaz.
#   RUNNING_AMOUNT_NEEDED adet net ama tutar söylenmemiş. Tutar UYDURULMAZ
#                         (fiyat listesinden de türetilmez, kural 4), yazarak
#                         sorulur. Gelen metin Türkçe sayı olarak çözülür
#                         ("5000", "5 bin", "5000 tl").


def _running_ok_label(resolved: ResolvedIntent) -> str:
    return f"Fark {_fmt_decimal(resolved.qty)} olsun"


def _running_fix_keyboard(resolved: ResolvedIntent) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_running_ok_label(resolved), callback_data="running:fix")],
            [InlineKeyboardButton("İptal", callback_data="running:cancel")],
        ]
    )


def _format_running_mismatch(resolved: ResolvedIntent) -> str:
    """Matematik uyarısı: ne yazıldığı, ne olması gerektiği ve kararın
    kullanıcıda olduğu açıkça söylenir. Hiçbir şey kaydedilmedi."""
    return (
        f"Sayılar tutmuyor: {_fmt_decimal(resolved.running_before)} → "
        f"{_fmt_decimal(resolved.running_after)} için fark "
        f"{_fmt_decimal(resolved.qty)} olmalı ama "
        f"{_fmt_decimal(resolved.running_change)} yazdınız. Hangisi doğru?\n"
        "Kayıt yapmadım."
    )


def _format_running_summary(resolved: ResolvedIntent) -> str:
    """"70 → 50 · 20 balya saman borç" — koşan formatın ne anlaşıldığı."""
    yon = "borç" if resolved.kind == "debt" else "tahsilat"
    birim = f" {resolved.unit}" if resolved.unit else ""
    urun = resolved.product.name if resolved.product else (resolved.product_name_raw or "")
    return (
        f"{_fmt_decimal(resolved.running_before)} → {_fmt_decimal(resolved.running_after)} · "
        f"{_fmt_decimal(resolved.qty)}{birim} {urun} {yon}"
    )


def _format_running_amount_prompt(resolved: ResolvedIntent) -> str:
    return f"{_format_running_summary(resolved)}\nTutar kaç TL?"


async def _prompt_running_fix(
    reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent, raw_message_id: int, raw_text: str
) -> None:
    context.chat_data["running_fix"] = {
        "raw_message_id": raw_message_id,
        "raw_text": raw_text,
    }
    await reply(_format_running_mismatch(resolved), reply_markup=_running_fix_keyboard(resolved))


async def _prompt_running_amount(
    reply, context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedIntent, raw_message_id: int, raw_text: str
) -> None:
    """Kişi ve ürün zaten çözülmüş (READY); yalnızca tutar bekletiliyor.
    Bekleyen kayıt LLM önizlemesiyle AYNI şekli taşır (id'ler, yeniden
    fuzzy eşleştirme yok)."""
    context.chat_data["running_amount"] = _llm_pending_from_resolved(resolved, raw_message_id, raw_text)
    await reply(_format_running_amount_prompt(resolved))


def parse_amount_reply(text: str) -> Decimal | None:
    """Tutar sorusuna gelen serbest cevabı çözer: "5000", "5.000", "5 bin",
    "5000 tl", "5bin lira". Sayı çıkmazsa (ya da sıfır/negatifse) None —
    uydurma yok, tekrar sorulur."""
    temiz = " ".join(
        kelime
        for kelime in parser.normalize(text or "").split()
        if kelime not in parser.CURRENCY_UNITS
    )
    value = parser.parse_turkish_number(temiz)
    if value is None or value <= 0:
        return None
    return value


async def _handle_running_amount_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict, text: str
) -> None:
    """Tutar cevabı: çözülemezse HİÇBİR ŞEY kaydedilmez, soru tekrarlanır
    (bekleyen kayıt yerinde kalır) — yanlış bir tutar yazmaktansa sormak."""
    amount = parse_amount_reply(text)
    if amount is None:
        await update.message.reply_text("Tutarı anlayamadım. Sadece rakamla yazar mısın? (örn. 5000)")
        return

    context.chat_data.pop("running_amount", None)

    async with SessionLocal() as session:
        person = await session.get(Person, pending["person_id"])
        if person is None:
            await update.message.reply_text("Kişi bulunamadı.")
            await _advance_queue(context, update.message)
            return
        product = await session.get(Product, pending["product_id"]) if pending["product_id"] else None

        resolved = ResolvedIntent(
            status=ResolutionStatus.READY,
            kind=pending["kind"],
            person=person,
            qty=pending["qty"],
            unit=pending["unit"],
            product=product,
            amount=amount,
        )
        raw = await session.get(RawMessage, pending["raw_message_id"])
        result = await message_processor.handle_resolved(
            session, raw, resolved, pending["raw_text"], source="rule"
        )
        await session.commit()

    msg = _format_record_confirmation(resolved, result.balance_before, result.balance)
    context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
    await update.message.reply_text(msg, reply_markup=_undo_keyboard(result.transaction_id))
    await _advance_queue(context, update.message)


async def _handle_running_fix(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """"Fark N olsun": orta sayı |ilk - son|'a eşitlenmiş metin NORMAL
    akıştan yeniden geçirilir (bkz. _handle_delete_ambiguous_payment'taki
    aynı desen) — kişi/ürün/tutar çözümü ikinci kez YAZILMAZ."""
    pending = context.chat_data.pop("running_fix", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    duzeltilmis = parser.correct_running_text(pending["raw_text"])
    if duzeltilmis is None:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        await _advance_queue(context, query.message)
        return

    async with SessionLocal() as session:
        raw = await session.get(RawMessage, pending["raw_message_id"])
        result = await message_processor.process_raw_message(session, raw, duzeltilmis)
        await session.commit()
        await query.edit_message_reply_markup(reply_markup=None)
        await _reply_outcome(session, context, result, raw, duzeltilmis, query.message)

    if result.outcome in _COMPLETES_WITHOUT_INPUT:
        await _advance_queue(context, query.message)



def _is_admin(chat_id: int | None) -> bool:
    """Yönetici = TELEGRAM_ADMIN_IDS ∪ TELEGRAM_ADMIN_CHAT_ID — "/" menüsünde
    /durum'u gösteren kümeyle (_admin_chat_idleri) AYNI. Eskiden yalnızca
    TELEGRAM_ADMIN_IDS'e bakılıyordu: yalnızca TELEGRAM_ADMIN_CHAT_ID'si
    tanımlı yönetici /durum'u menüde görüyor ama komut sessiz kalıyordu."""
    return chat_id is not None and chat_id in _admin_chat_idleri()


# --------------------------------------------------------------- komutlar

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MUSTERI_KARSILAMA)


async def cmd_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(YARDIM_METNI)


async def cmd_durum(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    if not _is_admin(chat_id):
        # Yetkisiz kişiye komutun varlığı bile sızmasın; iz yalnızca logda
        # (yanlış yapılandırmayı teşhis etmek için).
        logger.warning("yetkisiz /durum denemesi: chat_id=%s", chat_id)
        return

    async with SessionLocal() as session, typing_action(context.bot, chat_id):
        metin = await durum.build_durum_raporu(session)
    await update.message.reply_text(metin)


# --------------------------------------------------------------- "/" komut menüsü
#
# Telegram'da "/" yazınca çıkan öneri listesi (kayıt: _set_commands →
# setMyCommands). İki tür komut var:
#
#   DİREKT    /yedek, /bakiye ve argümanlı /kisi, /koy — tek adımda biter.
#   ADIM ADIM /borc, /tahsilat, /kisiekle ve argümansız /kisi, /koy — eksik
#             bilgiyi sorar; bekleyen soru chat_data["komut_akisi"]'nda durur,
#             cevabını on_text yakalar (bkz. oradaki komut_akisi kolu).
#
# Komutlar KENDİ kayıt/onay mantıklarını KURMAZ, mevcut olanı TETİKLER: kişi
# eşleştirme güvenliği find_person_match'ten, "hangisi?" _candidates_keyboard'
# dan, yeni kişi _new_person_flow_baslat'tan, kaydın kendisi
# message_processor.process_intent → handle_resolved'dan geçer. Böylece
# varsayılan saman fiyatı, koşan format, ürün önerisi, çoklu istek kuyruğu ve
# 60 sn "Geri al" komut yolunda da aynen çalışır — ikinci bir defter mantığı
# yazılmadı.

KOMUT_ADMIN_REDDI = "Bu komut sadece yönetici içindir."

_KOMUT_KISI_SORUSU = {
    "debt": "Kimin borcunu eklemek istiyorsun? Adını yaz.",
    "payment": "Kimden tahsilat aldın? Adını yaz.",
}

_KOMUT_KAYIT_SORUSU = {
    "debt": "ne aldı, ne kadar? (örn. 20 balya saman 5000 tl)",
    "payment": "ne ödedi, ne kadar? (örn. 5000 tl)",
}


def _komut_argumani(context: ContextTypes.DEFAULT_TYPE) -> str:
    """"/bakiye ahmet yılmaz" -> "ahmet yılmaz" (argümansızsa "")."""
    return " ".join(getattr(context, "args", None) or []).strip()


def _komut_temizle(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Yeni bir komut, yarım kalmış ESKİ bir komut sorusunu düşürür: kullanıcı
    "/borc" deyip cevaplamadan "/bakiye" yazarsa, sonraki mesajı borç sorusunun
    cevabı sanmayalım."""
    context.chat_data.pop("komut_akisi", None)
    context.chat_data.pop("komut_secim", None)


async def _komut_raw_id(update: Update) -> int:
    """Komutla gelen mesaj da işlenmeden ÖNCE raw_messages'a yazılır (mesaj
    asla kaybolmaz — CLAUDE.md > Faz 3); id'si kayıt/izleme için döner."""
    async with SessionLocal() as session:
        raw = await save_raw_message(session, update.to_dict())
        await session.commit()
        return raw.id


async def _komut_intent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    intent: ParsedIntent,
    *,
    person: Person | None = None,
    text: str | None = None,
) -> None:
    """Niyeti KOMUTTAN belli bir isteği normal işleme hattından geçirir:
    parser'a (ve LLM'e) yeniden tahmin ettirilmez, ama kişi eşleştirme
    güvenliği, saman fiyatı, onay akışları ve kayıt aynen düz metindeki
    gibi çalışır (message_processor.process_intent)."""
    message = update.message
    ham = text if text is not None else (message.text or "")

    async with SessionLocal() as session:
        raw = await save_raw_message(session, update.to_dict())
        await session.commit()
        async with typing_action(context.bot, message.chat_id):
            result = await message_processor.process_intent(
                session, raw, intent, ham, person=person
            )
            await session.commit()
        await _reply_outcome(session, context, result, raw, ham, message)


# ---- /yedek (DİREKT, yalnızca yönetici)

async def cmd_yedek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tüm veritabanını gzip'leyip AYNI sohbete dosya olarak gönderir
    (app/services/telegram_yedek.py — host'taki scripts/telegram-yedek.sh'nin
    ikizi). Yalnızca TELEGRAM_ADMIN_CHAT_ID kullanabilir; başkası çağırırsa
    /durum'un aksine sessiz kalınmaz, açıkça "sadece yönetici" denir
    (kullanıcı kararı) — komutun varlığı zaten menüde yalnızca yöneticiye
    gösteriliyor. Her deneme audit_log'a yazılır: veritabanının TAMAMI
    dışarı çıkıyor."""
    _komut_temizle(context)
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    admin_chat = settings.telegram_admin_chat_id_int

    if admin_chat is None or chat_id != admin_chat:
        await update.message.reply_text(KOMUT_ADMIN_REDDI)
        return

    await update.message.reply_text("Yedek alınıyor…")
    async with typing_action(context.bot, chat_id, ChatAction.UPLOAD_DOCUMENT):
        sonuc = await telegram_yedek.yedek_gonder(admin_chat)

    async with SessionLocal() as session:
        await telegram_yedek.sonucu_kaydet(session, f"telegram:{chat_id}", sonuc)
        await session.commit()

    # Başarılıysa dosyanın kendisi (başlığında tarih + boyut) zaten geldi,
    # üstüne ikinci bir "gönderildi" mesajı yazılmaz.
    if not sonuc.ok:
        await update.message.reply_text(f"Yedek alınamadı. {sonuc.mesaj}")


# ---- /bakiye (DİREKT)

async def cmd_bakiye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Argümansız: defterin TAMAMININ özeti (total_balance). "/bakiye ahmet":
    o kişinin bakiye tablosu — kişi belirsizse her zamanki "hangisi?" sorulur,
    hiç yoksa "defterde yok" denir (salt okunur sorgu, kişi açılması teklif
    edilmez)."""
    _komut_temizle(context)
    isim = _komut_argumani(context)
    if not isim:
        await _komut_intent(update, context, ParsedIntent(kind="total_balance"))
        return
    await _komut_intent(update, context, ParsedIntent(kind="balance_query", person_name=isim))


# ---- /listele (DİREKT)

async def cmd_listele(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tüm kişiler + bakiyeleri — "kişileri listele" yazmakla aynı list_all
    niyeti ve aynı çıktı."""
    _komut_temizle(context)
    await _komut_intent(update, context, ParsedIntent(kind="list_all"))


# ---- /kisi ve /koy (argümanlıysa DİREKT, argümansızsa tek soru)

async def cmd_kisi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _komut_temizle(context)
    terim = _komut_argumani(context)
    if not terim:
        context.chat_data["komut_akisi"] = {"tip": "kisi"}
        await update.message.reply_text("Hangi kişiyi arıyorsun? Adını yaz.")
        return
    await _komut_ara(update, context, terim)


async def _komut_ara(update: Update, context: ContextTypes.DEFAULT_TYPE, terim: str) -> None:
    """Mevcut arama akışı (CLAUDE.md > "Telegram arama gibi davransın"):
    isimde/soyadda/ilçede eşleşen HERKESİ bakiyesiyle listeler, "hangisi?"
    diye sormaz."""
    await _komut_intent(update, context, ParsedIntent(kind="search", query=terim), text=terim)


async def cmd_koy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _komut_temizle(context)
    ilce = _komut_argumani(context)
    if not ilce:
        context.chat_data["komut_akisi"] = {"tip": "koy"}
        await update.message.reply_text("Hangi ilçe?")
        return
    await _komut_ilce(update, context, ilce)


async def _komut_ilce(update: Update, context: ContextTypes.DEFAULT_TYPE, ilce: str) -> None:
    # district, parser'ın ürettiğiyle AYNI biçimde (normalize, küçük harf)
    # verilir — sorgu tarafı iki farklı yazımla uğraşmasın.
    await _komut_intent(
        update, context, ParsedIntent(kind="list_district", district=parser.normalize(ilce)), text=ilce
    )


# ---- /borc ve /tahsilat (ADIM ADIM: kişi -> mal/tutar)

async def cmd_borc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _komut_kayit_baslat(update, context, "debt")


async def cmd_tahsilat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _komut_kayit_baslat(update, context, "payment")


async def _komut_kayit_baslat(
    update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str
) -> None:
    _komut_temizle(context)
    isim = _komut_argumani(context)
    if not isim:
        context.chat_data["komut_akisi"] = {"tip": "kisi_sec", "kind": kind}
        await update.message.reply_text(_KOMUT_KISI_SORUSU[kind])
        return
    await _komut_kisi_coz(update, context, kind, isim)


def _komut_pending(
    kind: str, isim: str, raw_id: int, raw_text: str, *, komut_kind: str | None = None
) -> dict:
    """Komut içinde açılacak adım adım kişi ekleme akışının bekleyen kaydı —
    şekli _pending_from_resolved ile AYNI (aynı akış tüketiyor).

    `komut_kind` dolu ise (/borc, /tahsilat) _complete_new_person bunu görüp
    "kişi açıldı ama kaydedilecek bir şey henüz yok, mal sorusuna geç" der;
    /kisiekle'de boştur — hedef kişinin kendisidir."""
    pending = {
        "kind": kind,
        "person_name_raw": isim,
        "qty": None,
        "unit": None,
        "product_name": None,
        "amount": None,
        "raw_message_id": raw_id,
        "raw_text": raw_text,
        "field": None,
        "new_value": None,
        "running": False,
        "assumed_product": False,
        "assumed_kind": False,
    }
    if komut_kind is not None:
        pending["komut_kind"] = komut_kind
    return pending


async def _komut_kisi_coz(
    update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, isim: str
) -> None:
    """Komutta da kişi eşleştirme güvenliği aynıdır (CLAUDE.md > "Kişi
    eşleştirme güvenliği"): birebir ya da belirgin tek eşleşme yoksa ASLA
    otomatik seçilmez — adaylar sorulur, hiç aday yoksa adım adım kişi ekleme
    teklif edilir. Burada LLM'e danışılmaz: kullanıcı zaten ismi yazmak için
    sorulmuş bir soruya cevap veriyor, tahmin etmek yerine sormak ucuz."""
    message = update.message
    raw_id = await _komut_raw_id(update)

    async with SessionLocal() as session:
        person, candidates = await find_person_match(session, isim, allow_llm_suggestion=False)
        if person is not None:
            secili_id, secili_ad = person.id, person.full_name

    if person is not None:
        await _komut_mal_sor(message.reply_text, context, kind, secili_id, secili_ad)
        return

    pending = _komut_pending(kind, isim, raw_id, message.text or isim, komut_kind=kind)

    if candidates:
        context.chat_data["komut_secim"] = {"kind": kind, "pending": pending}
        await message.reply_text(
            "Hangisini demek istedin?",
            reply_markup=_candidates_keyboard(candidates, prefix="komut"),
        )
        return

    # "Ekleyeyim mi? -> Evet" mevcut akışa girer (_begin_new_person_flow):
    # ad soyad onayı, telefon/il/ilçe, her adımda "Geç".
    context.chat_data["pending"] = pending
    await message.reply_text(
        f"{_title_tr(isim)} defterde yok. Ekleyeyim mi?", reply_markup=_yes_no_keyboard()
    )


async def _saman_ipucu() -> str:
    """Mal sorusunun altına eklenen kısa hatırlatma: saman için tutar
    yazılmazsa varsayılan fiyattan hesaplanacağı önceden söylenir (CLAUDE.md >
    "Varsayılan saman fiyatı"). Fiyat okunamıyorsa satır hiç yazılmaz —
    olmayan bir kolaylık vaat edilmez."""
    async with SessionLocal() as session:
        fiyat = await saman_fiyat.get_saman_price(session)
    if fiyat is None:
        return ""
    return f"\n(Saman için tutar yazmazsan {_fmt_try(fiyat)} TL/balya kullanılır.)"


async def _komut_mal_sor(
    reply, context: ContextTypes.DEFAULT_TYPE, kind: str, person_id: int, person_name: str
) -> None:
    """Kişi netleşti, sıra mal/tutarda. `reply` hem Message.reply_text hem
    CallbackQuery.edit_message_text olabilir (aday seçiminden de, yeni kişi
    akışının sonundan da buraya gelinir — bkz. _complete_new_person)."""
    context.chat_data["komut_akisi"] = {
        "tip": "kayit",
        "kind": kind,
        "person_id": person_id,
        "person_name": person_name,
    }
    await reply(f"{person_name} — {_KOMUT_KAYIT_SORUSU[kind]}{await _saman_ipucu()}")


async def _komut_kayit_isle(
    update: Update, context: ContextTypes.DEFAULT_TYPE, akis: dict, cevap: str
) -> None:
    """Mal/tutar cevabı NORMAL ayrıştırmadan geçer — "20 balya saman 5000 tl",
    "70-20-50", "5000 tl" hepsi çalışır. Cevap kişinin adıyla birleştirilip
    parse edilir (tek başına "20 balya saman 5000 tl" bir kayıt cümlesi
    değildir); kişi ZATEN seçili olduğu için isim yeniden EŞLEŞTİRİLMEZ
    (process_intent'e person= verilir).

    Anlaşılmazsa hiçbir şey kaydedilmez ve akış AÇIK kalır: soru tekrarlanır,
    kullanıcı yeniden yazar."""
    tam = f"{akis['person_name']} {cevap}"

    # SIRA ÖNEMLİ: önce "cevabın tamamı bir tutar mı?" diye bakılır. Komut
    # zaten "ne kadar?" diye sorduğu için tek başına bir sayı ("5000",
    # "5 bin", "5000 tl") PARADIR — CLAUDE.md > "Para vs adet": fiil
    # bağlamındaki çıplak sayı TL'dir ve buradaki fiili komutun kendisi
    # veriyor. Parser'a bırakılsaydı aynı sayı fiilsiz saman kalıbına
    # düşerdi ("furkan 20" = 20 balya saman) ve /tahsilat'ta "5000 balya
    # saman tahsilat mı?" gibi saçma bir soru çıkardı.
    # Çözücü koşan formatın tutar sorusuyla AYNI (parse_amount_reply):
    # ikinci bir sayı mantığı yazılmaz. Adet kastedilen cevapta birim ya da
    # ürün zaten yazılır ("20 balya", "20 saman") — o cevap buradan geçmez,
    # aşağıdaki normal ayrıştırmaya düşer.
    tutar = parse_amount_reply(cevap)
    if tutar is not None:
        intent = ParsedIntent(kind=akis["kind"], person_name=akis["person_name"], amount=tutar)
    else:
        intent = parser.parse(tam)
        if intent is not None and intent.kind not in ("debt", "payment"):
            intent = None

    if intent is None:
        await update.message.reply_text(
            "Anlayamadım. Ne alındı ve kaç TL? Örnek: 20 balya saman 5000 tl"
        )
        return

    # Yönü KOMUT söyler: /borc borç, /tahsilat tahsilat. Cevaptaki fiil bunu
    # değiştirmez (kullanıcı komutu bilerek seçti) — ve yön artık VARSAYIM
    # olmadığı için "borç mu demek istediniz?" diye ayrıca sorulmaz.
    intent.kind = akis["kind"]
    intent.assumed_kind = False

    context.chat_data.pop("komut_akisi", None)

    async with SessionLocal() as session:
        person = await session.get(Person, akis["person_id"])
        if person is None:
            await update.message.reply_text("Kişi bulunamadı.")
            return
        raw = await save_raw_message(session, update.to_dict())
        await session.commit()
        async with typing_action(context.bot, update.message.chat_id):
            result = await message_processor.process_intent(
                session, raw, intent, tam, person=person
            )
            await session.commit()
        await _reply_outcome(session, context, result, raw, tam, update.message)


# ---- /kisiekle (ADIM ADIM — mevcut akışı tetikler)

async def cmd_kisiekle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _komut_temizle(context)
    isim = _komut_argumani(context)
    if not isim:
        context.chat_data["komut_akisi"] = {"tip": "kisiekle"}
        await update.message.reply_text("Ad soyad?")
        return
    await _komut_kisiekle_baslat(update, context, isim)


async def _komut_kisiekle_baslat(
    update: Update, context: ContextTypes.DEFAULT_TYPE, isim: str
) -> None:
    """Telegram'da zaten var olan adım adım kişi ekleme akışını TETİKLER
    (ad soyad onayı → telefon → il → ilçe, her adımda "Geç", ilkinde "Hepsini
    geç") — ikinci bir akış yazılmaz (CLAUDE.md > "Telegram'dan kişi eklerken
    detay sorma")."""
    raw_id = await _komut_raw_id(update)
    pending = _komut_pending(
        "create_person", isim, raw_id, update.message.text or isim
    )
    prompt, keyboard = _new_person_flow_baslat(context, pending)
    await update.message.reply_text(prompt, reply_markup=keyboard)


# ---- komut sorularının metin cevapları

async def _handle_komut_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, akis: dict, text: str
) -> None:
    """Bir komut soru sormuşken gelen düz metin buraya gelir (bkz. on_text).
    Cevap işlenmeden önce akış chat_data'dan DÜŞÜRÜLÜR — tek istisna "kayit":
    cevabı anlaşılmazsa soru açık kalmalı ki kullanıcı yeniden yazabilsin."""
    cevap = (text or "").strip()
    if not cevap:
        await update.message.reply_text("Boş geçemem, yazar mısın?")
        return

    tip = akis["tip"]
    if tip == "kayit":
        await _komut_kayit_isle(update, context, akis, cevap)
        return

    context.chat_data.pop("komut_akisi", None)
    if tip == "kisi":
        await _komut_ara(update, context, cevap)
    elif tip == "koy":
        await _komut_ilce(update, context, cevap)
    elif tip == "kisi_sec":
        await _komut_kisi_coz(update, context, akis["kind"], cevap)
    elif tip == "kisiekle":
        await _komut_kisiekle_baslat(update, context, cevap)


async def _handle_komut_pick(query, context: ContextTypes.DEFAULT_TYPE, person_id: int) -> None:
    """/borc-/tahsilat'ta "hangisi?" cevaplandı: kişi seçildi, mal sorusuna
    geçilir (kayıt hâlâ YAPILMADI)."""
    secim = context.chat_data.pop("komut_secim", None)
    if not secim:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, person_id)
        if person is None:
            await query.edit_message_text("Kişi bulunamadı.")
            return
        secili_id, secili_ad = person.id, person.full_name

    await query.edit_message_reply_markup(reply_markup=None)
    await _komut_mal_sor(query.message.reply_text, context, secim["kind"], secili_id, secili_ad)


async def _handle_komut_new(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adaylardan "+ Yeni kişi ekle": adım adım kişi ekleme akışı başlar,
    kişi açılınca mal sorusuna dönülür (bkz. _complete_new_person)."""
    secim = context.chat_data.pop("komut_secim", None)
    if not secim:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    prompt, keyboard = _new_person_flow_baslat(context, secim["pending"])
    await query.edit_message_text(prompt, reply_markup=keyboard)


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

    # Koşan formatta tutar sorulmuşsa ("70-20-50" yalnızca adedi söyler),
    # gelen metin o tutarın cevabıdır (CLAUDE.md > "Koşan format").
    running_amount = context.chat_data.get("running_amount")
    if running_amount is not None:
        await _handle_running_amount_text(update, context, running_amount, text)
        return

    # Bir "/" komutu soru sormuşsa ("Kimin borcu?", "Ne aldı, ne kadar?")
    # gelen metin o sorunun cevabıdır (bkz. "/" komut menüsü bölümü).
    komut_akisi = context.chat_data.get("komut_akisi")
    if komut_akisi is not None:
        await _handle_komut_text(update, context, komut_akisi, text)
        return

    # Mesaj gelir gelmez typing başlar: regex mi LLM'e mi düşeceği henüz
    # belli olmadığından baştan gösterilir. Regex anında çözerse tek bir
    # typing zararsız; LLM'e düşerse (~13 sn) periyodik yenilenir.
    #
    # Aynı session boyunca: save_raw_message hemen commit edilir (mesaj
    # asla kaybolmaz), sonra aynı session'da işlenir — raw nesnesi başka
    # bir session'a taşınırsa flush() processed_at/transaction_id
    # güncellemesini göremez.
    async with SessionLocal() as session:
        raw = await save_raw_message(session, update.to_dict())
        await session.commit()
        await _process_text(update, context, session, raw, text, chat_id)


async def _process_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session,
    raw: RawMessage,
    text: str,
    chat_id: int,
) -> None:
    """Düz metni (kullanıcının yazdığı ya da sesten çevrilmiş) işler —
    on_text VE on_voice tarafından paylaşılır: ses, metne dönüşünce AYNI
    akışa (parser -> LLM fallback -> intent_resolver) girer (CLAUDE.md >
    "Telegram sesli mesajları için speech-to-text ekle"). `raw` zaten
    save_raw_message ile yazılmış ve commit edilmiş olmalı; sesli mesajda
    ayrıca voice_transcript de dolu olur (bkz. on_voice) — record_resolved
    bunu görüp Transaction.source'u TELEGRAM_VOICE yazar.

    Tek mesajda birden çok işlem olabilir (CLAUDE.md > "Tek mesajda birden
    çok istek", Grup 5): bölme SADECE her parça bağımsız olarak geçerli bir
    işleme parse edilebiliyorsa yapılır, şüphede tek bırakılır (bkz.
    app/services/message_splitter.py). Tek parça varsa (çoğunlukla) aşağıdaki
    akış TEK bir mesaj gibi davranır — davranış değişmez. Çoklu işlemde TÜM
    parçalar için AYNI raw kullanılır (gelen tek bir Telegram güncellemesi/ses
    kaydı) — her parça ayrı process_raw_message çağrısıyla kendi
    Transaction'ını oluşturur."""
    pieces = message_splitter.split_into_requests(text)
    is_multi = len(pieces) > 1

    if is_multi:
        await update.message.reply_text(f"{len(pieces)} işlem algılandı, sırayla işliyorum:")

    for idx, piece in enumerate(pieces):
        async with typing_action(context.bot, chat_id):
            result = await message_processor.process_raw_message(session, raw, piece)
            await session.commit()

        await _reply_outcome(session, context, result, raw, piece, update.message, is_multi)

        # Bu işlem kullanıcıdan onay/seçim bekliyorsa (ör. "hangisi?", ürün
        # önerisi, silme onayı) sıradaki parça OTOMATİK işlenmez — kalan
        # parçalar bir KUYRUĞA (chat_data["pending_queue"]) konur. Kullanıcı
        # bu onayı cevaplayınca (bkz. _advance_queue, ilgili onay/seçim
        # callback'lerinin/işleyicilerinin sonunda çağrılır) kuyruktaki bir
        # sonraki parça OTOMATİK işlenir — art arda birden çok onay gerekse
        # bile hiçbiri atlanmaz (CLAUDE.md > "Tek mesajda birden çok istek").
        if result.outcome not in _COMPLETES_WITHOUT_INPUT:
            remaining = pieces[idx + 1 :]
            if remaining:
                context.chat_data["pending_queue"] = {
                    "raw_message_id": raw.id,
                    "remaining": remaining,
                }
            break


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sesli mesaj: Telegram'dan .ogg indirilir, Groq whisper-large-v3 ile
    Türkçe metne çevrilir, sonra sanki kullanıcı yazmış gibi AYNI akışa
    (_process_text) verilir (CLAUDE.md > "Telegram sesli mesajları için
    speech-to-text ekle"). Mesaj asla kaybolmaz: raw_messages'a Groq'a hiç
    gitmeden ÖNCE yazılır — GROQ_API_KEY yoksa ya da çeviri başarısızsa bile
    ham ses kaydının izi durur, kullanıcıdan yazması istenir."""
    chat_id = update.effective_chat.id

    async with SessionLocal() as session:
        raw = await save_raw_message(session, update.to_dict())
        raw_id = raw.id
        await session.commit()

    provider = stt.get_stt_provider()
    if provider is None:
        await update.message.reply_text(VOICE_KAPALI_METNI)
        return

    tg_file = await context.bot.get_file(update.message.voice.file_id)
    audio = bytes(await tg_file.download_as_bytearray())

    async with typing_action(context.bot, chat_id):
        text = await provider.transcribe(audio)

    if not text:
        await update.message.reply_text(VOICE_ANLASILAMADI_METNI)
        return

    # Kullanıcı ne anladığımızı görsün — yanlışsa yazarak düzeltebilir
    # (CLAUDE.md: "Kullanıcıya önce '🎤 anladım: <metin>' gibi geri bildirim").
    await update.message.reply_text(f"🎤 Anladım: {text}")

    async with SessionLocal() as session:
        raw = await session.get(RawMessage, raw_id)
        raw.voice_transcript = text
        await session.commit()
        await _process_text(update, context, session, raw, text, chat_id)


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
        await _advance_queue(context, update.message)
        return

    async with SessionLocal() as session:
        person = await session.get(Person, pending["person_id"])
        if person is None or not person.is_active:
            await update.message.reply_text("Kişi bulunamadı.")
            await _advance_queue(context, update.message)
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
    await _advance_queue(context, update.message)


async def _reply_outcome(
    session: AsyncSession,
    context: ContextTypes.DEFAULT_TYPE,
    result: ProcessResult,
    raw: RawMessage,
    text: str,
    message,
    is_multi: bool = False,
) -> None:
    """`_reply_result`in gövdesi — `update` yerine genel bir `message`
    nesnesi alır (`update.message` ya da `query.message`, ikisi de
    reply_text/reply_document/chat_id sağlar). Bu sayede hem düz metinden
    (on_text) hem onay sonrası kuyruk devamından (_advance_queue) AYNI
    fonksiyon kullanılabilir (CLAUDE.md > "Tek mesajda birden çok istek").
    `is_multi`: bu parça çoklu-işlem bölmesinin bir parçasıysa True — yalnızca
    UNRECOGNIZED mesajının hangi metni işaret ettiği belirtilsin diye
    kullanılır, tek parçalı akışta (is_multi=False) mesaj DEĞİŞMEZ."""
    resolved = result.resolved

    if result.outcome == ProcessOutcome.RECORDED:
        msg = _format_record_confirmation(resolved, result.balance_before, result.balance)
        context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
        await message.reply_text(msg, reply_markup=_undo_keyboard(result.transaction_id))
        return

    if result.outcome == ProcessOutcome.BALANCE:
        await message.reply_text(
            _format_balance(resolved.person, result.balance, result.transactions or [], result.transactions_total or 0),
            parse_mode=ParseMode.HTML,
        )
        return

    if result.outcome == ProcessOutcome.PERSON_CONTACT:
        await message.reply_text(_format_person_card(resolved.person))
        return

    if result.outcome == ProcessOutcome.CREATE_PERSON:
        # _reply_outcome yalnızca DÜZ METİNDEN gelen sonuçlar için çağrılır;
        # yeni kişi oluşturma her zaman PERSON_NOT_FOUND -> Evet/Hayır ->
        # adım adım akıştan (_complete_new_person) geçer. Bu yüzden bu
        # outcome'a buradan gelinmesi, kişinin zaten mevcut olduğu
        # (find_person_match birebir eşleşme bulduğu) anlamına gelir.
        await message.reply_text(f"ℹ️ {resolved.person.full_name} zaten kayıtlı.")
        return

    if result.outcome == ProcessOutcome.INFO_MENU:
        context.chat_data["info_menu"] = {"person_id": resolved.person.id}
        await message.reply_text("Ne bilgisi?", reply_markup=_info_menu_keyboard())
        return

    if result.outcome == ProcessOutcome.ARCHIVE_CONFIRM:
        assert result.balance is not None
        await _prompt_archive_confirm(session, message.reply_text, context, resolved, result.balance)
        return

    if result.outcome == ProcessOutcome.EDIT_PERSON_CONFIRM:
        await _prompt_edit_confirm(message.reply_text, context, resolved)
        return

    if result.outcome == ProcessOutcome.EDIT_PERSON_MENU:
        await _prompt_edit_field_menu(message.reply_text, context, resolved)
        return

    if result.outcome == ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION:
        await _prompt_product_confirm(message.reply_text, context, resolved, raw.id, text)
        return

    if result.outcome == ProcessOutcome.LIST:
        for msg in _format_list_messages(resolved.kind, resolved.district, result.persons or []):
            await message.reply_text(msg)
        return

    if result.outcome == ProcessOutcome.SEARCH:
        for msg in _format_search_messages(resolved.query, result.persons or []):
            await message.reply_text(msg)
        return

    if result.outcome == ProcessOutcome.TOTAL_BALANCE:
        assert result.total is not None
        await message.reply_text(_format_total_balance(result.total))
        return

    if result.outcome == ProcessOutcome.DELETE_AMBIGUOUS:
        # Kişi netleşti ama HİÇBİR ŞEY yapılmadı: kullanıcı seçecek.
        await _prompt_delete_ambiguous(message.reply_text, context, resolved, raw.id, text)
        return

    if result.outcome == ProcessOutcome.REPORT_MENU:
        await message.reply_text(
            "Hangi raporu istersin?", reply_markup=_report_menu_keyboard()
        )
        return

    if result.outcome == ProcessOutcome.REPORT_DAILY:
        assert result.report_pdf is not None and result.report_stats is not None
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await message.reply_document(
            document=result.report_pdf,
            filename=f"rapor_gunluk_{report.today_tr().isoformat()}.pdf",
            caption=_format_daily_report_caption(result.report_stats),
        )
        return

    if result.outcome == ProcessOutcome.REPORT_GENERAL:
        assert result.report_pdf is not None and result.report_stats is not None
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await message.reply_document(
            document=result.report_pdf,
            filename=f"rapor_genel_{report.today_tr().isoformat()}.pdf",
            caption=_format_general_report_caption(result.report_stats),
        )
        return

    if result.outcome == ProcessOutcome.REPORT_PERSON:
        assert result.report_pdf is not None and result.balance is not None
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await message.reply_document(
            document=result.report_pdf,
            filename=f"rapor_ekstre_{report.slugify(resolved.person.full_name)}.pdf",
            caption=_format_person_report_caption(resolved.person, result.balance),
        )
        return

    if result.outcome == ProcessOutcome.LLM_CONFIRMATION:
        context.chat_data["llm_confirm"] = _llm_pending_from_resolved(resolved, raw.id, text)
        await message.reply_text(
            _format_llm_preview(resolved), reply_markup=_llm_confirm_keyboard()
        )
        return

    if result.outcome == ProcessOutcome.CLOSE_DEBT_CONFIRM:
        # "ali borcunu ödedi": tutar söylenmemiş, güncel bakiye teklif
        # ediliyor. Onay makinesi LLM önizlemesiyle AYNI (aynı chat_data
        # anahtarı, aynı Evet/Düzelt/İptal callback'leri) — yalnızca sorulan
        # cümle farklı.
        assert result.balance is not None
        context.chat_data["llm_confirm"] = _llm_pending_from_resolved(resolved, raw.id, text)
        await message.reply_text(
            _format_close_debt_preview(resolved, result.balance),
            reply_markup=_llm_confirm_keyboard(),
        )
        return

    if result.outcome == ProcessOutcome.SAMAN_PRICE_CONFIRM:
        # Tutar varsayılan saman fiyatından geldi, ürün/yön varsayıldı:
        # kayıttan önce sorulur.
        await _prompt_saman_price_confirm(message.reply_text, context, resolved, raw.id, text)
        return

    if result.outcome == ProcessOutcome.PRODUCT_QUERY_UNSUPPORTED:
        await message.reply_text(_format_product_query_unsupported(resolved))
        return

    if result.outcome == ProcessOutcome.RUNNING_MISMATCH:
        # Koşan üçlünün matematiği tutmuyor: hiçbir şey kaydedilmedi.
        await _prompt_running_fix(message.reply_text, context, resolved, raw.id, text)
        return

    if result.outcome == ProcessOutcome.RUNNING_AMOUNT_NEEDED:
        # Adet net, TL söylenmemiş: tutar uydurulmaz, yazarak sorulur.
        await _prompt_running_amount(message.reply_text, context, resolved, raw.id, text)
        return

    if result.outcome == ProcessOutcome.PERSON_NOT_FOUND:
        isim = _title_tr(resolved.person_name_raw or "")
        if resolved.kind in _QUERY_ONLY_KINDS:
            await message.reply_text(f"{isim} defterde yok.")
            return
        context.chat_data["pending"] = _pending_from_resolved(resolved, raw.id, text)
        await message.reply_text(
            f"{isim} defterde yok. Ekleyeyim mi?", reply_markup=_yes_no_keyboard()
        )
        return

    if result.outcome == ProcessOutcome.NEEDS_CONFIRMATION:
        context.chat_data["pending"] = _pending_from_resolved(resolved, raw.id, text)
        await message.reply_text(
            "Hangisini demek istedin?", reply_markup=_candidates_keyboard(resolved.person_candidates)
        )
        return

    # UNRECOGNIZED — asla ikinci soru sorma, örnekle yönlendir. Çoklu işlem
    # bölmesinin bir parçasıysa (is_multi) HANGİ parçanın anlaşılmadığı
    # belirtilir (CLAUDE.md > "Tek mesajda birden çok istek" — bir parça
    # anlaşılmazsa diğerleri yine de işlenir, bu yüzden kullanıcı hangisinin
    # atlandığını görmeli); tek parçalı akışta mesaj DEĞİŞMEZ.
    if is_multi:
        await message.reply_text(f"Şu kısmı anlayamadım: '{text}'")
    else:
        await message.reply_text(ANLASILAMADI_METNI)


async def _reply_result(
    session: AsyncSession,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: ProcessResult,
    raw: RawMessage,
    text: str,
    is_multi: bool = False,
) -> None:
    await _reply_outcome(session, context, result, raw, text, update.message, is_multi)


async def _advance_queue(context: ContextTypes.DEFAULT_TYPE, message) -> None:
    """Bir onay/seçim (Evet/Hayır/hangisi/İptal — hangisi fark etmez, kullanıcı
    KARARINI verdiği an) tamamlandıktan SONRA çağrılır (CLAUDE.md > "Tek
    mesajda birden çok istek"). chat_data["pending_queue"]'da kalan parça
    varsa bir sonrakini işler; o da onay isterse kuyruk yine orada durur
    (chat_data zaten güncellenmiş olur, bu fonksiyon bir dahaki onaydan
    sonra tekrar çağrılınca kaldığı yerden devam eder). Kuyruk hiç yoksa
    (tek parçalı bir mesajın onayıysa) hiçbir şey yapmaz. `message` hem
    update.message hem query.message olabilir."""
    queue = context.chat_data.get("pending_queue")
    if not queue:
        return

    remaining = queue["remaining"]
    if not remaining:
        context.chat_data.pop("pending_queue", None)
        await message.reply_text("Tüm işlemler tamamlandı.")
        return

    piece, *rest = remaining
    # Bu parçayı kuyruktan ÖNCE tüket: aşağıdaki işlem kendisi de onay
    # isterse (zincirleme), chat_data zaten doğru "rest" ile güncel olur —
    # bir sonraki onay bu fonksiyonu tekrar çağırdığında kaldığı yerden
    # devam eder.
    context.chat_data["pending_queue"] = {**queue, "remaining": rest}

    async with SessionLocal() as session:
        raw = await session.get(RawMessage, queue["raw_message_id"])
        async with typing_action(context.bot, message.chat_id):
            result = await message_processor.process_raw_message(session, raw, piece)
            await session.commit()

        await _reply_outcome(session, context, result, raw, piece, message, is_multi=True)

    if result.outcome in _COMPLETES_WITHOUT_INPUT:
        await _advance_queue(context, message)  # zincirleme devam


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
        await _advance_queue(context, query.message)
        return
    if data in ("person:yes", "person:new"):
        await _begin_new_person_flow(query, context)
        return
    if data.startswith("person:pick:"):
        person_id = int(data.rsplit(":", 1)[1])
        await _handle_person_pick(query, context, person_id)
        return
    if data == "komut:new":
        await _handle_komut_new(query, context)
        return
    if data.startswith("komut:pick:"):
        await _handle_komut_pick(query, context, int(data.rsplit(":", 1)[1]))
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
        await _advance_queue(context, query.message)
        return
    if data == "llm:cancel":
        context.chat_data.pop("llm_confirm", None)
        await query.edit_message_text("Tamam, iptal ettim.")
        await _advance_queue(context, query.message)
        return
    if data == "report:daily":
        await _handle_report_daily(query, context)
        await _advance_queue(context, query.message)
        return
    if data == "report:general":
        await _handle_report_general(query, context)
        await _advance_queue(context, query.message)
        return
    if data == "info:balance":
        await _handle_info_balance(query, context)
        await _advance_queue(context, query.message)
        return
    if data == "info:card":
        await _handle_info_card(query, context)
        await _advance_queue(context, query.message)
        return
    if data == "info:report":
        await _handle_info_report(query, context)
        await _advance_queue(context, query.message)
        return
    if data == "edit:yes":
        await _handle_edit_confirm_yes(query, context)
        return
    if data == "edit:no":
        context.chat_data.pop("edit_confirm", None)
        await query.edit_message_text("Tamam, değişiklik yapılmadı.")
        await _advance_queue(context, query.message)
        return
    if data.startswith("editfield:"):
        field = data.split(":", 1)[1]
        await _handle_edit_field_pick(query, context, field)
        return
    if data == "delete:person":
        await _handle_delete_ambiguous_person(query, context)
        return
    if data == "delete:payment":
        await _handle_delete_ambiguous_payment(query, context)
        return
    if data == "delete:cancel":
        context.chat_data.pop("delete_ambiguous", None)
        await query.edit_message_text("Tamam, iptal ettim.")
        await _advance_queue(context, query.message)
        return
    if data == "running:fix":
        await _handle_running_fix(query, context)
        return
    if data == "running:cancel":
        context.chat_data.pop("running_fix", None)
        await query.edit_message_text("Tamam, iptal ettim. Doğru sayılarla tekrar yazar mısın?")
        await _advance_queue(context, query.message)
        return
    if data == "product:yes":
        await _handle_product_confirm(query, context, use_suggestion=True)
        return
    if data == "product:new":
        await _handle_product_confirm(query, context, use_suggestion=False)
        return
    if data == "product:cancel":
        context.chat_data.pop("product_confirm", None)
        await query.edit_message_text("İşlem iptal edildi.")
        await _advance_queue(context, query.message)
        return


async def _handle_delete_ambiguous_person(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """"Kişiyi sil" seçildi: normal silme akışına (yazarak onay) girilir —
    kısayol YOK, onay kelimesi yine istenir (CLAUDE.md > "Yazarak onay her
    silmede")."""
    pending = context.chat_data.pop("delete_ambiguous", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    async with SessionLocal() as session:
        person = await session.get(Person, pending["person_id"])
        if person is None or not person.is_active:
            await query.edit_message_text("Kişi bulunamadı.")
            await _advance_queue(context, query.message)
            return
        bal = await balance_of(session, person.id)
        resolved = ResolvedIntent(
            status=ResolutionStatus.READY, kind="archive_person", person=person
        )
        await _prompt_archive_confirm(session, query.edit_message_text, context, resolved, bal)


async def _handle_delete_ambiguous_payment(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """"Tahsilat gir" seçildi: silme fiili ayıklanmış metin ("...borcunu
    ödedi") NORMAL akıştan (kural parser -> gerekirse LLM) yeniden geçirilir.
    Böylece ikinci bir mantık yazılmaz; tutar eksikse akış her zamanki
    "anlamadım" yönlendirmesiyle biter."""
    pending = context.chat_data.pop("delete_ambiguous", None)
    if not pending:
        await query.edit_message_text("Bu istek artık geçerli değil.")
        return

    await query.edit_message_reply_markup(reply_markup=None)
    text = pending["text"]
    async with SessionLocal() as session:
        raw = await session.get(RawMessage, pending["raw_message_id"])
        async with typing_action(context.bot, query.message.chat_id):
            result = await message_processor.process_raw_message(session, raw, text)
            await session.commit()
        await _reply_outcome(session, context, result, raw, text, query.message)

    if result.outcome in _COMPLETES_WITHOUT_INPUT:
        await _advance_queue(context, query.message)


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

    prompt, keyboard = _new_person_flow_baslat(context, pending)
    await query.edit_message_text(prompt, reply_markup=keyboard)


def _new_person_flow_baslat(
    context: ContextTypes.DEFAULT_TYPE, pending: dict
) -> tuple[str, InlineKeyboardMarkup]:
    """Adım adım yeni kişi akışını başlatır, ilk sorunun metnini döner.
    "Ekleyeyim mi? → Evet", "+ Yeni kişi ekle", /kisiekle ve /borc-/tahsilat
    içindeki "defterde yok" hepsi bu tek girişten geçer."""
    name = _title_tr(pending.get("person_name_raw") or "")
    context.chat_data["new_person_flow"] = {
        "step": "name",
        "name": name,
        "phone": None,
        "city": None,
        "district": None,
        "pending": pending,
    }
    return _new_person_prompt("name", name)


def _person_from_flow(flow: dict) -> Person:
    return Person(
        full_name=flow["name"],
        phone=flow.get("phone"),
        city=flow.get("city"),
        district=flow.get("district"),
    )


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
    await _advance_queue(context, query.message)


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
        completed = await _finish_pending(session, query, context, person, pending)
        await session.commit()

    # Kuyruk İLERLETME oturum KAPANDIKTAN sonra yapılır: _advance_queue kendi
    # SessionLocal()'ını açıyor ve aynı raw_messages satırını güncelliyor —
    # yukarıdaki oturum hâlâ açıkken çağrılırsa iki oturum aynı satır kilidini
    # bekleyip sonsuza kadar asılı kalır (testler bu yüzden bitmiyordu).
    if completed:
        await _advance_queue(context, query.message)


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
        running=pending.get("running", False),
        assumed_product=pending.get("assumed_product", False),
        assumed_kind=pending.get("assumed_kind", False),
    )
    if pending["product_name"] and pending["kind"] != "balance_query":
        product, suggestion = await catalog.resolve_product_or_suggest(
            session, pending["product_name"], pending["unit"]
        )
        if suggestion is not None:
            # Ürün adı bulanık (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)") —
            # burada kişi az önce netleşti (aday seçimi/yeni kişi akışı) ama
            # ürün otomatik bağlanmaz, aynı PRODUCT_NEEDS_CONFIRMATION
            # akışına (message_processor.handle_resolved ile aynı sonuç
            # şeklinde) düşer.
            resolved.status = ResolutionStatus.PRODUCT_NEEDS_CONFIRMATION
            resolved.product_suggestion = suggestion
            return resolved, ProcessResult(
                outcome=ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION, resolved=resolved
            )
        resolved.product = product

    raw = await session.get(RawMessage, pending["raw_message_id"])
    async with typing_action(context.bot, chat_id):
        result = await message_processor.handle_resolved(session, raw, resolved, pending["raw_text"])
    return resolved, result


async def _finish_pending(session, query, context, person: Person, pending: dict) -> bool:
    """Aday seçildikten sonra bekleyen niyeti tamamlar. Bu parçanın TAM
    bitip bitmediğini (yeni bir onay/seçim beklemediğini) döner; kuyruğu
    ilerletme kararını çağıran verir — `session` burada hâlâ AÇIK olduğu
    için kuyruk bu fonksiyonun içinden ilerletilemez (bkz.
    _handle_person_pick'teki kilit notu)."""
    resolved, result = await _resolve_and_process(session, context, query.message.chat_id, person, pending)

    if result.outcome == ProcessOutcome.BALANCE:
        await query.edit_message_text(
            _format_balance(person, result.balance, result.transactions or [], result.transactions_total or 0),
            parse_mode=ParseMode.HTML,
        )

    elif result.outcome == ProcessOutcome.REPORT_PERSON:
        assert result.report_pdf is not None and result.balance is not None
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await query.message.reply_document(
            document=result.report_pdf,
            filename=f"rapor_ekstre_{report.slugify(person.full_name)}.pdf",
            caption=_format_person_report_caption(person, result.balance),
        )

    elif result.outcome == ProcessOutcome.PERSON_CONTACT:
        await query.edit_message_text(_format_person_card(person))

    elif result.outcome == ProcessOutcome.INFO_MENU:
        context.chat_data["info_menu"] = {"person_id": person.id}
        await query.edit_message_text("Ne bilgisi?", reply_markup=_info_menu_keyboard())

    elif result.outcome == ProcessOutcome.ARCHIVE_CONFIRM:
        assert result.balance is not None
        await _prompt_archive_confirm(session, query.edit_message_text, context, resolved, result.balance)

    elif result.outcome == ProcessOutcome.DELETE_AMBIGUOUS:
        await _prompt_delete_ambiguous(
            query.edit_message_text, context, resolved,
            pending["raw_message_id"], pending["raw_text"],
        )

    elif result.outcome == ProcessOutcome.EDIT_PERSON_CONFIRM:
        await _prompt_edit_confirm(query.edit_message_text, context, resolved)

    elif result.outcome == ProcessOutcome.EDIT_PERSON_MENU:
        await _prompt_edit_field_menu(query.edit_message_text, context, resolved)

    elif result.outcome == ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION:
        await _prompt_product_confirm(
            query.edit_message_text, context, resolved, pending["raw_message_id"], pending["raw_text"]
        )

    elif result.outcome == ProcessOutcome.RUNNING_AMOUNT_NEEDED:
        await _prompt_running_amount(
            query.edit_message_text, context, resolved,
            pending["raw_message_id"], pending["raw_text"],
        )

    elif result.outcome == ProcessOutcome.SAMAN_PRICE_CONFIRM:
        await _prompt_saman_price_confirm(
            query.edit_message_text, context, resolved,
            pending["raw_message_id"], pending["raw_text"],
        )

    elif result.outcome == ProcessOutcome.CREATE_PERSON:
        # Adaylardan biri seçildi (mevcut kişi) — "hangisi?" sorusuna cevap
        # verildi, yeni bir kişi oluşturulmadı (bkz. _reply_result'taki aynı
        # outcome yorumu).
        await query.edit_message_text(f"ℹ️ {person.full_name} zaten kayıtlı.")

    else:
        msg = _format_record_confirmation(resolved, result.balance_before, result.balance)
        context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
        await query.edit_message_text(msg, reply_markup=_undo_keyboard(result.transaction_id))

    # Bu parça TAM olarak bittiyse (yeni bir onay/seçim beklemiyorsa) çağıran
    # kuyrukta bekleyen bir sonraki parçaya geçer (CLAUDE.md > "Tek mesajda
    # birden çok istek") — ARCHIVE_CONFIRM/EDIT_PERSON_*/
    # PRODUCT_NEEDS_CONFIRMATION/INFO_MENU gibi ZİNCİRLEME bir onay daha
    # başlattıysa (aday seçildi ama şimdi ürün de belirsiz çıktı gibi) kuyruk
    # İLERLETİLMEZ, bu parça hâlâ açık sayılır.
    return result.outcome in _COMPLETES_WITHOUT_INPUT


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
    if "komut_kind" in pending:
        # /borc ya da /tahsilat içinde "defterde yok → Evet" ile açılan kişi:
        # henüz kaydedilecek bir şey yok, sıradaki adım "ne aldı, ne kadar?".
        async with SessionLocal() as session:
            person = _person_from_flow(flow)
            session.add(person)
            await session.flush()
            person_id, full_name = person.id, person.full_name
            await session.commit()
        await msg.reply_text(f"✅ {full_name} eklendi.")
        await _komut_mal_sor(msg.reply_text, context, pending["komut_kind"], person_id, full_name)
        return

    async with SessionLocal() as session:
        person = _person_from_flow(flow)
        session.add(person)
        await session.flush()
        resolved, result = await _resolve_and_process(session, context, msg.chat_id, person, pending)
        await session.commit()

    if result.outcome == ProcessOutcome.BALANCE:
        await msg.reply_text(
            _format_balance(person, result.balance, result.transactions or [], result.transactions_total or 0),
            parse_mode=ParseMode.HTML,
        )

    elif result.outcome == ProcessOutcome.REPORT_PERSON:
        assert result.report_pdf is not None and result.balance is not None
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await msg.reply_document(
            document=result.report_pdf,
            filename=f"rapor_ekstre_{report.slugify(person.full_name)}.pdf",
            caption=_format_person_report_caption(person, result.balance),
        )

    elif result.outcome == ProcessOutcome.PERSON_CONTACT:
        await msg.reply_text(_format_person_card(person))

    elif result.outcome == ProcessOutcome.INFO_MENU:
        context.chat_data["info_menu"] = {"person_id": person.id}
        await msg.reply_text("Ne bilgisi?", reply_markup=_info_menu_keyboard())

    elif result.outcome == ProcessOutcome.ARCHIVE_CONFIRM:
        # Nadir yol: "+ Yeni kişi ekle" ile arşivleme niyeti için az önce
        # oluşturulmuş bir kişi üzerinde çalışılıyor demektir. Yukarıdaki
        # session bu noktada KAPANMIŞ olduğundan (kişi oluşturma commit
        # edildi) onay kelimesini okumak için kısa ömürlü bir session açılır.
        assert result.balance is not None
        async with SessionLocal() as onay_session:
            await _prompt_archive_confirm(onay_session, msg.reply_text, context, resolved, result.balance)

    elif result.outcome == ProcessOutcome.EDIT_PERSON_CONFIRM:
        # Nadir yol: "+ Yeni kişi ekle" ile düzenleme niyeti için az önce
        # oluşturulmuş bir kişi üzerinde çalışılıyor demektir.
        await _prompt_edit_confirm(msg.reply_text, context, resolved)

    elif result.outcome == ProcessOutcome.EDIT_PERSON_MENU:
        await _prompt_edit_field_menu(msg.reply_text, context, resolved)

    elif result.outcome == ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION:
        await _prompt_product_confirm(msg.reply_text, context, resolved, pending["raw_message_id"], pending["raw_text"])

    elif result.outcome == ProcessOutcome.RUNNING_AMOUNT_NEEDED:
        await _prompt_running_amount(msg.reply_text, context, resolved, pending["raw_message_id"], pending["raw_text"])

    elif result.outcome == ProcessOutcome.SAMAN_PRICE_CONFIRM:
        await _prompt_saman_price_confirm(
            msg.reply_text, context, resolved, pending["raw_message_id"], pending["raw_text"]
        )

    elif result.outcome == ProcessOutcome.CREATE_PERSON:
        # Bu akışta `person` az önce oluşturuldu (yukarıda) — burada her
        # zaman "yeni eklendi" anlamına gelir (bkz. _reply_result'taki
        # "zaten kayıtlı" yorumuyla karşılaştır: orası asla yeni oluşturmaz).
        await msg.reply_text(f"✅ {person.full_name} eklendi.")

    else:
        text = _format_record_confirmation(resolved, result.balance_before, result.balance)
        context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
        await msg.reply_text(text, reply_markup=_undo_keyboard(result.transaction_id))

    # bkz. _finish_pending'in sonundaki aynı not: yalnızca bu parça TAM
    # bitmişse (zincirleme yeni bir onay başlatmadıysa) kuyruktaki bir
    # sonraki parçaya geçilir.
    if result.outcome in _COMPLETES_WITHOUT_INPUT:
        await _advance_queue(context, msg)


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
            await _advance_queue(context, query.message)
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
            default_unit_price=pending.get("default_unit_price"),
        )

        raw = await session.get(RawMessage, pending["raw_message_id"])
        result = await message_processor.handle_resolved(
            session, raw, resolved, pending["raw_text"], source="rule"
        )
        await session.commit()

    msg = _format_record_confirmation(resolved, result.balance_before, result.balance)
    context.chat_data["undo"] = {"tx_id": result.transaction_id, "at": time.monotonic()}
    await query.edit_message_text(msg, reply_markup=_undo_keyboard(result.transaction_id))
    await _advance_queue(context, query.message)


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
        await _advance_queue(context, query.message)
        return
    await query.edit_message_text(_format_edit_result(name, pending["field"], pending["value"]))
    await _advance_queue(context, query.message)


# --------------------------------------------------------------- kurulum

# "/" yazınca çıkan menü. Açıklamalar kısa ve teknik terimsiz: müşteri
# "intent", "parser", "LLM" gibi kelimeleri görmez.
_MUSTERI_KOMUTLARI = [
    BotCommand("borc", "Borç kaydı ekle"),
    BotCommand("tahsilat", "Tahsilat kaydı ekle"),
    BotCommand("bakiye", "Defter toplamı (ya da: /bakiye ahmet)"),
    BotCommand("listele", "Tüm kişileri ve bakiyelerini listele"),
    BotCommand("kisi", "Kişi ara"),
    BotCommand("koy", "İlçedeki kişiler"),
    BotCommand("kisiekle", "Yeni kişi ekle"),
    BotCommand("yardim", "Yardım"),
]

# Yalnızca yöneticinin menüsünde görünür (BotCommandScopeChat): müşteriye
# komutun VARLIĞI bile sızmaz (CLAUDE.md > "Admin/müşteri ayrımı").
_ADMIN_KOMUTLARI = [
    BotCommand("durum", "Sistem durumu"),
    BotCommand("yedek", "Veritabanı yedeğini gönder"),
]


def komut_listesi(admin: bool = False) -> list[BotCommand]:
    komutlar = [BotCommand("start", "Başla"), *_MUSTERI_KOMUTLARI]
    return [*komutlar, *_ADMIN_KOMUTLARI] if admin else komutlar


def _admin_chat_idleri() -> list[int]:
    """Yönetici menüsünün gösterileceği sohbetler: /durum'un yetkilendirdiği
    TELEGRAM_ADMIN_IDS ve /yedek'in yetkilendirdiği TELEGRAM_ADMIN_CHAT_ID —
    ikisi farklı ayar, biri diğerini kapsamayabilir."""
    idler = list(settings.telegram_admin_ids_list)
    yedek_chat = settings.telegram_admin_chat_id_int
    if yedek_chat is not None and yedek_chat not in idler:
        idler.append(yedek_chat)
    return idler


async def _set_commands(application: Application) -> None:
    await application.bot.set_my_commands(komut_listesi(), scope=BotCommandScopeDefault())
    for admin_id in _admin_chat_idleri():
        await application.bot.set_my_commands(
            komut_listesi(admin=True), scope=BotCommandScopeChat(chat_id=admin_id)
        )


async def _warmup_llm() -> None:
    """Polling başlamadan önce modeli belleğe alır: Ollama boştayken modeli
    düşürüyor (keep_alive), ilk gerçek mesaj bu yüzden soğuk (60sn+) yükleme
    süresine denk geliyordu (CLAUDE.md ölçümü). Bu çağrı sonucu kullanılmaz,
    yalnızca modeli ısıtır. Hata olursa yutulur — bot LLM'siz de başlar.
    Hangi sağlayıcının ısıtılacağı get_active_provider ile aynı seçim
    mantığından (settings.llm_primary) geçer — sabit Ollama değil."""
    async with SessionLocal() as session:
        provider = await llm_provider.get_active_provider(session)
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
    # "/" komut menüsü (bkz. o bölüm). Sıra önemsiz: her komut kendi adıyla
    # eşleşir, düz metin (on_text) zaten filters.COMMAND'ı dışlıyor.
    application.add_handler(CommandHandler("yedek", cmd_yedek))
    application.add_handler(CommandHandler("bakiye", cmd_bakiye))
    application.add_handler(CommandHandler("listele", cmd_listele))
    application.add_handler(CommandHandler("kisi", cmd_kisi))
    application.add_handler(CommandHandler("koy", cmd_koy))
    application.add_handler(CommandHandler("borc", cmd_borc))
    application.add_handler(CommandHandler("tahsilat", cmd_tahsilat))
    application.add_handler(CommandHandler("kisiekle", cmd_kisiekle))
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
