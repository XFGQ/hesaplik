from decimal import Decimal

from app.bot.main import _fmt_try, _format_list_messages, _format_person_card
from app.models import Person
from app.services.queries import PersonBalanceRow


def _row(name: str, balance: str, items=None, district=None) -> PersonBalanceRow:
    return PersonBalanceRow(
        person=Person(full_name=name, district=district),
        balance_try=Decimal(balance),
        items=items or [],
    )


def test_fmt_try_binlik_ve_ondalik():
    assert _fmt_try(Decimal("1500.00")) == "1.500,00"
    assert _fmt_try(Decimal("1500000.5")) == "1.500.000,50"
    assert _fmt_try(Decimal("0")) == "0,00"


def test_fmt_try_negatif():
    assert _fmt_try(Decimal("-200.00")) == "-200,00"


def test_format_list_messages_bos_liste_borclu():
    msgs = _format_list_messages("list_debtors", None, [])
    assert msgs == ["Şu anda borçlu kimse yok."]


def test_format_list_messages_bos_ilce():
    msgs = _format_list_messages("list_district", "bergama", [])
    assert msgs == ["Bergama'da kayıtlı kimse yok."]


def test_format_list_messages_baslik_ve_satirlar():
    rows = [_row("Ahmet Yılmaz", "1500.00"), _row("Ayşe Kaya", "-200.00")]
    msgs = _format_list_messages("list_all", None, rows)

    assert len(msgs) == 1
    assert "📋 Kişiler (2 kişi)" in msgs[0]
    assert "Ahmet Yılmaz — 1.500,00 TL borçlu" in msgs[0]
    assert "Ayşe Kaya — 200,00 TL alacaklı" in msgs[0]


def test_format_list_messages_acik_kalem_alt_satirda():
    rows = [_row("Ahmet Yılmaz", "1000.00", items=[("Saman", Decimal("20"), "balya")])]
    msgs = _format_list_messages("list_debtors", None, rows)

    assert "  20 balya Saman" in msgs[0]


def test_format_list_messages_4096_karakter_sinirinda_bolunur():
    rows = [_row(f"Kişi {i}", "100.00") for i in range(200)]
    msgs = _format_list_messages("list_all", None, rows)

    assert len(msgs) > 1
    for m in msgs:
        assert len(m) <= 4096


# --------------------------------------------------------------- kişi bilgileri kartı


def test_format_person_card_bos_alanlar_gosterilmez():
    person = Person(full_name="Esma Kaya")
    text = _format_person_card(person)

    assert text == "Esma Kaya\nKayıtlı iletişim/konum bilgisi yok."


def test_format_person_card_dolu_alanlar_gosterilir():
    person = Person(full_name="Esma Kaya", phone="0555 111 22 33", city="İzmir", district="Bergama")
    text = _format_person_card(person)

    assert text == "Esma Kaya\nTelefon: 0555 111 22 33\nİl: İzmir\nİlçe: Bergama"


def test_format_person_card_kismi_alanlar():
    person = Person(full_name="Ahmet Yılmaz", phone="0555 000 00 00")
    text = _format_person_card(person)

    assert text == "Ahmet Yılmaz\nTelefon: 0555 000 00 00"
