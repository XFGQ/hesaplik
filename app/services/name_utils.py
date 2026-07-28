"""Türkçe ad eki temizleme (CLAUDE.md > "KRİTİK — LLM isim bozuyor").

LLM'e Türkçe çekim eki sökme işi bırakılamaz: bazen harf uydurup ismi
bozuyor ("mehmetten" -> "mehtap" gibi). Bu yüzden ek temizleme KOD
tarafında, deterministik ve LLM'den bağımsız yapılır — hem regex parser'dan
hem LLM'den gelen kişi adı burada normalize edilip SONRA pg_trgm ile
eşleştirilir (bkz. app/services/intent_resolver.py).

Yalnızca kişi adının SON kelimesine uygulanır: ad soyadsa yalnızca soyadın
eki soyulur, adın kendisi değişmez.

Kapsanan ekler:
  - iyelik (genitive): -in/-ın/-un/-ün (ahmetin -> ahmet), tamponlu
    -nin/-nın/-nun/-nün ünlüyle biten kökte de aynı 2 harfli yol denenir
    önce (bkz. _strip_word) — kısa kalırsa tamponlu yola düşülür.
  - ünsüz yumuşaması: iyelik ekinden önce kökün son ünsüzü sertleşmiş
    olabilir (mehmedin -> mehmed -> mehmet; d/c/b/g -> t/ç/p/k).
  - ayrılma (ablatif): -den/-dan/-ten/-tan (ahmetten -> ahmet).
  - yönelme (dative), yalnızca TAMPONLU -ye/-ya (aliye -> ali).

KRİTİK BUG (2026-07-28, düzeltildi): tamponSUZ yönelme eki (çıplak -e/-a,
"ahmete -> ahmet") KASTEN SÖKÜLMEZ. Bu ek tek bir harf ("e"/"a") ve Türkçe
isimlerin çoğu zaten sesli harfle bitiyor ("esma", "ayşe", "fatma",
"hatice") — bu ekle isim sonu ayırt edilemez, "esma" yanlışlıkla "esm"e
kesiliyordu (para/kişi güvenliği ihlali: kişi hiç bulunamıyordu). Prensip:
YANLIŞ KESMEKTENSE HİÇ KESME — eşleştirme zaten pg_trgm fuzzy olduğundan ek
kalsa bile ("ahmete") yakın eşleşme/aday olarak bulunur, ama isim
kesildiğinde ("esm") eşleşme TAMAMEN kaçar. Tamponlu -ye/-ya ("aliye")
sökülmeye devam eder çünkü 2 harflik daha belirgin bir örüntüdür (çıplak
kökler nadiren "ye"/"ya" ile biter).

Bu basit bir sezgisel sökücüdür, tam bir Türkçe morfolojik çözümleyici
değil: nadir durumlarda (örn. kökü de "n" ile biten adlar) tamponlu/
tamponsuz iyelik ayrımı belirsiz kalabilir; bu durumda daha UZUN kökü
(daha az agresif soyma) tercih ederiz.

Ayrıca burada BAĞLAM KELİMESİ ayıklama da yapılır (bkz. CLAUDE.md > "LLM
serbest cümleden kişi adını yanlış çıkarıyor" bug'ı, 2026-07-28): LLM ya da
regex bazen isim öbeğine "hesabının", "durumu", "dökümünü" gibi komut/bağlam
kelimelerini de katıyor ("ahmetin hesabının dökümünü çıkar" -> kişi adı
yanlışlıkla "ahmetin hesabının" oluyor). Bunlar gerçek isim DEĞİL, rapor/
bakiye komutunun parçası — eşleştirmeden önce ayıklanır (bkz.
strip_context_words). Bu ayıklama hem regex parser'dan hem LLM'den gelen
isimde aynı şekilde uygulanır (tek giriş noktası: strip_turkish_suffix).

Aynı katmanda HİTAP kelimeleri de ayıklanır (bkz. CLAUDE.md > "Kişi bilgi
sorgusu + hitap kelimeleri"): "abla", "abi/ağabey", "bey", "hanım", "amca",
"dayı", "teyze", "hala", "usta", "hoca", "efendi", "kardeş", "bacı" gerçek
isim değil, hitaptır ("esma abla" -> "esma", "ahmet usta" -> "ahmet").
Yalnızca ismin SON kelimesi kontrol edilir (bkz. strip_honorific) — bilinen
hitap listesi dışındaki gerçek soyadlara dokunulmaz ("esma şeker" değişmez).
"""

from __future__ import annotations

from app.services.catalog import normalize
from app.services.parser import BALANCE_KEYWORDS, REPORT_PERSON_KEYWORDS

MIN_ROOT_LEN = 2

# Genitif (-nın/-nin/-nun/-nün) hâlleri BALANCE_KEYWORDS/REPORT_PERSON_KEYWORDS'te
# yok (o setler parser.py'nin kendi ihtiyaçları için dar tutulmuş) — burada
# "ahmetin hesabının..." gibi tamlamalarda ayrıca gerekiyor.
_CONTEXT_GENITIVE_EXTRAS = {
    "hesabının", "durumunun", "bakiyesinin", "borcunun",
    "ekstresinin", "raporunun", "dökümünün", "dokumunun",
}
CONTEXT_WORDS = BALANCE_KEYWORDS | REPORT_PERSON_KEYWORDS | _CONTEXT_GENITIVE_EXTRAS

