"""Tek mesajda birden çok işlem (CLAUDE.md > "Tek mesajda birden çok
istek", Grup 5). "mehmetten 5000 aldım aliye 500 mal gitti" gibi bir
mesaj iki ayrı işlem olabilir; sistem bunları sırayla, ayrı ayrı işlemeli.

KRİTİK GÜVENLİK İLKESİ: yanlış bölmektense tek bırak. Bölme SADECE her
parça BAĞIMSIZ olarak geçerli bir işleme parse edilebiliyorsa yapılır —
şüphe varsa (bir parça anlamsızsa/None dönüyorsa) TÜM metin tek işlem
sayılır. Yanlış bölme para hatası yapar (ör. bir cümleyi ikiye bölüp
yanlış kişiye/miktara borç yazmak), bölmemek yalnızca "anlaşılamadı"
sonucuna yol açar — asimetrik risk, o yüzden temkinli tarafta kalınır.

Üç bölme stratejisi sırayla denenir, ilk geçerli olan kullanılır:
  1. Satır sonu (\\n) — her satır bağımsız parse olmalı.
  2. " ve " bağlacı — her parça bağımsız parse olmalı.
  3. Ayraçsız art arda gelen {isim}+{işlem} kalıpları (ör. "mehmetten 5000
     aldım aliye 500 mal gitti") — en riskli strateji, bu yüzden en sıkı
     validasyon: yalnızca borç/tahsilat (kind debt/payment) VE bir sayı
     (tutar ya da adet) içeren parçalar kabul edilir.

Hiçbiri geçerli bir bölme üretmezse [text] (tek elemanlı liste) döner.
"""

from __future__ import annotations

import re

from app.services import parser as parser_module
from app.services.parser import ParsedIntent

# "search" kind, tek bir kelimenin/ifadenin HİÇBİR bilinen komuta uymadığında
# düşülen genel bir "yakala" niyetidir (bkz. parser._try_single_word_search).
# Bir bölme parçası yalnızca "search"e düşüyorsa bu, o parçanın aslında
# anlamlı bir işlem OLMADIĞININ işaretidir — böyle bir parçayı geçerli
# saymak neredeyse her rastgele kelimeyi/cümle parçasını "bölünebilir"
# yapardı. Bu yüzden hem satır hem " ve " stratejisinde "search" reddedilir.
_MEANINGLESS_KINDS = {"search"}


def _is_meaningful_piece(intent: ParsedIntent | None) -> bool:
    return intent is not None and intent.kind not in _MEANINGLESS_KINDS


# Ayraçsız (pattern-based) bölme yalnızca borç/tahsilat için denenir —
# bakiye sorgusu/liste/rapor gibi niyetler bu kalıpta hiç aranmaz, çünkü
# ayraç olmadan iki ayrı sorgu ile bir borç cümlesinin sınırını güvenle
# bulmak (kişi eşleştirme + tutar çıkarımı üstüne üstüne) aşırı risklidir.
_TRANSACTIONAL_KINDS = {"debt", "payment"}


def _is_complete_transaction(intent: ParsedIntent | None) -> bool:
    """"aliye 500 mal gitti" gibi kalemli ama tutarsız bir cümle bile
    (amount=None) bir işlem SAYILIR (bkz. modül docstring'i — bölme
    SAYISINI test eden görevler bunu bekler), yeter ki bir SAYI (tutar ya
    da adet) VE bir kişi adı içersin — "mal gitti" gibi sayısız/kişisiz bir
    kırıntı ("gitti" tek başına DEBT_WORDS'te olduğu için yanlışlıkla debt
    sayılabilir) bu şartla elenir."""
    return (
        intent is not None
        and intent.kind in _TRANSACTIONAL_KINDS
        and bool(intent.person_name)
        and (intent.amount is not None or intent.qty is not None)
    )


_VE_RE = re.compile(r"\bve\b", re.IGNORECASE)


def _split_by_ve(text: str) -> list[str] | None:
    if not _VE_RE.search(text):
        return None
    parts = [p.strip() for p in _VE_RE.split(text) if p.strip()]
    if len(parts) < 2:
        return None
    if all(_is_meaningful_piece(parser_module.parse(p)) for p in parts):
        return parts
    return None


def _split_by_newline(text: str) -> list[str] | None:
    """Satır sonu, " ve "/ayraçsız kalıptan FARKLI olarak, KOŞULSUZ bölünür
    — kullanıcının kendi bastığı Enter tuşu zaten belirsizlik taşımayan,
    kasıtlı bir ayraçtır (nereden böleceğimizi TAHMİN etmiyoruz, kullanıcı
    zaten söylemiş). Bir satır tek başına anlaşılmaz çıkarsa (parse None)
    bu BÖLMEYİ geçersiz kılmaz — o satır kendi başına "anlaşılamadı"
    sayılır, DİĞER satırlar yine de işlenir (CLAUDE.md > "Tek mesajda
    birden çok istek": "bir parça anlaşılmazsa... diğer işlemleri yine de
    işle")."""
    if "\n" not in text:
        return None
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    return lines


_MIN_PATTERN_TOKENS = 4  # en az 2+2 token (her parçada en az bir isim+bir sayı)


def _split_by_pattern(text: str) -> list[str] | None:
    """Ayraçsız art arda gelen {isim}+{işlem} kalıplarını dener. Soldan
    sağa ilk geçerli bölme noktasını kullanır: sol taraf TAM bir işlem
    olarak parse olmalı VE sağ taraf da (doğrudan ya da kendi içinde tekrar
    bölünerek) tamamen geçerli işlem(ler)e ayrılabilmeli. Sağ taraf hiçbir
    i için doğrulanamazsa None (bölme yok)."""
    tokens = text.split()
    if len(tokens) < _MIN_PATTERN_TOKENS:
        return None

    for i in range(2, len(tokens) - 1):
        left = " ".join(tokens[:i])
        if not _is_complete_transaction(parser_module.parse(left)):
            continue

        right = " ".join(tokens[i:])
        if _is_complete_transaction(parser_module.parse(right)):
            return [left, right]

        further = _split_by_pattern(right)
        if further is not None:
            return [left, *further]

    return None


def split_into_requests(text: str) -> list[str]:
    """Mesajı bağımsız işlem parçalarına böler. Güvenle bölünemiyorsa
    [text] (tek elemanlı) döner — çağıran taraf (bot) bunu her zaman mevcut
    tek-işlem akışıyla aynı şekilde işleyebilir."""
    stripped = (text or "").strip()
    if not stripped:
        return [stripped]

    newline_split = _split_by_newline(stripped)
    if newline_split is not None:
        return newline_split

    ve_split = _split_by_ve(stripped)
    if ve_split is not None:
        return ve_split

    pattern_split = _split_by_pattern(stripped)
    if pattern_split is not None:
        return pattern_split

    return [stripped]
