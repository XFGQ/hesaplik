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
  - yönelme (dative): tamponsuz -e/-a (ahmete -> ahmet), tamponlu -ye/-ya
    (aliye -> ali).

Bu basit bir sezgisel sökücüdür, tam bir Türkçe morfolojik çözümleyici
değil: nadir durumlarda (örn. kökü de "n" ile biten adlar) tamponlu/
tamponsuz iyelik ayrımı belirsiz kalabilir; bu durumda daha UZUN kökü
(daha az agresif soyma) tercih ederiz.
"""

from __future__ import annotations

from app.services.catalog import normalize

MIN_ROOT_LEN = 2

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

    # Yönelme (dative), tamponlu (kök ünlüyle bitiyor): aliye -> ali.
    if len(word) >= 3 and word[-2:] in ("ye", "ya"):
        root = word[:-2]
        if len(root) >= MIN_ROOT_LEN:
            return root
        return word

    # Yönelme (dative), tamponsuz (kök ünsüzle bitiyor): ahmete -> ahmet.
    if word[-1] in _DATIVE_VOWELS:
        root = word[:-1]
        if len(root) >= MIN_ROOT_LEN:
            return root
        return word

    return word


def strip_turkish_suffix(name: str) -> str:
    """Adın normalize edilmiş halini, SON kelimesindeki yaygın Türkçe
    çekim eki soyulmuş olarak döner. Boş girdide boş döner."""
    norm = normalize(name or "")
    words = norm.split()
    if not words:
        return norm
    words[-1] = _strip_word(words[-1])
    return " ".join(words)