# Hitap kelimeleri (CLAUDE.md > "Kişi bilgi sorgusu + hitap kelimeleri"):
# isim değil, hitaptır. Bağlam kelimelerinden farklı olarak yalnızca ismin
# SON kelimesiyken ayıklanır (bkz. strip_honorific) — "hala" gibi bazı
# hitaplar nadiren gerçek isim de olabilir, orta kelimede dokunulmaz.
HONORIFIC_WORDS = {
    "abla", "abi", "ağabey", "agabey", "bey", "hanım", "hanim",
    "amca", "dayı", "dayi", "teyze", "hala", "usta", "hoca",
    "efendi", "kardeş", "kardes", "bacı", "baci",
}

# Ünsüz yumuşaması: iyelik eki (-in/-ın/-un/-ün) sertleşmiş kökün üstüne
# geldiğinde kökün son ünsüzü yumuşar (t->d, ç->c, p->b, k->ğ/g). Sökerken
# tersini uygularız.
_SOFTEN_TO_HARD = {"d": "t", "c": "ç", "b": "p", "g": "k"}

_GENITIVE_VOWELS = "iıuü"
_DATIVE_VOWELS = "ea"
_ABLATIVE_CONSONANTS = ("t", "d")


def _strip_word(word: str) -> str:
    if len(word) <= MIN_ROOT_LEN:
        return word

    # İyelik (-in/-ın/-un/-ün) ya da ayrılma (-ten/-tan/-den/-dan): ikisi de
    # sonu "n" ile biter, ayırıcı bir önceki harf (i/ı/u/ü -> iyelik, e/a ->
    # ayrılma).
    if word[-1] == "n" and len(word) >= 3:
        prev_vowel = word[-2]
        before = word[-3]

        if prev_vowel in _GENITIVE_VOWELS:
            # Ünsüz yumuşamalı iyelik: mehmedin -> mehmed -> mehmet.
            if before in _SOFTEN_TO_HARD:
                root = word[:-2]
                hardened = root[:-1] + _SOFTEN_TO_HARD[root[-1]]
                if len(hardened) >= MIN_ROOT_LEN:
                    return hardened
            # Düz iyelik, kök ünsüzle bitiyor: ahmetin -> ahmet,
            # furkanın -> furkan.
            root = word[:-2]
            if len(root) >= MIN_ROOT_LEN:
                return root
            # Kök çok kısa kaldıysa tamponlu (kök ünlüyle biter) ihtimalini
            # dene: sunun -> su.
            if before == "n" and len(word) >= 4:
                buffered_root = word[:-3]
                if len(buffered_root) >= MIN_ROOT_LEN:
                    return buffered_root
            return word

        if prev_vowel in _DATIVE_VOWELS and before in _ABLATIVE_CONSONANTS:
            # Ayrılma (ablatif): ahmetten -> ahmet, bergamadan -> bergama.
            root = word[:-3]
            if len(root) >= MIN_ROOT_LEN:
                return root
            return word

    # Yönelme (dative), yalnızca TAMPONLU (kök ünlüyle bitiyor): aliye ->
    # ali. Tamponsuz biçim (çıplak -e/-a, "ahmete" -> "ahmet") KASTEN
    # sökülmez (bkz. modül docstring'i, "KRİTİK BUG" notu) — "esma", "ayşe",
    # "fatma" gibi doğal olarak sesli biten isimlerle ayırt edilemiyordu.
    if len(word) >= 3 and word[-2:] in ("ye", "ya"):
        root = word[:-2]
        if len(root) >= MIN_ROOT_LEN:
            return root
        return word

    return word


def strip_context_words(name: str) -> str:
    """İsim öbeğinin içinden bağlam/komut kelimelerini ("hesabının",
    "durumu", "ekstresi" vb., bkz. CONTEXT_WORDS) ayıklar. Yalnızca TAM
    kelime eşleşmesiyle çalışır, gerçek isim kelimelerine dokunmaz —
    "ahmet yılmaz" gibi normal bir isim bu fonksiyondan değişmeden çıkar."""
    norm = normalize(name or "")
    words = [w for w in norm.split() if w not in CONTEXT_WORDS]
    return " ".join(words)


def strip_honorific(name: str) -> str:
    """Sondaki hitap kelimesini ayıklar: "esma abla" -> "esma", "ahmet
    usta" -> "ahmet". Yalnızca SON kelime kontrol edilir ve en az bir
    kelime daha kalması gerekir — tek başına "abla" gibi bir girdi olduğu
    gibi bırakılır (belki gerçek bir isimdir). Bilinen hitap listesi
    dışındaki soyadlara dokunmaz ("esma şeker" değişmeden kalır)."""
    norm = normalize(name or "")
    words = norm.split()
    if len(words) >= 2 and words[-1] in HONORIFIC_WORDS:
        return " ".join(words[:-1])
    return norm


def strip_turkish_suffix(name: str) -> str:
    """Adın normalize edilmiş halini, önce bağlam kelimelerinden (bkz.
    strip_context_words) ve sondaki hitaptan (bkz. strip_honorific)
    arındırılmış, sonra SON kelimesindeki yaygın Türkçe çekim eki soyulmuş
    olarak döner. Boş girdide boş döner."""
    norm = strip_context_words(name)
    norm = strip_honorific(norm)
    words = norm.split()
    if not words:
        return norm
    words[-1] = _strip_word(words[-1])
    return " ".join(words)
