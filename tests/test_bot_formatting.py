from datetime import datetime, timezone
from decimal import Decimal

from app.bot.main import (
    _fmt_try,
    _format_balance,
    _format_list_messages,
    _format_person_card,
    _format_search_messages,
)
from app.models import Person, TxKind
from app.services.ledger import Balance
from app.services.queries import PersonBalanceRow, PersonTransactionRow


def _row(name: str, balance: str, items=None, district=None) -> PersonBalanceRow:
    return PersonBalanceRow(
        person=Person(full_name=name, district=district),
        balance_try=Decimal(balance),
        items=items or [],
    )


def _tx(kind: TxKind, amount: str, lines=None, day: int = 24) -> PersonTransactionRow:
    return PersonTransactionRow(
        id=1,
        occurred_at=datetime(2026, 7, day, 10, 0, tzinfo=timezone.utc),
        kind=kind,
        amount_try=Decimal(amount),
        lines=lines or [],
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


# --------------------------------------------------------------- bakiye tablosu (CLAUDE.md >
# "Bot sorgu anlama" Grup 1, madde 2: düz metin değil hizalı tablo)


def test_format_balance_pre_ile_sarili():
    person = Person(full_name="Furkan Duman")
    bal = Balance(person_id=1, balance_try=Decimal("10000.00"))
    text = _format_balance(person, bal, [], 0)

    assert text.startswith("📋 <pre>")
    assert text.endswith("</pre>")


def test_format_balance_hareketler_ve_bakiye():
    person = Person(full_name="Furkan Duman")
    bal = Balance(person_id=1, balance_try=Decimal("10000.00"))
    txs = [
        _tx(TxKind.DEBIT, "15000.00", lines=[("Saman", Decimal("20"), "balya")], day=24),
        _tx(TxKind.CREDIT, "5000.00", day=25),
    ]

    text = _format_balance(person, bal, txs, 2)

    assert "Furkan Duman" in text
    assert "24 Tem" in text
    assert "Saman 20 balya" in text
    assert "+15.000,00" in text
    assert "25 Tem" in text
    assert "Tahsilat" in text
    assert "−5.000,00" in text
    assert "Güncel bakiye: 10.000,00 TL borçlu" in text


def test_format_balance_alacakli_durum():
    person = Person(full_name="Ayşe Kaya")
    bal = Balance(person_id=1, balance_try=Decimal("-200.00"))

    text = _format_balance(person, bal, [], 0)

    assert "Güncel bakiye: 200,00 TL alacaklı" in text


def test_format_balance_sifir_durum():
    person = Person(full_name="Sıfır Kişi")
    bal = Balance(person_id=1, balance_try=Decimal("0.00"))

    text = _format_balance(person, bal, [], 0)

    assert "Güncel bakiye: hesabı sıfır" in text


def test_format_balance_hareket_yok():
    person = Person(full_name="Yeni Kişi")
    bal = Balance(person_id=1, balance_try=Decimal("0.00"))

    text = _format_balance(person, bal, [], 0)

    assert "Hareket yok." in text


def test_format_balance_uzun_liste_ozetlenir():
    person = Person(full_name="Çok Hareketli Kişi")
    bal = Balance(person_id=1, balance_try=Decimal("500.00"))
    txs = [_tx(TxKind.DEBIT, "100.00", day=24)] * 15

    text = _format_balance(person, bal, txs, 20)

    assert "...ve 5 kayıt daha" in text


def test_format_balance_html_ozel_karakterler_kacirilir():
    person = Person(full_name="<Ahmet> & Oğulları")
    bal = Balance(person_id=1, balance_try=Decimal("0.00"))

    text = _format_balance(person, bal, [], 0)

    assert "<Ahmet>" not in text
    assert "&lt;Ahmet&gt;" in text


# --------------------------------------------------------------- arama (CLAUDE.md > "Bot
# sorgu anlama" Grup 1, madde 5: tek kelime = arama)


def test_format_search_messages_bos_sonuc():
    msgs = _format_search_messages("zzzyok", [])
    assert msgs == ["'zzzyok' ile eşleşen kişi yok."]


def test_format_search_messages_eslesenler():
    rows = [_row("Ahmet Yılmaz", "1500.00"), _row("Ahmet Kaya", "-200.00")]
    msgs = _format_search_messages("ahmet", rows)

    assert "🔍 'ahmet' (2 kişi)" in msgs[0]
    assert "Ahmet Yılmaz — 1.500,00 TL borçlu" in msgs[0]
    assert "Ahmet Kaya — 200,00 TL alacaklı" in msgs[0]
