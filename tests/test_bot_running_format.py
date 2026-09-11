"""Telegram botunda koşan format (CLAUDE.md > "Koşan format").

"ahmet 70-20-50 saman" = 70 vardı, 20 değişti, 50 oldu -> 20 balya saman
BORÇ. Üçlü YALNIZCA mal adedini söyler; TL ayrı girilir, uydurulmaz — tek
istisna saman: tutar varsayılan saman fiyatından gelir (CLAUDE.md >
"Varsayılan saman fiyatı"). Tutar sorusu bu yüzden arpa ile test edilir.

Web ikizinin aynı senaryoları: tests/test_chat_api.py > "koşan format".
Ayrıştırmanın kendisi tests/test_parser.py'de test edilir; burada botun
SORDUĞU sorular ve HİÇBİR ŞEYİ kaydetmediği anlar doğrulanır.
"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import Person, Product, Transaction


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
    def __init__(self, message: FakeMessage | None = None, data: str = ""):
        self.message = message or FakeMessage()
        self.data = data
        self.edit_message_text = AsyncMock()
        self.edit_message_reply_markup = AsyncMock()
        self.answer = AsyncMock()


class _FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeUpdate:
    def __init__(self, message: FakeMessage | None = None, callback_query: FakeQuery | None = None):
        self.message = message or FakeMessage()
        self.callback_query = callback_query
        self.effective_chat = _FakeChat(self.message.chat_id)

    def to_dict(self):
        return {
            "update_id": id(self),
            "message": {"text": self.message.text, "chat": {"id": self.message.chat_id}},
        }


class FakeContext:
    def __init__(self):
        self.chat_data: dict = {}
        self.bot = AsyncMock()


def _reply_texts(update: FakeUpdate) -> list[str]:
    return [call.args[0] for call in update.message.reply_text.await_args_list]


async def _tx_count(session) -> int:
    return (await session.execute(select(func.count(Transaction.id)))).scalar_one()


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet_ve_saman(session):
    ahmet = Person(full_name="Ahmet Yılmaz")
    saman = Product(name="Saman", base_unit="balya")
    session.add_all([ahmet, saman])
    await session.flush()
    await session.commit()
    return ahmet


# --------------------------------------------------------------- tutar sorusu


async def test_tutar_soylenmemisse_sorar_ve_cevaptan_sonra_kaydeder(
    session, patch_session_local, ahmet_ve_saman
):
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 70-20-50 arpa"))
    await bot_main.on_text(update, context)

    soru = _reply_texts(update)[-1]
    assert "70 → 50" in soru
    assert "Tutar kaç TL?" in soru
    assert "running_amount" in context.chat_data
    assert await _tx_count(session) == 0  # tutar bilinmeden kayıt YOK

    cevap = FakeUpdate(FakeMessage(text="5000"))
    await bot_main.on_text(cevap, context)

    assert "running_amount" not in context.chat_data
    assert await _tx_count(session) == 1
    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet_ve_saman.id))
    ).scalar_one()
    assert tx.amount_try == Decimal("5000.00")
    assert tx.lines[0].qty == Decimal("20.000")  # kaydedilen sayı FARK


async def test_tutar_ayni_cumlede_verilirse_dogrudan_kaydeder(
    session, patch_session_local, ahmet_ve_saman
):
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 70-20-50 saman 5000 tl"))
    await bot_main.on_text(update, context)

    assert "running_amount" not in context.chat_data
    assert await _tx_count(session) == 1
    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet_ve_saman.id))
    ).scalar_one()
    assert tx.lines[0].qty == Decimal("20.000")


async def test_artis_tahsilat_yazar(session, patch_session_local, ahmet_ve_saman):
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 70-30-100 saman 3000 tl"))
    await bot_main.on_text(update, context)

    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet_ve_saman.id))
    ).scalar_one()
    assert tx.kind.name == "CREDIT"
    assert tx.lines[0].qty == Decimal("30.000")


async def test_anlasilmayan_tutar_kaydetmez_tekrar_sorar(
    session, patch_session_local, ahmet_ve_saman
):
    context = FakeContext()
    await bot_main.on_text(FakeUpdate(FakeMessage(text="ahmet yılmaz 70-20-50 arpa")), context)

    hatali = FakeUpdate(FakeMessage(text="bilmiyorum"))
    await bot_main.on_text(hatali, context)
    assert "anlayamadım" in _reply_texts(hatali)[-1]
    assert "running_amount" in context.chat_data  # soru hâlâ açık
    assert await _tx_count(session) == 0

    await bot_main.on_text(FakeUpdate(FakeMessage(text="5 bin")), context)
    assert await _tx_count(session) == 1


# --------------------------------------------------------------- matematik kontrolü


async def test_matematik_tutmuyorsa_kaydetmez_sorar(session, patch_session_local, ahmet_ve_saman):
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 70-25-50 saman 5000 tl"))
    await bot_main.on_text(update, context)

    uyari = _reply_texts(update)[-1]
    assert "fark 20 olmalı ama 25" in uyari
    assert "Kayıt yapmadım." in uyari
    assert "running_fix" in context.chat_data
    assert await _tx_count(session) == 0


async def test_fark_duzeltilince_normal_akistan_gecip_kaydedilir(
    session, patch_session_local, ahmet_ve_saman
):
    context = FakeContext()
    await bot_main.on_text(FakeUpdate(FakeMessage(text="ahmet yılmaz 70-25-50 saman 5000 tl")), context)

    query = FakeQuery(data="running:fix")
    await bot_main.on_callback(FakeUpdate(callback_query=query), context)

    assert "running_fix" not in context.chat_data
    assert await _tx_count(session) == 1
    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet_ve_saman.id))
    ).scalar_one()
    assert tx.lines[0].qty == Decimal("20.000")
    assert tx.amount_try == Decimal("5000.00")


async def test_matematik_uyarisi_iptal_edilince_hicbir_sey_kaydedilmez(
    session, patch_session_local, ahmet_ve_saman
):
    context = FakeContext()
    await bot_main.on_text(FakeUpdate(FakeMessage(text="ahmet yılmaz 70-25-50 saman 5000 tl")), context)

    query = FakeQuery(data="running:cancel")
    await bot_main.on_callback(FakeUpdate(callback_query=query), context)

    assert "running_fix" not in context.chat_data
    assert await _tx_count(session) == 0


# --------------------------------------------------------------- kişi güvenliği


async def test_coklu_kisi_once_hangisi_sonra_tutar_sorar(session, patch_session_local):
    duman = Person(full_name="Furkan Duman")
    yildiz = Person(full_name="Furkan Yıldız")
    session.add_all([duman, yildiz, Product(name="Saman", base_unit="balya")])
    await session.flush()
    await session.commit()

    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="furkan 70-20-50 arpa"))
    await bot_main.on_text(update, context)
    assert "Hangisini demek istedin?" in _reply_texts(update)[-1]

    query = FakeQuery(data=f"person:pick:{duman.id}")
    await bot_main.on_callback(FakeUpdate(callback_query=query), context)
    assert "Tutar kaç TL?" in query.edit_message_text.await_args_list[-1].args[0]
    assert await _tx_count(session) == 0

    await bot_main.on_text(FakeUpdate(FakeMessage(text="4000")), context)
    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == duman.id))
    ).scalar_one()
    assert tx.lines[0].qty == Decimal("20.000")
    assert tx.amount_try == Decimal("4000.00")


async def test_tarih_kosan_format_sanilmaz(session, patch_session_local, ahmet_ve_saman):
    """"12-05-2026" bir üçlüye benziyor ama tarihtir: ne kayıt ne uyarı."""
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 12-05-2026 saman"))
    await bot_main.on_text(update, context)

    assert "running_fix" not in context.chat_data
    assert "running_amount" not in context.chat_data
    assert await _tx_count(session) == 0


async def test_samanda_tutar_sorulmaz_varsayilan_fiyattan_kaydeder(
    session, patch_session_local, ahmet_ve_saman
):
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 70-20-50 saman"))
    await bot_main.on_text(update, context)

    assert "running_amount" not in context.chat_data
    assert "varsayılan saman fiyatı" in _reply_texts(update)[-1]
    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet_ve_saman.id))
    ).scalar_one()
    assert tx.lines[0].qty == Decimal("20.000")
    assert tx.amount_try == Decimal("3600.00")
