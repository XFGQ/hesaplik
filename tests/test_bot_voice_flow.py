"""Sesli mesaj akışı (CLAUDE.md > "Telegram sesli mesajları için
speech-to-text ekle"): on_voice, Groq'un STT çıktısını AYNI
message_processor akışına (_process_text) sokmalı. Gerçek Groq'a ASLA
bağlanılmaz — bot_main.stt.get_stt_provider monkeypatch'lenir (sağlayıcının
kendi HTTP davranışı tests/test_stt.py'de ayrıca test edilir)."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import Person, RawMessage, Transaction, TxSource


@pytest_asyncio.fixture(loop_scope="session")
async def patch_session_local(engine, monkeypatch):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(bot_main, "SessionLocal", maker)
    return maker


class FakeVoice:
    def __init__(self, file_id: str = "voice-file-1"):
        self.file_id = file_id


class FakeMessage:
    def __init__(self, chat_id: int = 1, voice: FakeVoice | None = None):
        self.chat_id = chat_id
        self.voice = voice or FakeVoice()
        self.reply_text = AsyncMock()
        self.reply_document = AsyncMock()


class _FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeUpdate:
    def __init__(self, message: FakeMessage | None = None):
        self.message = message or FakeMessage()
        self.effective_chat = _FakeChat(self.message.chat_id)

    def to_dict(self):
        return {
            "update_id": id(self),
            "message": {"voice": {"file_id": self.message.voice.file_id}, "chat": {"id": self.message.chat_id}},
        }


class FakeSTTProvider:
    def __init__(self, text: str | None):
        self.text = text
        self.calls: list[bytes] = []

    async def transcribe(self, audio: bytes) -> str | None:
        self.calls.append(audio)
        return self.text


class FakeContext:
    def __init__(self, get_file_bytes: bytes = b"ses-verisi"):
        self.chat_data: dict = {}
        self.bot = AsyncMock()
        fake_file = AsyncMock()
        fake_file.download_as_bytearray = AsyncMock(return_value=bytearray(get_file_bytes))
        self.bot.get_file = AsyncMock(return_value=fake_file)


def _fake_context(get_file_bytes: bytes = b"ses-verisi") -> FakeContext:
    return FakeContext(get_file_bytes)


def _reply_texts(update: FakeUpdate) -> list[str]:
    return [call.args[0] for call in update.message.reply_text.await_args_list]


async def _tx_count(session) -> int:
    return (await session.execute(select(func.count(Transaction.id)))).scalar_one()


async def test_groq_kapaliysa_yaziniz_denir_hicbir_sey_islenmez(
    session, patch_session_local, monkeypatch
):
    monkeypatch.setattr(bot_main.stt, "get_stt_provider", lambda: None)

    context = _fake_context()
    update = FakeUpdate()

    await bot_main.on_voice(update, context)

    replies = _reply_texts(update)
    assert replies == [bot_main.VOICE_KAPALI_METNI]
    context.bot.get_file.assert_not_awaited()

    # Mesaj asla kaybolmaz: raw_messages'a Groq'a hiç gidilmeden önce yazılır.
    raw = (await session.execute(select(RawMessage))).scalar_one()
    assert raw.voice_transcript is None


async def test_transkript_basarisizsa_anlasilamadi_denir(session, patch_session_local, monkeypatch):
    fake_provider = FakeSTTProvider(text=None)
    monkeypatch.setattr(bot_main.stt, "get_stt_provider", lambda: fake_provider)

    context = _fake_context()
    update = FakeUpdate()

    await bot_main.on_voice(update, context)

    replies = _reply_texts(update)
    assert replies == [bot_main.VOICE_ANLASILAMADI_METNI]
    assert fake_provider.calls == [b"ses-verisi"]

    raw = (await session.execute(select(RawMessage))).scalar_one()
    assert raw.voice_transcript is None
    assert await _tx_count(session) == 0


async def test_basarili_transkript_kaydi_olusturur_ve_kaynak_sesli(
    session, patch_session_local, monkeypatch
):
    ahmet = Person(full_name="Ahmet Yılmaz")
    session.add(ahmet)
    await session.flush()
    await session.commit()

    fake_provider = FakeSTTProvider(text="ahmet yılmaz 1000 tl borç yazdım")
    monkeypatch.setattr(bot_main.stt, "get_stt_provider", lambda: fake_provider)

    context = _fake_context()
    update = FakeUpdate()

    await bot_main.on_voice(update, context)

    replies = _reply_texts(update)
    assert replies[0] == "🎤 Anladım: ahmet yılmaz 1000 tl borç yazdım"
    assert len(replies) == 2  # "anladım" geri bildirimi + kayıt onayı
    assert "Ahmet Yılmaz" in replies[1]

    raw = (await session.execute(select(RawMessage))).scalar_one()
    assert raw.voice_transcript == "ahmet yılmaz 1000 tl borç yazdım"

    tx = (await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))).scalar_one()
    assert tx.amount_try == Decimal("1000.00")
    assert tx.source == TxSource.TELEGRAM_VOICE
    assert tx.raw_text == "ahmet yılmaz 1000 tl borç yazdım"


async def test_coklu_islem_bolmesi_ses_mesajinda_da_calisir(session, patch_session_local, monkeypatch):
    ahmet = Person(full_name="Ahmet Yılmaz")
    mehmet = Person(full_name="Mehmet Öztürk")
    session.add_all([ahmet, mehmet])
    await session.flush()
    await session.commit()

    fake_provider = FakeSTTProvider(
        text="ahmet yılmaz 1000 tl borç yazdım\nmehmet öztürk 500 tl ödedi"
    )
    monkeypatch.setattr(bot_main.stt, "get_stt_provider", lambda: fake_provider)

    context = _fake_context()
    update = FakeUpdate()

    await bot_main.on_voice(update, context)

    assert await _tx_count(session) == 2
    txs = (await session.execute(select(Transaction))).scalars().all()
    assert all(tx.source == TxSource.TELEGRAM_VOICE for tx in txs)
