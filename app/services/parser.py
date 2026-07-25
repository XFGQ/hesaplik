"""Kural tabanlı Türkçe komut çözümleyici (Faz 3, adım 2).

LLM yok: yalnızca regex + sözlük eşleştirme. Girdi ham metin, çıktı
ParsedIntent ya da (anlaşılamadıysa) None. Belirsiz kalan alan uydurulmaz,
boş bırakılır — kararı intent_resolver ve bot verir.

Desteklenen kalıplar (kelime sırası biraz oynayabilir):
  Borç:     "{isim} {sayı} {birim} {ürün} aldı {tutar} tl borç"
            varyantlar: "aldı", "verdim", "çekti", "borç yaz(dı)"
  Tahsilat: "{isim} {tutar} tl (ödedi|verdi|yatırdı)"
            ürünlü:    "{isim} {sayı} {birim} {ürün} parası ödedi {tutar} tl"
  Bakiye:   "{isim} (borcu|hesabı|bakiyesi|durumu) (ne|nedir|kaç|ne kadar)"
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

DEBT_WORDS = {"aldı", "verdim", "çekti"}
PAYMENT_WORDS = {"ödedi", "yatırdı", "verdi"}
BALANCE_KEYWORDS = {"borcu", "hesabı", "bakiyesi", "durumu"}
BALANCE_QUESTIONS = {"ne", "nedir", "kaç", "kadar"}

# Niyet belirlendikten sonra kişi/ürün metninden temizlenen kelimeler.
STOPWORDS = DEBT_WORDS | PAYMENT_WORDS | {
    "borç", "borc", "yaz", "yazdı", "yazdım", "parası", "parasını",
    "için", "icin",
}

_TR_ONES = {
    "bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5,
    "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
}
_TR_TENS = {
    "on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "elli": 50,
    "altmış": 60, "yetmiş": 70, "seksen": 80, "doksan": 90,
}
_TR_SCALES = {"yüz": 100, "bin": 1000}

_NUMBER_TOKEN = re.compile(r"\d[\d.,]*")


@dataclass(slots=True)
class ParsedIntent:
    kind: str  # "debt" | "payment" | "balance_query"
    person_name: str | None = None
    qty: Decimal | None = None
    unit: str | None = None
    product: str | None = None
    amount: Decimal | None = None


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


def _words_to_number(words: list[str]) -> int | None:
    total = 0
    segment = 0
    matched = False
    for w in words:
        if w in _TR_ONES:
            segment += _TR_ONES[w]
            matched = True
        elif w in _TR_TENS:
            segment += _TR_TENS[w]
            matched = True
        elif w in _TR_SCALES:
            scale = _TR_SCALES[w]
            if scale == 1000:
                total += (segment or 1) * scale
                segment = 0
            else:
                segment = (segment or 1) * scale
            matched = True
        else:
            break
    if not matched:
        return None
    return total + segment


def _consume_number(tokens: list[str], i: int) -> tuple[Decimal, int] | None:
    """tokens[i]'den başlayan bir sayıyı (rakam ya da Türkçe sayı kelimesi
    dizisi) tüketir. Bulamazsa None."""
    tok = tokens[i]
    if _NUMBER_TOKEN.fullmatch(tok):
        value = _parse_amount(tok)
        return (value, 1) if value is not None else None

    words: list[str] = []
    j = i
    while j < len(tokens) and (
        tokens[j] in _TR_ONES or tokens[j] in _TR_TENS or tokens[j] in _TR_SCALES
    ):
        words.append(tokens[j])
        j += 1
    if not words:
        return None
    num = _words_to_number(words)
    if num is None:
        return None
    return Decimal(num), j - i


def _split_tokens(norm: str) -> list[str]:
    norm = re.sub(r"[.,!?;:]+(?=\s|$)", "", norm)          # cümle sonu noktalama
    norm = re.sub(r"(\d)(tl|try|lira|₺)\b", r"\1 \2", norm)  # "15000tl" -> "15000 tl"
    norm = re.sub(r"(₺)(\d)", r"\1 \2", norm)
    return norm.split()


def _extract_amount(tokens: list[str]) -> tuple[Decimal | None, list[str]]:
    """Sayı + (tl|lira|₺) ikilisini bulur, kalan tokenlardan çıkarır."""
    for i in range(len(tokens)):
        consumed = _consume_number(tokens, i)
        if consumed is None:
            continue
        value, length = consumed
        end = i + length
        if end < len(tokens) and tokens[end] in CURRENCY_UNITS:
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
    for i, tok in enumerate(tokens):
        if tok in BALANCE_KEYWORDS:
            rest = tokens[i + 1:]
            if rest and any(w in BALANCE_QUESTIONS for w in rest):
                person = " ".join(tokens[:i]).strip()
                if person:
                    return ParsedIntent(kind="balance_query", person_name=person)
    return None


def parse(raw_text: str) -> ParsedIntent | None:
    text = " ".join((raw_text or "").split())
    if not text:
        return None

    norm = normalize(text)
    tokens = _split_tokens(norm)
    if not tokens:
        return None

    balance = _try_balance_query(tokens)
    if balance is not None:
        return balance

    amount, tokens = _extract_amount(tokens)

    kind = _detect_kind(tokens)
    if kind is None:
        return None

    tokens = [t for t in tokens if t not in STOPWORDS]
    qty, unit, product_tokens, person_tokens = _extract_qty_unit(tokens)

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
