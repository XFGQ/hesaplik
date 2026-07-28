import pytest

from app.services.name_utils import strip_context_words, strip_honorific, strip_turkish_suffix


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ahmetten", "ahmet"),
        ("mehmetin", "mehmet"),
        ("aliye", "ali"),
        ("ahmedin", "ahmet"),
        ("furkanın", "furkan"),
        ("mehmetten", "mehmet"),
        ("bergamadan", "bergama"),
        ("ahmetin", "ahmet"),
        ("mehmedin", "mehmet"),
        ("dumanın", "duman"),
    ],
)
def test_strip_turkish_suffix_bilinen_ekler(raw, expected):
    assert strip_turkish_suffix(raw) == expected


def test_strip_turkish_suffix_ekli_olmayan_isim_degismez():
    assert strip_turkish_suffix("ahmet") == "ahmet"
    assert strip_turkish_suffix("ali") == "ali"


def test_strip_turkish_suffix_sadece_son_kelimeye_uygulanir():
    # Ad soyadsa yalnızca soyadın eki soyulur, adın kendisi değişmez.
    assert strip_turkish_suffix("ahmet yılmazın") == "ahmet yılmaz"
    assert strip_turkish_suffix("furkan dumandan") == "furkan duman"


# ------------------------------------------------------------------
# KRİTİK BUG (2026-07-28): tamponsuz yönelme eki (çıplak -e/-a) sesli
# harfle biten gerçek isimlerle ayırt edilemiyordu ("esma" -> "esm" gibi
# yanlış kesim, kişi hiç bulunamıyordu — para/kişi güvenliği ihlali).
# Artık bu ek KASTEN sökülmez; ek kalsa bile pg_trgm fuzzy eşleştirme onu
# tolere eder, ama isim yanlış kesilirse eşleşme tamamen kaçar.

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("esma", "esma"),
        ("ayşe", "ayşe"),
        ("fatma", "fatma"),
        ("hatice", "hatice"),
        ("emine", "emine"),
    ],
)
def test_strip_turkish_suffix_sesliyle_biten_isimler_korunur(raw, expected):
    assert strip_turkish_suffix(raw) == expected


@pytest.mark.parametrize("raw", ["ahmete", "dumana", "mehmete", "furkana"])
def test_strip_turkish_suffix_tamponsuz_yonelme_eki_artik_sokulmez(raw):
    # Riskli tek harflik -e/-a eki artık KORUNUR (yanlış kesmektense hiç
    # kesme) — bu, önceki davranıştan kasıtlı bir sapmadır.
    assert strip_turkish_suffix(raw) == raw


def test_strip_turkish_suffix_bos_girdi():
    assert strip_turkish_suffix("") == ""
    assert strip_turkish_suffix(None) == ""


def test_strip_turkish_suffix_cok_kisa_kok_soyulmaz():
    # Kök MIN_ROOT_LEN altına düşecekse soyma yapılmaz (aşırı soyma yok).
    assert strip_turkish_suffix("in") == "in"
    assert strip_turkish_suffix("ay") == "ay"


def test_strip_turkish_suffix_buyuk_harf_ve_turkce_i_normalize_edilir():
    assert strip_turkish_suffix("AHMETİN") == "ahmet"


# ------------------------------------------------------------------
# Bağlam kelimesi ayıklama (CLAUDE.md 2026-07-28 bug'ı): LLM/regex bazen
# isim öbeğine "hesabının", "durumu", "dökümünü" gibi komut kelimelerini de
# katıyor — bunlar gerçek isim değil, ayıklanmalı.

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ahmetin hesabının dökümünü", "ahmetin"),
        ("Ahmetin Hesabının", "ahmetin"),
        ("ahmetin hesabını", "ahmetin"),
        ("mehmetin durumu", "mehmetin"),
        ("mehmetin durumunu", "mehmetin"),
        ("furkan ekstresi", "furkan"),
        ("furkan raporu", "furkan"),
        ("furkan bakiyesinin", "furkan"),
        ("furkan borcunun", "furkan"),
    ],
)
def test_strip_context_words_baglam_kelimesi_ayiklanir(raw, expected):
    assert strip_context_words(raw) == expected


