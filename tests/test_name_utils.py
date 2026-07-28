import pytest

from app.services.name_utils import strip_turkish_suffix


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
