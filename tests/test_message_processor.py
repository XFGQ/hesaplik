from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select

from app.models import Person, RawMessage, Transaction, TxKind, TxSource
from app.services import message_processor
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent
from app.services.message_processor import ProcessOutcome


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    p = Person(full_name="Ahmet Yılmaz")
    session.add(p)
    await session.flush()
    return p


async def _make_raw(session, text: str, update_id: int) -> RawMessage:
    raw = RawMessage(
        channel="telegram", external_id=str(update_id), chat_id="1", payload={"text": text}
    )
    session.add(raw)
    await session.flush()
    return raw


async def test_borc_urun_ve_tutarla_kaydedilir(session, ahmet):
    text = "ahmet yılmaz 20 balya saman aldı 15000 tl borç"
    raw = await _make_raw(session, text, 1)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    assert result.resolved.person.id == ahmet.id
    assert result.balance.balance_try == Decimal("15000.00")

    tx = await session.get(Transaction, result.transaction_id)
    assert tx.kind == TxKind.DEBIT
    assert tx.amount_try == Decimal("15000.00")
    assert tx.source == TxSource.TELEGRAM_TEXT
    assert tx.raw_text == text
    assert len(tx.lines) == 1
    assert tx.lines[0].qty == Decimal("20")
    # Kullanıcının yazdığı tutar esas: birim fiyat tutardan türetilir.
    assert tx.lines[0].unit_price == Decimal("750.00")

    await session.refresh(raw)
    assert raw.processed_at is not None
    assert raw.transaction_id == tx.id


async def test_nakit_borc_kalemsiz_kaydedilir(session, ahmet):
    text = "ahmet yılmaz 500 tl borç yazdım"
    raw = await _make_raw(session, text, 2)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.amount_try == Decimal("500.00")
    assert len(tx.lines) == 0


async def test_tahsilat_kaydedilir(session, ahmet):
    text = "ahmet yılmaz 20000 tl ödedi"
    raw = await _make_raw(session, text, 3)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.kind == TxKind.CREDIT
    assert tx.amount_try == Decimal("20000.00")


async def test_bakiye_sorgusu_kayit_olusturmaz(session, ahmet):
    text1 = "ahmet yılmaz 1000 tl borç yazdım"
    raw1 = await _make_raw(session, text1, 4)
    await message_processor.process_raw_message(session, raw1, text1)

    text2 = "ahmet yılmaz borcu ne"
    raw2 = await _make_raw(session, text2, 5)
    result = await message_processor.process_raw_message(session, raw2, text2)

    assert result.outcome == ProcessOutcome.BALANCE
    assert result.balance.balance_try == Decimal("1000.00")

    await session.refresh(raw2)
    assert raw2.processed_at is None
    assert raw2.transaction_id is None


async def test_bakiye_sorgusu_borcunu_soyle_kalibiyle_calisir(session, ahmet):
    # Bug: "borcunu söyle" parser'da tanınmıyordu (UNRECOGNIZED). Şimdi
    # "borcu ne kadar" ile aynı şekilde bakiye döndürmeli.
    text1 = "ahmet yılmaz 1000 tl borç yazdım"
    raw1 = await _make_raw(session, text1, 20)
    await message_processor.process_raw_message(session, raw1, text1)

    text2 = "ahmet yılmaz borcunu söyle"
    raw2 = await _make_raw(session, text2, 21)
    result = await message_processor.process_raw_message(session, raw2, text2)

    assert result.outcome == ProcessOutcome.BALANCE
    assert result.balance.balance_try == Decimal("1000.00")


