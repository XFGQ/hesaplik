"""türkçe sayı çözücü (parse_turkish_number) testleri.

CLAUDE.md > "LLM son çare, regex birincil": "3bin" gibi bitişik yazılmış
Türkçe sayılar LLM'e gitmeden, koddan ANINDA ve DOĞRU çözülmeli. En kritik
regresyon: "3bin" ASLA 3.000.000 olmamalı (bkz. test_bitisik_bin_carpimi).
"""

from decimal import Decimal

from app.services.parser import parse_turkish_number


def test_duz_rakam():
    assert parse_turkish_number("5000") == Decimal("5000")
    assert parse_turkish_number("15000") == Decimal("15000")


def test_bitisik_bin_ayrik_bin():
    assert parse_turkish_number("3bin") == Decimal("3000")
    assert parse_turkish_number("3 bin") == Decimal("3000")


def test_bitisik_bin_carpimi():
    # Kritik regresyon: "3bin" 3 milyon DEĞİL 3000 olmalı.
    assert parse_turkish_number("5bin") == Decimal("5000")
    assert parse_turkish_number("10bin") == Decimal("10000")
    assert parse_turkish_number("25bin") == Decimal("25000")


def test_yuz_katlari():
    assert parse_turkish_number("yüz") == Decimal("100")
    assert parse_turkish_number("ikiyüz") == Decimal("200")
    assert parse_turkish_number("beşyüz") == Decimal("500")


def test_bin_beryuz_bilesik():
    assert parse_turkish_number("bin beşyüz") == Decimal("1500")
    assert parse_turkish_number("binbeşyüz") == Decimal("1500")


def test_bucuk():
    assert parse_turkish_number("3buçuk") == Decimal("3.5")
    assert parse_turkish_number("2buçuk") == Decimal("2.5")


def test_milyon():
    assert parse_turkish_number("birmilyon") == Decimal("1000000")
    assert parse_turkish_number("2 milyon") == Decimal("2000000")


def test_turkce_sayi_kelimesi_zinciri():
    assert parse_turkish_number("on beş bin") == Decimal("15000")
    assert parse_turkish_number("yirmi") == Decimal("20")


def test_bos_ve_anlamsiz_metin_none_doner():
    assert parse_turkish_number("") is None
    assert parse_turkish_number("saman") is None


def test_fazladan_kelime_varsa_none_doner():
    # parse_turkish_number tek bir sayı ifadesi bekler, cümle değil.
    assert parse_turkish_number("3bin tl") is None
