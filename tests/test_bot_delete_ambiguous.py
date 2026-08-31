""""sil" bağlam ayrımı (CLAUDE.md > "'sil' bağlam ayrımı", 2026-08-31).

"furkan duman 20 saman borcunu ödedi sil" cümlesinde asıl niyet TAHSİLAT'tır
— kullanıcı kapanan BORCU silmek ister, kişiyi değil. Bot bunu sessizce kişi
silme sanmaz: kişiyi çözer, HİÇBİR ŞEY yapmadan üç butonla sorar. "Kişiyi
sil" seçilirse normal yazarak-onay akışına girilir (kısayol yok); "Tahsilat
gir" seçilirse silme fiili ayıklanmış metin NORMAL akıştan yeniden geçer.
"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import Person, RawMessage, Transaction, TxKind
from app.services import message_processor
from app.services.message_processor import ProcessOutcome


@pytest_asyncio.fixture(loop_scope="session")
async def patch_session_local(engine, monkeypatch):
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


class FakeContext:
    def __init__(self):
        self.chat_data: dict = {}
        self.bot = AsyncMock()


async def _make_raw(session, text: str, external_id: str) -> RawMessage:
    raw = RawMessage(channel="telegram", external_id=external_id, chat_id="1", payload={"text": text})
    session.add(raw)
    await session.commit()
    return raw


@pytest_asyncio.fixture(loop_scope="session")
async def furkan(session):
    p = Person(full_name="Furkan Duman")
    session.add(p)
    await session.flush()
    await session.commit()
    return p


async def _ask(session, context, text, external_id):
    """Metni işleyip botun cevabını üretir; delete_ambiguous durumunda
    chat_data'yı doldurur."""
    raw = await _make_raw(session, text, external_id)
    result = await message_processor.process_raw_message(session, raw, text)
    await session.commit()
    message = FakeMessage()
    await bot_main._reply_outcome(session, context, result, raw, text, message)
    return result, message


async def test_borcunu_odedi_sil_sorar_arsivlemez(session, patch_session_local, furkan):
    context = FakeContext()
    result, message = await _ask(
        session, context, "furkan duman 20 saman borcunu ödedi sil", "delamb-1"
    )

    assert result.outcome == ProcessOutcome.DELETE_AMBIGUOUS
    reply, kwargs = message.reply_text.await_args
    assert "Furkan Duman" in reply[0]
    assert [
        b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row
    ] == ["delete:payment", "delete:person", "delete:cancel"]

    # Hiçbir şey yapılmadı.
    await session.refresh(furkan)
    assert furkan.is_active is True
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0

    # "sil" ayıklanmış metin saklandı — "Tahsilat gir" bunu yeniden işleyecek.
    assert context.chat_data["delete_ambiguous"]["text"] == (
        "furkan duman 20 saman borcunu ödedi"
    )


async def test_kisiyi_sil_secilince_yazarak_onay_istenir(session, patch_session_local, furkan):
    context = FakeContext()
    await _ask(session, context, "furkan duman 20 saman borcunu ödedi sil", "delamb-2")

    query = FakeQuery()
    await bot_main._handle_delete_ambiguous_person(query, context)

    metin = query.edit_message_text.await_args[0][0]
    assert "silinecek" in metin
    assert context.chat_data["archive_confirm"]["person_id"] == furkan.id

    # Kısayol yok: onay kelimesi yazılmadan hiçbir şey silinmez.
    await session.refresh(furkan)
    assert furkan.is_active is True


async def test_tahsilat_gir_secilince_kayit_olusur(session, patch_session_local, furkan):
    context = FakeContext()
    await _ask(session, context, "furkan duman 5000 tl ödedi sil", "delamb-3")

    query = FakeQuery()
    await bot_main._handle_delete_ambiguous_payment(query, context)

    tx = (await session.execute(select(Transaction))).scalars().one()
    assert tx.kind == TxKind.CREDIT
    assert tx.amount_try == Decimal("5000.00")
    await session.refresh(furkan)
    assert furkan.is_active is True


async def test_iptal_hicbir_sey_yapmaz(session, patch_session_local, furkan):
    context = FakeContext()
    await _ask(session, context, "furkan duman 5000 tl ödedi sil", "delamb-4")
    context.chat_data.pop("delete_ambiguous", None)

    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0
    await session.refresh(furkan)
    assert furkan.is_active is True


async def test_net_kisi_silme_hala_dogrudan_arsiv_onayina_gider(session, patch_session_local, furkan):
    context = FakeContext()
    result, _ = await _ask(session, context, "furkan duman sil", "delamb-5")

    assert result.outcome == ProcessOutcome.ARCHIVE_CONFIRM
    assert "archive_confirm" in context.chat_data
    assert "delete_ambiguous" not in context.chat_data


async def test_süresi_gecmis_istek_nazikce_reddedilir(session, patch_session_local):
    context = FakeContext()
    query = FakeQuery()
    await bot_main._handle_delete_ambiguous_person(query, context)
    query.edit_message_text.assert_awaited_with("Bu istek artık geçerli değil.")
