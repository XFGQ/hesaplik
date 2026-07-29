from decimal import Decimal
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import Person, RawMessage, Transaction


@pytest_asyncio.fixture(loop_scope="session")
async def patch_session_local(engine, monkeypatch):
    """Bot handler'ları kendi SessionLocal() session'ını açıyor; testte bunun
    test veritabanına (conftest'teki `engine`) bağlanması için monkeypatch'lenir."""
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(bot_main, "SessionLocal", maker)
    return maker


class FakeMessage:
    def __init__(self, chat_id: int = 1, text: str = ""):
        self.chat_id = chat_id
        self.text = text
        self.reply_text = AsyncMock()
        self.reply_document = AsyncMock()


class FakeQuery:
    def __init__(self, message: FakeMessage | None = None):
        self.message = message or FakeMessage()
        self.edit_message_text = AsyncMock()
        self.edit_message_reply_markup = AsyncMock()


class FakeUpdate:
    def __init__(self, message: FakeMessage | None = None):
        self.message = message or FakeMessage()


class FakeContext:
    def __init__(self):
        self.chat_data: dict = {}
        self.bot = AsyncMock()


async def _make_raw(session, text: str, external_id: str) -> RawMessage:
    raw = RawMessage(channel="telegram", external_id=external_id, chat_id="1", payload={"text": text})
    session.add(raw)
    await session.commit()
    return raw


def _pending(raw_id: int, *, kind: str, amount: Decimal, name_raw: str) -> dict:
    return {
        "kind": kind,
        "person_name_raw": name_raw,
        "qty": None,
        "unit": None,
        "product_name": None,
        "amount": amount,
        "raw_message_id": raw_id,
        "raw_text": "test mesaj",
    }


async def test_yeni_kisi_akisi_tum_alanlar_dolu_kaydedilir(session, patch_session_local):
    raw = await _make_raw(session, "mehmet oz 500 tl tahsilat", "np-1")
    pending = _pending(raw.id, kind="payment", amount=Decimal("500"), name_raw="mehmet oz")

    context = FakeContext()
    query = FakeQuery()
    context.chat_data["pending"] = pending

    await bot_main._begin_new_person_flow(query, context)
    flow = context.chat_data["new_person_flow"]
    assert flow["step"] == "name"
    assert flow["name"] == "Mehmet Oz"

    await bot_main._new_person_confirm_name(query, context)
    assert flow["step"] == "phone"

    update = FakeUpdate()
    await bot_main._handle_new_person_text(update, context, flow, "05551112233")
    assert flow["step"] == "city"

    await bot_main._handle_new_person_text(update, context, flow, "İzmir")
    assert flow["step"] == "district"

    await bot_main._handle_new_person_text(update, context, flow, "Bergama")

    assert "new_person_flow" not in context.chat_data
    update.message.reply_text.assert_awaited()

    person = (
        await session.execute(select(Person).where(Person.full_name == "Mehmet Oz"))
    ).scalar_one()
    assert person.phone == "05551112233"
    assert person.city == "İzmir"
    assert person.district == "Bergama"

    tx_count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert tx_count == 1


async def test_yeni_kisi_akisi_hepsini_gec_sadece_isimle_kaydeder(session, patch_session_local):
    raw = await _make_raw(session, "ayse kaya 1000 tl borc", "np-2")
    pending = _pending(raw.id, kind="debt", amount=Decimal("1000"), name_raw="ayse kaya")

    context = FakeContext()
    query = FakeQuery()
    context.chat_data["pending"] = pending

    await bot_main._begin_new_person_flow(query, context)
    await bot_main._new_person_skip_all(query, context)

    assert "new_person_flow" not in context.chat_data
    query.edit_message_reply_markup.assert_awaited()
    query.message.reply_text.assert_awaited()

    person = (
        await session.execute(select(Person).where(Person.full_name == "Ayse Kaya"))
    ).scalar_one()
    assert person.phone is None
    assert person.city is None
    assert person.district is None

    tx_count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert tx_count == 1


async def test_yeni_kisi_akisi_iptal_kisi_olusturmaz(session, patch_session_local):
    raw = await _make_raw(session, "veli 200 tl borc", "np-3")
    pending = _pending(raw.id, kind="debt", amount=Decimal("200"), name_raw="veli")

    context = FakeContext()
    query = FakeQuery()
    context.chat_data["pending"] = pending

    await bot_main._begin_new_person_flow(query, context)
    await bot_main._new_person_cancel(query, context)

    assert "new_person_flow" not in context.chat_data
    query.edit_message_text.assert_awaited_with("Kişi ekleme iptal edildi.")

    count = (await session.execute(select(func.count(Person.id)))).scalar_one()
    assert count == 0


async def test_yeni_kisi_akisi_duzelt_ismi_degistirir_ve_gecleri_atlar(session, patch_session_local):
    raw = await _make_raw(session, "mehmet 300 tl borc", "np-4")
    pending = _pending(raw.id, kind="debt", amount=Decimal("300"), name_raw="mehmet")

    context = FakeContext()
    query = FakeQuery()
    context.chat_data["pending"] = pending

    await bot_main._begin_new_person_flow(query, context)
    flow = context.chat_data["new_person_flow"]

    await bot_main._new_person_edit_name(query, context)
    assert flow["step"] == "name"

    update = FakeUpdate()
    await bot_main._handle_new_person_text(update, context, flow, "mehmet yilmaz")
    assert flow["name"] == "Mehmet Yilmaz"
    assert flow["step"] == "phone"

    await bot_main._new_person_skip_field(query, context)  # telefon geç -> il
    await bot_main._new_person_skip_field(query, context)  # il geç -> ilçe
    await bot_main._new_person_skip_field(query, context)  # ilçe geç -> tamamla

    assert "new_person_flow" not in context.chat_data
    person = (
        await session.execute(select(Person).where(Person.full_name == "Mehmet Yilmaz"))
    ).scalar_one()
    assert person.phone is None
    assert person.city is None
    assert person.district is None


# --------------------------------------------------------------- create_person (CLAUDE.md >
# "Bot kayıt akışı" Grup 2, madde 4/5): SADECE kişi ekleme, borç YOK.


async def test_create_person_akisi_borc_olusturmaz_ve_eklendi_mesaji(session, patch_session_local):
    raw = await _make_raw(session, "ahmet duman kayıt et", "np-5")
    pending = _pending(raw.id, kind="create_person", amount=None, name_raw="ahmet duman")

    context = FakeContext()
    query = FakeQuery()
    context.chat_data["pending"] = pending

    await bot_main._begin_new_person_flow(query, context)
    await bot_main._new_person_skip_all(query, context)

    assert "new_person_flow" not in context.chat_data
    count = (
        await session.execute(select(func.count(Person.id)).where(Person.full_name == "Ahmet Duman"))
    ).scalar_one()
    assert count == 1

    tx_count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert tx_count == 0

    query.message.reply_text.assert_awaited_with("✅ Ahmet Duman eklendi.")
