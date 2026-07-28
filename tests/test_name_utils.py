import pytest

from app.services.name_utils import strip_context_words, strip_turkish_suffix


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
        ("ahmete", "ahmet"),
        ("mehmedin", "mehmet"),
        ("dumana", "duman"),
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
    assert strip_turkish_suffix("furkan dumana") == "furkan duman"


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
