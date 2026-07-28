"""Kural tabanlı Türkçe komut çözümleyici (Faz 3, adım 2).

LLM yok: yalnızca regex + sözlük eşleştirme. Girdi ham metin, çıktı
ParsedIntent ya da (anlaşılamadıysa) None. Belirsiz kalan alan uydurulmaz,
boş bırakılır — kararı intent_resolver ve bot verir.

Desteklenen kalıplar (kelime sırası biraz oynayabilir):
  Borç:     "{isim} {sayı} {birim} {ürün} aldı {tutar} tl borç"
            varyantlar: "aldı", "verdim/verdik", "çekti", "sattım",
            "çıktı", "gitti", "borç yaz(dı)"
  Tahsilat: "{isim} {tutar} tl (ödedi|verdi|yatırdı|geldi)"
            varyantlar: "{isim}den/dan {tutar} aldım/aldık",
            "{isim}den tahsil ettim"
            ürünlü:    "{isim} {sayı} {birim} {ürün} parası ödedi {tutar} tl"
  Yön ayrımı fiil çekiminde: "aldı" (o aldı) = borç, "aldım/aldık" (ben
            aldım) = tahsilat — bkz. DEBT_WORDS/PAYMENT_WORDS.
  Tutar/adet: birim kelimesi (balya/kg/...) yoksa sayı ADET değil TUTAR
            sayılır ("mehmete 3000 verdim" -> amount=3000, qty=None).
  Türkçe sayı: "3bin"/"15bin"=3000/15000, "ikiyüz"=200, "3buçuk"=3.5,
            "birmilyon"=1000000 (bkz. parse_turkish_number, _consume_number).
  Bakiye:   "{isim} (borcu|borcunu|hesabı|hesabını|bakiyesi|bakiyesini|
            durumu|durumunu) [ne|nedir|kaç|ne kadar|söyle|göster]"
            varyant:   "{isim} ne kadar borcu var"
  Sorgu:    "kişileri listele/sırala" -> list_all
            "borçluları listele" -> list_debtors
            "alacaklıları listele" -> list_creditors
            "{ilçe}lileri listele" -> list_district (ör. "bergamalıları listele")
  Rapor (CLAUDE.md > "Rapor komutları — gelişmiş anlama"), en spesifikten
  en geneline sırayla denenir:
            "genel rapor" / "genel durum" / "tüm zamanların raporu" /
              "herkesin durumu" / "bütün müşteriler" / "tüm rapor" ->
              report_general
            "günlük rapor" / "günün raporu" / "bugünün raporu" /
              "bugünkü hareketler" / "bugün ne yaptık/oldu" -> report_daily
            "{isim} ekstresi/raporu/dökümü" / "{isim} hesap dökümü" ->
              report_person
            "rapor ver" / "rapor" (tek başına, tür belirsiz) -> report_menu
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.services.catalog import normalize

CURRENCY_UNITS = {"tl", "try", "lira", "₺"}

UNITS = {
    "balya", "kg", "kilo", "kilogram", "gram", "gr", "ton", "çuval",
    "adet", "litre", "lt", "paket", "koli", "kasa", "teneke", "varil",
    "metre", "m", "düzine", "kutu", "torba", "top", "dolap", "çift",
}

# Yön ayrımı (CLAUDE.md > "LLM son çare, regex birincil"): kişi eki değil,
# FİİL ÇEKİMİ zaten yönü kodluyor — "aldı" (3. şahıs, o aldı) = BORÇ ama
# "aldım/aldık" (1. şahıs, ben aldım) = TAHSİLAT. Bu ayrım kelime bazında
# net ve LLM'e bırakılamayacak kadar kritik (bkz. "mehmetten 5000 aldım").
DEBT_WORDS = {"aldı", "verdim", "verdik", "çekti", "sattım", "çıktı", "gitti"}
PAYMENT_WORDS = {
    "ödedi", "yatırdı", "verdi", "aldım", "aldık", "geldi", "tahsil",
}
BALANCE_KEYWORDS = {
    "borcu", "borcunu", "hesabı", "hesabını", "bakiyesi", "bakiyesini",
    "durumu", "durumunu",
}
# Anahtar kelimeden önce ("ne kadar borcu var") ya da sonra ("borcu ne
# kadar", "borcunu söyle") gelebilen, isme dahil edilmemesi gereken dolgu
# kelimeleri.
BALANCE_FILLERS = {"ne", "nedir", "kaç", "kadar", "söyle", "göster", "var"}

LIST_VERBS = {"listele", "sırala", "listeler", "sıralar", "listelesene", "sıralasana"}
LIST_FILLERS = {"tüm", "tum", "bütün", "butun", "hepsini", "hepsi", "lütfen", "lutfen", "bana"}
LIST_ALL_WORDS = {"kişileri", "kisileri", "kişiler", "kisiler", "herkesi", "herkes"}
LIST_DEBTORS_WORDS = {"borçluları", "borclulari", "borçlular", "borclular"}
LIST_CREDITORS_WORDS = {"alacaklıları", "alacaklilari", "alacaklılar", "alacaklilar"}

# Genel "rapor ver" / "rapor" isteği: hangi rapor istendiği belirsiz, bot
# günlük/genel seçimini buton ile sorar. Fiil ve dolgu kelimeleri hariç
# tutulduğunda tek kelime "rapor" kalmalı ("günlük rapor" gibi belirli bir
# tür istenmişse buraya düşmesin diye kasıtlı dar tutuldu).
REPORT_MENU_FILLERS = LIST_FILLERS | {"ver", "istiyorum", "çıkar"}

# Genel durum raporu: bir "nitelik" (genel/tüm/bütün/herkes...) VE bir
# "isim" (rapor/durum/müşteriler...) birlikte geçmeli — yalnız biri anlamsız
# ya da başka bir niyetle karışabilir (ör. "tüm kişileri listele" zaten
# _try_list_query'de yakalanır, buraya hiç düşmez).
REPORT_GENERAL_QUALIFIERS = {"genel", "tüm", "tum", "bütün", "butun", "herkesin", "herkes"}
REPORT_GENERAL_NOUNS = {
    "rapor", "raporu", "raporunu",
    "durum", "durumu", "durumunu",
    "müşteriler", "musteriler", "müşterileri", "musterileri",
    "zamanların", "zamanlarin",
}

# Günlük rapor: aynı nitelik+isim ikilisi mantığı, gün/bugün eksenli.
REPORT_DAILY_QUALIFIERS = {"gün", "günün", "bugün", "bugünün", "günlük", "bugünkü"}
REPORT_DAILY_NOUNS = {
    "rapor", "raporu", "raporunu",
    "hareketler", "hareketleri",
    "yaptık", "yaptik", "oldu",
}

# Kişiye özel ekstre/rapor/döküm isteği. "hesap dökümü" gibi iki kelimeli
# kalıplarda "hesap" isme dahil edilmemesi gereken bir dolgu kelimesidir.
REPORT_PERSON_KEYWORDS = {
    "ekstresi", "ekstresini", "ekstre",
    "raporu", "raporunu",
    "dökümü", "dökümünü", "döküm",
    "dokumu", "dokumunu", "dokum",
}
REPORT_PERSON_PRE_FILLERS = {"hesap"}
# Anahtar kelimeden önce bu kelimelerden biri geçiyorsa muhtemelen bir kişi
# adı değil, genel bir ifadedir ("bir durum raporu hazırla" gibi) — bu
# durumda uydurmadan pes edilir (None), böylece LLM'e devredilir.
REPORT_PERSON_EXCLUDED_PRECEDING = (
    REPORT_GENERAL_QUALIFIERS | REPORT_GENERAL_NOUNS | REPORT_DAILY_QUALIFIERS | REPORT_DAILY_NOUNS
)

# İlçe eki çözümü: sondan önce çoğul/belirtme (-leri/-ları/-i/-ı), sonra
# "ile ilgili" eki (-li/-lı/-lu/-lü) soyulur. Uzun ekler önce denenir ki
# "ahmetbeylerlileri" -> "leri" (değil "i") soyulsun.
_DISTRICT_SUFFIX_OUTER = ("leri", "ları", "i", "ı")
_DISTRICT_SUFFIX_INNER = ("li", "lı", "lu", "lü")


def _strip_district_suffix(word: str) -> str | None:
    """"bergamalıları" -> "bergama", "ahmetbeylerlileri" -> "ahmetbeyler".
    Ek bulunamazsa None (bu kelime bir ilçe adı çekimi değil demektir)."""
    stripped = word
    for suf in _DISTRICT_SUFFIX_OUTER:
        if stripped.endswith(suf) and len(stripped) > len(suf) + 1:
            stripped = stripped[: -len(suf)]
            break
    else:
        return None

    for suf in _DISTRICT_SUFFIX_INNER:
        if stripped.endswith(suf) and len(stripped) > len(suf) + 1:
            stripped = stripped[: -len(suf)]
            return stripped
    return None

# Niyet belirlendikten sonra kişi/ürün metninden temizlenen kelimeler.
STOPWORDS = DEBT_WORDS | PAYMENT_WORDS | {
    "borç", "borc", "yaz", "yazdı", "yazdım", "parası", "parasını",
    "için", "icin", "ettim",
}

_TR_ONES = {
    "bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5,
    "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
}
_TR_TENS = {
    "on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "elli": 50,
    "altmış": 60, "yetmiş": 70, "seksen": 80, "doksan": 90,
}
_TR_SCALES = {"yüz": 100, "bin": 1000, "milyon": 1_000_000}

# "buçuk" (yarım fazlası) bir sayı kelimesi değil ama zincirin son halkası
# olabilir ("üç buçuk" -> 3.5) — sözlük dışı ayrı işlenir (bkz. _consume_number).
_NUMBER_WORDS = set(_TR_ONES) | set(_TR_TENS) | set(_TR_SCALES) | {"buçuk"}

_NUMBER_TOKEN = re.compile(r"\d[\d.,]*")


def _segment_number_word(word: str, _cache: dict[str, list[str] | None] = {}) -> list[str] | None:
    """Bitişik yazılmış Türkçe sayı kelimelerini ayırır: "ikiyüz" ->
    ["iki", "yüz"], "binbeşyüz" -> ["bin", "beş", "yüz"]. Kelime TAMAMEN
    sayı kelimelerinden oluşmuyorsa None (rastgele bir isim/ürün adının
    yanlışlıkla bölünmemesi için tam kapsama şartı aranır)."""
    if word in _cache:
        return _cache[word]
    if word in _NUMBER_WORDS:
        _cache[word] = [word]
        return [word]
    result: list[str] | None = None
    for split in range(len(word) - 1, 0, -1):
        prefix, suffix = word[:split], word[split:]
        if prefix in _NUMBER_WORDS:
            rest = _segment_number_word(suffix)
            if rest is not None:
                result = [prefix] + rest
                break
    _cache[word] = result
    return result


@dataclass(slots=True)
class ParsedIntent:
    kind: str  # "debt" | "payment" | "balance_query" | "list_all" | "list_debtors" |
               # "list_creditors" | "list_district" | "report_menu" | "report_person" |
               # "report_general" | "report_daily"
    person_name: str | None = None
    qty: Decimal | None = None
    unit: str | None = None
    product: str | None = None
    amount: Decimal | None = None
    district: str | None = None


def _parse_amount(raw: str) -> Decimal | None:
    """"15000" -> 15000; "15.000" -> 15000 (binlik ayraç);
    "1.500,50" / "1500,50" -> 1500.50 (TR ondalık virgülü)."""
    raw = raw.strip()
    if not raw:
        return None
    has_dot = "." in raw
    has_comma = "," in raw
    try:
        if has_dot and has_comma:
            raw = raw.replace(".", "").replace(",", ".")
        elif has_comma:
            raw = raw.replace(",", ".")
        elif has_dot and re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
            raw = raw.replace(".", "")
        return Decimal(raw)
    except InvalidOperation:
        return None


def _consume_number(tokens: list[str], i: int) -> tuple[Decimal, int] | None:
    """tokens[i]'den başlayan bir sayıyı tüketir: düz rakam ("15000"),
    Türkçe sayı kelimesi dizisi ("on beş bin" -> 15000) ya da glue-split
    sonrası karışık biçim ("3"+"bin" -> 3000, "3"+"buçuk" -> 3.5). İlk
    token rakamsa (yalnızca i'de) taban değer olarak alınır, ardından gelen
    "yüz"/"bin"/"milyon"/"buçuk" ile zincirlenebilir — bu sayede "3bin" ASLA
    3 milyon değil 3000 olur (çarpan mantığı: bin=×1000, milyon=×1000000,
    yüz=×100, kökle çarpılır/toplanır, rakamla karıştırılmaz).
    Bulamazsa None döner."""
    total = Decimal(0)
    segment = Decimal(0)
    matched = False
    j = i
    while j < len(tokens):
        tok = tokens[j]
        if j == i and _NUMBER_TOKEN.fullmatch(tok):
            value = _parse_amount(tok)
            if value is None:
                break
            segment += value
            matched = True
            j += 1
            continue
        if tok in _TR_ONES:
            segment += _TR_ONES[tok]
            matched = True
            j += 1
            continue
        if tok in _TR_TENS:
            segment += _TR_TENS[tok]
            matched = True
            j += 1
            continue
        if tok == "buçuk":
            segment += Decimal("0.5")
            matched = True
            j += 1
            continue
        if tok in _TR_SCALES:
            scale = _TR_SCALES[tok]
            base = segment if segment else Decimal(1)
            if scale == 100:
                segment = base * scale
            else:  # bin, milyon: taban katmanı kapanır, toplama eklenir
                total += base * scale
                segment = Decimal(0)
            matched = True
            j += 1
            continue
        break
    if not matched:
        return None
    return total + segment, j - i


def parse_turkish_number(text: str) -> Decimal | None:
    """Tek bir sayı ifadesini uçtan uca çözer: "3bin" -> 3000, "3 bin" ->
    3000, "on beş bin" -> 15000, "ikiyüz" -> 200, "3buçuk" -> 3.5,
    "birmilyon" -> 1000000, "15000" -> 15000. Metnin TAMAMI tek bir sayıya
    karşılık gelmelidir (fazladan kelime kalırsa None) — cümle içinde geçen
    bir sayıyı bulmak için değil, sayı ifadesinin kendisini test etmek/
    çözmek için kullanılır (bkz. tests/test_sayi.py)."""
    tokens = _split_tokens(normalize(text or ""))
    if not tokens:
        return None
    consumed = _consume_number(tokens, 0)
    if consumed is None:
        return None
    value, length = consumed
    if length != len(tokens):
        return None
    return value


def _split_tokens(norm: str) -> list[str]:
    norm = re.sub(r"[.,!?;:]+(?=\s|$)", "", norm)          # cümle sonu noktalama
    # "15000tl" -> "15000 tl", "3bin" -> "3 bin", "3buçuk" -> "3 buçuk": rakama
    # bitişik yazılmış her türlü harf grubu (para birimi ya da sayı çarpanı) ayrılır.
    norm = re.sub(r"(\d)([a-zçğıöşü]+)", r"\1 \2", norm)
    norm = re.sub(r"(₺)(\d)", r"\1 \2", norm)
    tokens = norm.split()

    # Bitişik yazılmış Türkçe sayı kelimelerini ayır: "ikiyüz" -> "iki yüz",
    # "birmilyon" -> "bir milyon" (bkz. _segment_number_word — tam kapsama
    # şartı sayesinde rastgele bir isim/ürün adı yanlışlıkla bölünmez).
    expanded: list[str] = []
    for tok in tokens:
        if tok.isdigit() or len(tok) < 3:
            expanded.append(tok)
            continue
        segments = _segment_number_word(tok)
        expanded.extend(segments if segments else [tok])
    return expanded


# Para birimi ("tl"/"lira"/"₺") olmasa da hemen ardından "borç" gelen bir
# sayı da tutar sayılır ("15bin borç" -> 15000): "borç" burada para birimi
# yerine geçen bir tutar işaretçisi. Kind tespiti bu tokenlar tüketilmeden
# ÖNCE (parse() içinde) yapıldığı için "borç"un kind sinyali olarak
# kaybolması sorun olmaz (bkz. parse()).
_AMOUNT_MARKERS = CURRENCY_UNITS | {"borç", "borc"}


def _extract_amount(tokens: list[str]) -> tuple[Decimal | None, list[str]]:
    """Sayı + (tl|lira|₺|borç) ikilisini bulur, kalan tokenlardan çıkarır."""
    for i in range(len(tokens)):
        consumed = _consume_number(tokens, i)
        if consumed is None:
            continue
        value, length = consumed
        end = i + length
        if end < len(tokens) and tokens[end] in _AMOUNT_MARKERS:
            return value, tokens[:i] + tokens[end + 1:]
    return None, tokens


def _extract_qty_unit(
    tokens: list[str],
) -> tuple[Decimal | None, str | None, list[str], list[str]]:
    """İlk sayıyı adet olarak alır. Hemen ardından bilinen bir birim varsa
    onu kullanır, yoksa birim boş bırakılır (ürün serbest metin)."""
    for i in range(len(tokens)):
        consumed = _consume_number(tokens, i)
        if consumed is None:
            continue
        value, length = consumed
        end = i + length
        person_tokens = tokens[:i]
        rest = tokens[end:]
        if rest and rest[0] in UNITS:
            return value, rest[0], rest[1:], person_tokens
        return value, None, rest, person_tokens
    return None, None, [], tokens


def _detect_kind(tokens: list[str]) -> str | None:
    token_set = set(tokens)
    if token_set & DEBT_WORDS:
        return "debt"
    if token_set & PAYMENT_WORDS:
        return "payment"
    if "borç" in token_set or "borc" in token_set:
        return "debt"
    return None


def _try_balance_query(tokens: list[str]) -> ParsedIntent | None:
    """Anahtar kelime ("borcu"/"hesabı"/"bakiyesi"/"durumu" ve ekli halleri)
    tek başına yeterlidir — bir soru/emir kelimesi ("ne", "söyle"...) şart
    değil. Bu kelimeler isim öncesinde ("ne kadar borcu var") ya da
    sonrasında ("borcu ne kadar", "borcunu söyle") gelebilir; ikisinde de
    isme dahil edilmez."""
    idx = next((i for i, tok in enumerate(tokens) if tok in BALANCE_KEYWORDS), None)
    if idx is None:
        return None
    person_tokens = [t for t in tokens[:idx] if t not in BALANCE_FILLERS]
    person = " ".join(person_tokens).strip()
    if not person:
        return None
    return ParsedIntent(kind="balance_query", person_name=person)


def _try_list_query(tokens: list[str]) -> ParsedIntent | None:
    verb_idx = next((i for i, tok in enumerate(tokens) if tok in LIST_VERBS), None)
    if verb_idx is None:
        return None

    head = [t for t in tokens[:verb_idx] if t not in LIST_FILLERS]
    if len(head) != 1:
        return None
    word = head[0]

    if word in LIST_ALL_WORDS:
        return ParsedIntent(kind="list_all")
    if word in LIST_DEBTORS_WORDS:
        return ParsedIntent(kind="list_debtors")
    if word in LIST_CREDITORS_WORDS:
        return ParsedIntent(kind="list_creditors")

    district = _strip_district_suffix(word)
    if district:
        return ParsedIntent(kind="list_district", district=district)
    return None


def _try_report_general(tokens: list[str]) -> ParsedIntent | None:
    """"genel rapor", "tüm zamanların raporu", "herkesin durumu",
    "bütün müşteriler" vb. -> tek anlamı olan genel durum raporu, menü
    sorulmadan direkt üretilir."""
    token_set = set(tokens)
    if token_set & REPORT_GENERAL_QUALIFIERS and token_set & REPORT_GENERAL_NOUNS:
        return ParsedIntent(kind="report_general")
    return None


def _try_report_daily(tokens: list[str]) -> ParsedIntent | None:
    """"günlük rapor", "bugünün raporu", "bugün ne oldu" vb. -> tek anlamı
    olan günlük rapor, menü sorulmadan direkt üretilir."""
    token_set = set(tokens)
    if token_set & REPORT_DAILY_QUALIFIERS and token_set & REPORT_DAILY_NOUNS:
        return ParsedIntent(kind="report_daily")
    return None


def _try_report_person_query(tokens: list[str]) -> ParsedIntent | None:
    """"{isim} ekstresi/raporu/dökümü" / "{isim} hesap dökümü" -> kişinin
    ekstre PDF'i istenir. Anahtar kelimeden önce genel/günlük rapor
    kelimelerinden biri geçiyorsa (ör. "bir durum raporu") bu bir kişi adı
    değildir — uydurmadan None dönülür, LLM'e bırakılır."""
    idx = next((i for i, tok in enumerate(tokens) if tok in REPORT_PERSON_KEYWORDS), None)
    if idx is None:
        return None
    person_tokens = [t for t in tokens[:idx] if t not in REPORT_PERSON_PRE_FILLERS]
    if not person_tokens or set(person_tokens) & REPORT_PERSON_EXCLUDED_PRECEDING:
        return None
    person = " ".join(person_tokens).strip()
    if not person:
        return None
    return ParsedIntent(kind="report_person", person_name=person)


def _try_report_menu(tokens: list[str]) -> ParsedIntent | None:
    """"rapor ver" / "rapor" -> hangi rapor istendiği belirsiz, bot günlük/genel
    seçimini buton ile sorar (kişiye özel istekler _try_report_person_query'de)."""
    remaining = [t for t in tokens if t not in REPORT_MENU_FILLERS]
    if remaining == ["rapor"]:
        return ParsedIntent(kind="report_menu")
    return None


def parse(raw_text: str) -> ParsedIntent | None:
    text = " ".join((raw_text or "").split())
    if not text:
        return None

    norm = normalize(text)
    tokens = _split_tokens(norm)
    if not tokens:
        return None

    listing = _try_list_query(tokens)
    if listing is not None:
        return listing

    # Rapor niyetleri en spesifikten en geneline denenir (genel/günlük/kişi
    # önce, tek başına "rapor" en sona): "genel raporu"/"günlük raporu" gibi
    # kalıplar "raporu" kişi-eki ile de eşleşebildiği için, kişi kontrolü
    # bunlardan SONRA çalışmalı — yoksa "genel"/"günlük" bir kişi adı sanılır.
    report_general = _try_report_general(tokens)
    if report_general is not None:
        return report_general

    report_daily = _try_report_daily(tokens)
    if report_daily is not None:
        return report_daily

    report_person = _try_report_person_query(tokens)
    if report_person is not None:
        return report_person

    report_menu = _try_report_menu(tokens)
    if report_menu is not None:
        return report_menu

    # Bug (2026-07-26): "ahmet yılmaz 20 balya borcunu 15000 tl ödedi" gibi
    # bir TAHSİLAT cümlesi "borcunu" (bakiye anahtar kelimesi) içerdiği
    # için _try_balance_query'ye düşüyor ve "ahmet yılmaz 20 balya" diye
    # anlamsız bir isimle sahte bir bakiye sorgusuna dönüşüyordu; bu da
    # kural parser'ın "çözdüm" sanıp LLM'e hiç devretmemesine yol açıyordu.
    # Cümlede HEM bir bakiye kelimesi HEM bir borç/tahsilat fiili
    # (aldı/verdim/çekti/ödedi/yatırdı/verdi) varsa bu ikisi çelişir —
    # kural parser'ın basit "sayı/birim/ürün" ayrıştırması böyle karışık
    # bir cümleyi güvenle çözemez, LLM'e bırakılır (None dön).
    token_set = set(tokens)
    if token_set & BALANCE_KEYWORDS and token_set & (DEBT_WORDS | PAYMENT_WORDS):
        return None

    balance = _try_balance_query(tokens)
    if balance is not None:
        return balance

    # Kind, tutar çıkarılmadan ÖNCE tespit edilir: "borç" hem bir fiil
    # sinyali hem de (bkz. _extract_amount) tutarın bitişiğindeki bir
    # işaretçi olarak tüketilebilir ("15bin borç" -> 15000). Sıra tersine
    # çevrilirse ("...borç" tüketildikten sonra kind aransa) tek kind
    # sinyali "borç" olan cümlelerde (fiil hiç yoksa) kind kaybolurdu.
    kind = _detect_kind(tokens)
    if kind is None:
        return None

    amount, tokens = _extract_amount(tokens)

    tokens = [t for t in tokens if t not in STOPWORDS]
    qty, unit, product_tokens, person_tokens = _extract_qty_unit(tokens)

    # Adet-birim bulunamadıysa ve ürün de yoksa, bu sayı aslında bir ADET
    # değil çıplak bir TUTARDIR ("mehmete 3000 verdim" — para birimi/"borç"
    # eki yok ama "3000" tek başına kalan sayı, ürün/birim bağlamı da yok).
    # CLAUDE.md > "Para vs adet": birim kelimesi yoksa sayı tutar sayılır.
    if amount is None and qty is not None and unit is None and not product_tokens:
        amount, qty = qty, None

    person_name = " ".join(person_tokens).strip() or None
    product = " ".join(product_tokens).strip() or None

    if not person_name:
        return None

    return ParsedIntent(
        kind=kind,
        person_name=person_name,
        qty=qty,
        unit=unit,
        product=product,
        amount=amount,
    )