def test_strip_context_words_normal_isim_bozulmaz():
    # Komut kelimesi olmayan normal bir isim değişmeden kalmalı.
    assert strip_context_words("ahmet yılmaz") == "ahmet yılmaz"
    assert strip_context_words("ali veli") == "ali veli"


def test_strip_context_words_iki_kelimeli_isim_korunur():
    # "ekstresi" ayıklanır ama isim iki kelimeli kalır (bkz. "veli" bir
    # bağlam kelimesi değil, isme dahil).
    assert strip_context_words("ali velinin ekstresi") == "ali velinin"


def test_strip_turkish_suffix_baglam_kelimesi_ve_ek_birlikte():
    # Bağlam kelimesi ayıklama + ek soyma zinciri: "Ahmetin Hesabının" bug'ı
    # (2026-07-28) — kişi adına yanlışlıkla katılan bağlam kelimeleri
    # ayıklanır, SONRA gerçek ismin eki soyulur.
    assert strip_turkish_suffix("Ahmetin Hesabının") == "ahmet"
    assert strip_turkish_suffix("ahmetin hesabının dökümünü") == "ahmet"
    assert strip_turkish_suffix("mehmetin durumu") == "mehmet"
    assert strip_turkish_suffix("ahmet yılmaz") == "ahmet yılmaz"


# ------------------------------------------------------------------
# Hitap kelimesi ayıklama (CLAUDE.md > "Kişi bilgi sorgusu + hitap
# kelimeleri"): "esma abla", "ahmet usta" gibi hitaplar gerçek isim değil,
# sondaysa ayıklanır.

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("esma abla", "esma"),
        ("ahmet usta", "ahmet"),
        ("mehmet bey", "mehmet"),
        ("ayşe hanım", "ayşe"),
        ("ali amca", "ali"),
        ("zeynep teyze", "zeynep"),
        ("hasan hoca", "hasan"),
        ("veli kardeş", "veli"),
        ("fatma bacı", "fatma"),
        ("ahmet ağabey", "ahmet"),
        ("mehmet abi", "mehmet"),
        ("kemal efendi", "kemal"),
        ("hasan dayı", "hasan"),
    ],
)
def test_strip_honorific_bilinen_hitaplar_ayiklanir(raw, expected):
    assert strip_honorific(raw) == expected
    # Tam zincir (strip_honorific + _strip_word) de aynı sonucu vermeli —
    # tamponsuz yönelme eki bug'ı düzeltildiğinden ("esma" artık "esm"e
    # kesilmiyor), sesli harfle biten isimler de tam zincirde korunur.
    assert strip_turkish_suffix(raw) == expected


def test_strip_honorific_bilinmeyen_soyada_dokunmaz():
    # "şeker" bilinen hitap listesinde değil, gerçek bir soyad olarak
    # korunmalı.
    assert strip_honorific("esma şeker") == "esma şeker"
    assert strip_turkish_suffix("esma şeker") == "esma şeker"


def test_strip_honorific_tek_kelimeyken_ayiklanmaz():
    # Tek başına bir hitap kelimesi bir isim olabilir ihtimaline karşı,
    # yalnızca en az iki kelime varken (isim + hitap) ayıklama uygulanır.
    assert strip_honorific("abla") == "abla"
    assert strip_honorific("hanım") == "hanım"
    assert strip_turkish_suffix("hanım") == "hanım"


def test_strip_honorific_orta_kelimeye_dokunmaz():
    # Hitap yalnızca SONDAYSA ayıklanır, ortadaki bir kelimeye dokunulmaz.
    assert strip_honorific("abla ahmet") == "abla ahmet"
