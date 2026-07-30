"""Bot ürün yazım düzeltme akışı (CLAUDE.md > "Ürün yazım düzeltme
(fuzzy)", Grup 5). "samaan" gibi hatalı ürün adları sessizce yeni ürün
olarak açılmaz: kişi netleştikten sonra ürün bulanıksa bot "'samaan' →
'saman' mı? [Evet] [Hayır, yeni ürün] [İptal]" sorar. Evet -> mevcut ürüne
bağlanır (yeni ürün AÇILMAZ). Hayır -> kullanıcının yazdığı ham adla
gerçekten yeni ürün açılır. İptal -> hiçbir şey kaydedilmez."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import Person, Product, RawMessage, Transaction


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


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    p = Person(full_name="Ahmet Yılmaz")
    session.add(p)
    await session.flush()
    await session.commit()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def saman(session):
    p = Product(name="Saman", base_unit="balya")
    session.add(p)
    await session.flush()
    await session.commit()
    return p


async def _make_raw(session, text: str, external_id: str) -> RawMessage:
    raw = RawMessage(channel="telegram", external_id=external_id, chat_id="1", payload={"text": text})
    session.add(raw)
    await session.commit()
    return raw


def _pending(raw_id: int, person_id: int, suggestion_id: int) -> dict:
    return {
        "kind": "debt",
        "person_id": person_id,
        "qty": Decimal("20"),
        "unit": "balya",
        "product_name_raw": "samaan",
        "suggestion_id": suggestion_id,
        "amount": Decimal("5000"),
        "raw_message_id": raw_id,
        "raw_text": "ahmet yılmaz 20 balya samaan aldı 5000 tl borç",
    }


# --------------------------------------------------------------- on_text ile uçtan uca tetikleme


async def test_on_text_urun_yazim_hatasinda_oneri_gosterir(session, patch_session_local, ahmet, saman):
    context = FakeContext()
    text = "ahmet yılmaz 20 balya samaan aldı 5000 tl borç"
    update = FakeUpdate(FakeMessage(text=text))

    await bot_main.on_text(update, context)

    assert "product_confirm" in context.chat_data
    pending = context.chat_data["product_confirm"]
    assert pending["person_id"] == ahmet.id
    assert pending["suggestion_id"] == saman.id
    assert pending["product_name_raw"] == "samaan"

    update.message.reply_text.assert_awaited_with(
        "'samaan' → 'Saman' mı?", reply_markup=bot_main._product_suggestion_keyboard()
    )

    count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert count == 0


async def test_on_text_tam_eslesirse_sormadan_kaydeder(session, patch_session_local, ahmet, saman):
    context = FakeContext()
    text = "ahmet yılmaz 20 balya saman aldı 5000 tl borç"
    update = FakeUpdate(FakeMessage(text=text))

    await bot_main.on_text(update, context)

    assert "product_confirm" not in context.chat_data
    update.message.reply_text.assert_awaited()
    count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert count == 1


# --------------------------------------------------------------- Evet / Hayır / İptal


async def test_evet_ile_oneriye_baglanir_yeni_urun_acilmaz(session, patch_session_local, ahmet, saman):
    raw = await _make_raw(session, "ahmet yılmaz 20 balya samaan aldı 5000 tl borç", "prod-1")
    context = FakeContext()
    context.chat_data["product_confirm"] = _pending(raw.id, ahmet.id, saman.id)
    query = FakeQuery(data="product:yes")
    update = FakeUpdate(callback_query=query)

    await bot_main.on_callback(update, context)

    assert "product_confirm" not in context.chat_data
    query.edit_message_text.assert_awaited()

    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))
    ).scalar_one()
    assert tx.lines[0].product_id == saman.id

    urun_sayisi = (await session.execute(select(func.count(Product.id)))).scalar_one()
    assert urun_sayisi == 1  # yeni ürün açılmadı


async def test_hayir_ile_ham_adla_yeni_urun_acilir(session, patch_session_local, ahmet, saman):
    raw = await _make_raw(session, "ahmet yılmaz 20 balya samaan aldı 5000 tl borç", "prod-2")
    context = FakeContext()
    context.chat_data["product_confirm"] = _pending(raw.id, ahmet.id, saman.id)
    query = FakeQuery(data="product:new")
    update = FakeUpdate(callback_query=query)

    await bot_main.on_callback(update, context)

    assert "product_confirm" not in context.chat_data

    tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))
    ).scalar_one()
    yeni_urun = await session.get(Product, tx.lines[0].product_id)
    assert yeni_urun.id != saman.id
    assert yeni_urun.name == "samaan"

    urun_sayisi = (await session.execute(select(func.count(Product.id)))).scalar_one()
    assert urun_sayisi == 2  # saman + samaan


async def test_iptal_ile_hicbir_sey_kaydedilmez(session, patch_session_local, ahmet, saman):
    raw = await _make_raw(session, "ahmet yılmaz 20 balya samaan aldı 5000 tl borç", "prod-3")
    context = FakeContext()
    context.chat_data["product_confirm"] = _pending(raw.id, ahmet.id, saman.id)
    query = FakeQuery(data="product:cancel")
    update = FakeUpdate(callback_query=query)

    await bot_main.on_callback(update, context)

    assert "product_confirm" not in context.chat_data
    query.edit_message_text.assert_awaited_with("İşlem iptal edildi.")

    count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert count == 0
    urun_sayisi = (await session.execute(select(func.count(Product.id)))).scalar_one()
    assert urun_sayisi == 1  # yalnızca "saman" fixture'ı


async def test_pending_yoksa_gecerli_degil_mesaji(session, patch_session_local):
    context = FakeContext()
    query = FakeQuery(data="product:yes")
    update = FakeUpdate(callback_query=query)

    await bot_main.on_callback(update, context)

    query.edit_message_text.assert_awaited_with("Bu istek artık geçerli değil.")


async def test_kisi_bulunamazsa_bulunamadi_der(session, patch_session_local, saman):
    raw = await _make_raw(session, "ahmet yılmaz 20 balya samaan aldı 5000 tl borç", "prod-4")
    context = FakeContext()
    context.chat_data["product_confirm"] = _pending(raw.id, 999999, saman.id)
    query = FakeQuery(data="product:yes")
    update = FakeUpdate(callback_query=query)

    await bot_main.on_callback(update, context)

    query.edit_message_text.assert_awaited_with("Kişi bulunamadı.")
