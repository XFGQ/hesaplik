"""Bot kişi silme = arşivleme akışı (CLAUDE.md > "Bot kişi silme =
arşivleme — Grup 3"): "furkanı sil" hiçbir şeyi hemen silmez, kişi
netleşince YAZARAK onay istenir (işletme adının ilk kelimesi, Türkçe büyük
harf). Doğru yazılırsa person_archive.archive_person çağrılır, yanlış/eksik
onayda hiçbir şey arşivlenmez. Geri getirme bot'tan YAPILMAZ (test edilmez,
kasıtlı olarak yok)."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import ArchivedPerson, Person, RawMessage
from app.services import message_processor
from app.services.ledger import Balance


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


@pytest_asyncio.fixture(loop_scope="session")
async def furkan(session):
    p = Person(full_name="Furkan Duman")
    session.add(p)
    await session.flush()
    await session.commit()
    return p


# --------------------------------------------------------------- Grup 4 (CLAUDE.md
# > "Silme mesajı + kişi düzenleme"): kullanıcıya gösterilen metin "arşiv"
# değil "sil" dili kullanmalı — arka planda hâlâ arşivleniyor ama kullanıcı
# bunu bilmez.


def test_format_archive_confirm_silindi_dili_kullanir():
    bal = Balance(person_id=1, balance_try=Decimal("1000.00"))
    msg = bot_main._format_archive_confirm("Furkan Duman", bal, "HESAPLIK")

    assert "silinecek" in msg
    assert "arşiv" not in msg.lower()
    assert "⚠️ Furkan Duman'in 1.000,00 TL borcu var." in msg
    assert "Onaylıyorsan HESAPLIK yaz." in msg


async def test_dogru_onay_kelimesi_kisiyi_arsivler(session, patch_session_local, furkan):
    context = FakeContext()
    update = FakeUpdate()
    context.chat_data["archive_confirm"] = {
        "person_id": furkan.id,
        "kind": "archive_person",
        "onay_kelimesi": "HESAPLIK",
    }

    await bot_main._handle_archive_confirm_text(update, context, context.chat_data.pop("archive_confirm"), "hesaplık")

    update.message.reply_text.assert_awaited_with("Furkan Duman silindi.")

    await session.refresh(furkan)
    assert furkan.is_active is False

    archived = (
        await session.execute(
            select(ArchivedPerson).where(ArchivedPerson.original_person_id == furkan.id)
        )
    ).scalar_one()
    assert archived.full_name == "Furkan Duman"

    # Yeniden oluşturma istenmedi, aynı isimle yeni bir kişi açılmamalı.
    count = (
        await session.execute(select(func.count(Person.id)).where(Person.full_name == "Furkan Duman"))
    ).scalar_one()
    assert count == 1


async def test_yanlis_onay_kelimesi_iptal_eder(session, patch_session_local, furkan):
    context = FakeContext()
    update = FakeUpdate()
    pending = {"person_id": furkan.id, "kind": "archive_person", "onay_kelimesi": "HESAPLIK"}
    context.chat_data["archive_confirm"] = pending

    await bot_main._handle_archive_confirm_text(update, context, context.chat_data.pop("archive_confirm"), "yanlış kelime")

    update.message.reply_text.assert_awaited_with("İşlem iptal edildi.")

    await session.refresh(furkan)
    assert furkan.is_active is True
    count = (await session.execute(select(func.count(ArchivedPerson.id)))).scalar_one()
    assert count == 0


async def test_eksik_onay_kelimesi_iptal_eder(session, patch_session_local, furkan):
    context = FakeContext()
    update = FakeUpdate()
    pending = {"person_id": furkan.id, "kind": "archive_person", "onay_kelimesi": "HESAPLIK"}

    await bot_main._handle_archive_confirm_text(update, context, pending, "")

    update.message.reply_text.assert_awaited_with("İşlem iptal edildi.")
    await session.refresh(furkan)
    assert furkan.is_active is True


async def test_archive_and_recreate_dogru_onay_temiz_kisi_acar(session, patch_session_local, furkan):
    # Furkan'ın 1000 TL borcu var, sonra "sil yeniden oluştur" ile arşivlenip
    # aynı isimle bakiyesi sıfır temiz bir kişi açılmalı.
    raw = await _make_raw(session, "furkan duman 1000 tl borç yazdım", "arch-1")
    result = await message_processor.process_raw_message(session, raw, "furkan duman 1000 tl borç yazdım")
    assert result.outcome == message_processor.ProcessOutcome.RECORDED
    await session.commit()

    context = FakeContext()
    update = FakeUpdate()
    pending = {"person_id": furkan.id, "kind": "archive_and_recreate", "onay_kelimesi": "HESAPLIK"}

    await bot_main._handle_archive_confirm_text(update, context, pending, "hesaplık")

    update.message.reply_text.assert_awaited_with("Furkan Duman silindi, temiz hesap açıldı.")

    await session.refresh(furkan)
    assert furkan.is_active is False

    yeni = (
        await session.execute(
            select(Person).where(Person.full_name == "Furkan Duman", Person.is_active.is_(True))
        )
    ).scalar_one()
    assert yeni.id != furkan.id

    from app.services.ledger import balance_of
    bal = await balance_of(session, yeni.id)
    assert bal.balance_try == Decimal("0.00")


async def test_coklu_aday_secilince_onay_istenir(session, patch_session_local):
    a = Person(full_name="Furkan Duman")
    b = Person(full_name="Furkan Yılmaz")
    session.add_all([a, b])
    await session.flush()
    await session.commit()

    raw = await _make_raw(session, "furkanı sil", "arch-2")
    pending = {
        "kind": "archive_person",
        "person_name_raw": "furkanı",
        "qty": None,
        "unit": None,
        "product_name": None,
        "amount": None,
        "raw_message_id": raw.id,
        "raw_text": "furkanı sil",
    }
    context = FakeContext()
    query = FakeQuery()
    context.chat_data["pending"] = pending

    await bot_main._handle_person_pick(query, context, a.id)

    assert "archive_confirm" in context.chat_data
    confirm = context.chat_data["archive_confirm"]
    assert confirm["person_id"] == a.id
    assert confirm["kind"] == "archive_person"
    assert confirm["onay_kelimesi"] == "HESAPLIK"
    query.edit_message_text.assert_awaited()

    # Onay verilmeden hiçbir şey arşivlenmemiş olmalı.
    await session.refresh(a)
    assert a.is_active is True
