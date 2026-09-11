"""Telegram botunda varsayılan saman fiyatı (CLAUDE.md > "Varsayılan saman fiyatı").

Net cümle doğrudan kaydedilir ve onay mesajı kullanılan fiyatı gösterir;
ürün ya da yön varsayıldıysa önce sorulur (Evet/Düzelt/İptal — LLM
önizlemesiyle aynı makine). Web ikizi: tests/test_chat_api.py >
"varsayılan saman fiyatı". Hesap: tests/test_saman_fiyat.py.
"""

from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import Person, Product, Transaction
from test_bot_running_format import (
    FakeContext,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    _reply_texts,
    _tx_count,
)


@pytest_asyncio.fixture(loop_scope="session")
async def patch_session_local(engine, monkeypatch):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(bot_main, "SessionLocal", maker)
    return maker


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet_ve_saman(session):
    ahmet = Person(full_name="Ahmet Yılmaz")
    session.add_all([ahmet, Product(name="Saman", base_unit="balya")])
    await session.flush()
    await session.commit()
    return ahmet


async def test_net_saman_dogrudan_kaydedilir_fiyat_gosterilir(
    session, patch_session_local, ahmet_ve_saman
):
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 20 saman aldı"))
    await bot_main.on_text(update, context)

    cevap = _reply_texts(update)[-1]
    assert "20 balya Saman · 3.600,00 TL borç eklendi" in cevap
    assert "Birim fiyat: 180,00 TL/balya (varsayılan saman fiyatı)" in cevap
    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet_ve_saman.id))
    ).scalar_one()
    assert tx.amount_try == Decimal("3600.00")


async def test_urunsuz_sayi_sorulur_evetle_kaydedilir(session, patch_session_local, ahmet_ve_saman):
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 20"))
    await bot_main.on_text(update, context)

    soru = _reply_texts(update)[-1]
    assert soru == (
        "Ahmet Yılmaz'a 20 balya Saman borç mu demek istediniz?\n"
        "(180,00 TL/balya = 3.600,00 TL)"
    )
    assert "llm_confirm" in context.chat_data
    assert await _tx_count(session) == 0

    query = FakeQuery(data="llm:yes")
    await bot_main.on_callback(FakeUpdate(callback_query=query), context)

    onay = query.edit_message_text.await_args_list[-1].args[0]
    assert "varsayılan saman fiyatı" in onay
    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet_ve_saman.id))
    ).scalar_one()
    assert tx.amount_try == Decimal("3600.00")


async def test_fiilsiz_saman_duzelt_hicbir_sey_kaydetmez(session, patch_session_local, ahmet_ve_saman):
    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="ahmet yılmaz 20 saman"))
    await bot_main.on_text(update, context)
    assert "borç ekleyeyim mi?" in _reply_texts(update)[-1]

    query = FakeQuery(data="llm:fix")
    await bot_main.on_callback(FakeUpdate(callback_query=query), context)

    assert "llm_confirm" not in context.chat_data
    assert await _tx_count(session) == 0


async def test_coklu_kisi_secilince_saman_teyidi_yine_sorulur(session, patch_session_local):
    duman = Person(full_name="Furkan Duman")
    yildiz = Person(full_name="Furkan Yıldız")
    session.add_all([duman, yildiz, Product(name="Saman", base_unit="balya")])
    await session.flush()
    await session.commit()

    context = FakeContext()
    update = FakeUpdate(FakeMessage(text="furkan 20"))
    await bot_main.on_text(update, context)
    assert "Hangisini demek istedin?" in _reply_texts(update)[-1]

    query = FakeQuery(data=f"person:pick:{duman.id}")
    await bot_main.on_callback(FakeUpdate(callback_query=query), context)
    assert "borç mu demek istediniz?" in query.edit_message_text.await_args_list[-1].args[0]
    assert await _tx_count(session) == 0

    evet = FakeQuery(data="llm:yes")
    await bot_main.on_callback(FakeUpdate(callback_query=evet), context)
    tx = (await session.execute(select(Transaction).where(Transaction.person_id == duman.id))).scalar_one()
    assert tx.amount_try == Decimal("3600.00")