async def test_bakiye_sorgusu_belirsiz_kisi_onay_ister(session):
    # Kişi eşleştirme güvenliği bakiye sorgusunda da geçerli: iki "furkan"
    # varken otomatik birinin bakiyesi gösterilmemeli, "hangisi?" sorulmalı.
    duman = Person(full_name="Furkan Duman")
    yilmaz = Person(full_name="Furkan Yılmaz")
    session.add_all([duman, yilmaz])
    await session.flush()

    text = "furkan borcunu söyle"
    raw = await _make_raw(session, text, 22)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in result.resolved.person_candidates}
    assert {duman.id, yilmaz.id} == candidate_ids

    # Bot'ta aday seçilince (callback), kayıt değil bakiye gösterimi olmalı —
    # _finish_pending'in yaptığı gibi READY bir ResolvedIntent ile devam.
    picked = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind="balance_query",
        person=duman,
    )
    follow_up = await message_processor.handle_resolved(session, raw, picked, text)
    assert follow_up.outcome == ProcessOutcome.BALANCE
    assert follow_up.resolved.person.id == duman.id


async def test_anlasilmayan_metin_kayit_olusturmaz(session):
    text = "bugün hava çok güzel"
    raw = await _make_raw(session, text, 6)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.UNRECOGNIZED
    await session.refresh(raw)
    assert raw.processed_at is None


async def test_bulunamayan_kisi_kayit_olusturmaz(session):
    text = "hiç yok böyle biri 500 tl borç yazdı"
    raw = await _make_raw(session, text, 7)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PERSON_NOT_FOUND
    await session.refresh(raw)
    assert raw.processed_at is None
    # Ekranda "sormadan direkt kaydetmiş gibi görünüyor" şikayeti: gerçekten
    # hiçbir kişi/kayıt sessizce oluşturulmamış olmalı, bot Evet/Hayır sormalı.
    assert (await session.execute(select(func.count(Person.id)))).scalar_one() == 0
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0


async def test_farkli_soyadli_kisiye_otomatik_baglanmaz(session):
    # Kritik bug (CLAUDE.md 2026-07-25): "furkan yılmaz" yazılınca yalnızca
    # "furkan duman" varken sisteme otomatik ona para yazılmamalı.
    duman = Person(full_name="Furkan Duman")
    session.add(duman)
    await session.flush()

    text = "furkan yılmaz 50 saman aldı 5000 tl borç"
    raw = await _make_raw(session, text, 9)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome in (ProcessOutcome.PERSON_NOT_FOUND, ProcessOutcome.NEEDS_CONFIRMATION)
    assert (await session.execute(select(func.count(Person.id)))).scalar_one() == 1
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0
    await session.refresh(raw)
    assert raw.processed_at is None
    assert raw.transaction_id is None


async def test_sorgu_kisileri_listele(session, ahmet):
    text = "kişileri listele"
    raw = await _make_raw(session, text, 10)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.LIST
    assert [r.person.id for r in result.persons] == [ahmet.id]
    await session.refresh(raw)
    assert raw.processed_at is None
    assert raw.transaction_id is None


async def test_sorgu_borclulari_listele(session, ahmet):
    text1 = "ahmet yılmaz 1000 tl borç yazdım"
    raw1 = await _make_raw(session, text1, 11)
    await message_processor.process_raw_message(session, raw1, text1)

    text2 = "borçluları listele"
    raw2 = await _make_raw(session, text2, 12)
    result = await message_processor.process_raw_message(session, raw2, text2)

    assert result.outcome == ProcessOutcome.LIST
    assert len(result.persons) == 1
    assert result.persons[0].balance_try == Decimal("1000.00")


async def test_sorgu_ilceye_gore_listele(session):
    bergama = Person(full_name="Bergamalı Ahmet", district="Bergama")
    izmir = Person(full_name="İzmirli Mehmet", district="İzmir")
    session.add_all([bergama, izmir])
    await session.flush()

    text = "bergamalıları listele"
    raw = await _make_raw(session, text, 13)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.LIST
    assert result.resolved.district == "bergama"
    assert [r.person.id for r in result.persons] == [bergama.id]


async def test_belirsiz_kisi_kayit_olusturmaz(session):
    a = Person(full_name="Ahmet Yılmaz")
    b = Person(full_name="Ahmet Yıldız")
    session.add_all([a, b])
    await session.flush()

    text = "ahmet yı 100 tl borç yazdı"
    raw = await _make_raw(session, text, 8)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    assert len(result.resolved.person_candidates) >= 2
    await session.refresh(raw)
    assert raw.processed_at is None
