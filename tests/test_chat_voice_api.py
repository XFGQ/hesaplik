"""Web sohbetinde sesli mesaj (POST /api/chat/voice) — Telegram'daki
on_voice'un web karşılığı (bkz. tests/test_bot_voice_flow.py, aynı senaryolar).

Ses yalnızca bir ÖN ADIMDIR: Groq'un çıkardığı metin, kullanıcı yazmış gibi
handle_text'e girer. Bu yüzden buradaki testler "ses metne dönünce yazılı
mesajla AYNI şey oluyor mu" sorusunu sorar — ayrı bir kayıt mantığı olmadığının
kanıtı budur. Gerçek Groq'a ASLA bağlanılmaz: stt.get_stt_provider
monkeypatch'lenir (sağlayıcının kendi HTTP davranışı tests/test_stt.py'de).
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.auth import router as auth_router
from app.api.chat import VOICE_MAX_BYTES, _voice_filename, router as chat_router
from app.db import get_session
from app.models import Person, RawMessage, Transaction, TxSource
from app.services import stt, web_chat
from conftest import AUTH_PASSWORD, AUTH_USERNAME


class FakeSTTProvider:
    """Groq'un yerine geçer; hangi dosya adıyla çağrıldığını kaydeder
    (biçim uzantısı Groq için tek ipucu, bkz. app/services/stt.py)."""

    def __init__(self, text: str | None):
        self.text = text
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str | None:
        self.calls.append((audio, filename))
        return self.text


@pytest_asyncio.fixture(loop_scope="session")
async def client(session):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    p = Person(full_name="Ahmet Yılmaz")
    session.add(p)
    await session.flush()
    return p


@pytest.fixture
def stt_provider(monkeypatch):
    """Varsayılan: STT açık ve metni döndürüyor. Test `.text`i değiştirebilir."""
    provider = FakeSTTProvider("ahmet yılmaz 500 tl borç yazdım")
    monkeypatch.setattr(stt, "get_stt_provider", lambda: provider)
    return provider


async def _login(client) -> None:
    r = await client.post("/api/auth/login", json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


def _voice_file(data: bytes = b"sahte-webm-verisi", name: str = "kayit.webm", mime: str = "audio/webm"):
    return {"file": (name, data, mime)}


# ---------------------------------------------------------------- yetki

async def test_tokensiz_401(client, auth_account):
    r = await client.post("/api/chat/voice", files=_voice_file())
    assert r.status_code == 401


# ---------------------------------------------------------------- mutlu yol

async def test_ses_metne_cevrilip_dogrudan_kaydedilir(
    client, auth_account, session, ahmet, stt_provider
):
    """Ara onay YOK: net bir cümle, yazılı mesajdaki gibi anında deftere geçer."""
    await _login(client)

    r = await client.post("/api/chat/voice", files=_voice_file())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transcript"] == "ahmet yılmaz 500 tl borç yazdım"
    assert body["messages"][0]["outcome"] == "recorded"

    tx = (await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))).scalar_one()
    assert tx.amount_try == Decimal("500.00")
    # Kaynak izi: "sesle söylendi" ile "yazıldı" ayrı görünür.
    assert tx.source == TxSource.WEB_VOICE
    assert tx.created_by == AUTH_USERNAME


async def test_ham_mesaj_web_voice_kanaliyla_ve_ceviriyle_yazilir(
    client, auth_account, session, ahmet, stt_provider
):
    await _login(client)
    await client.post("/api/chat/voice", files=_voice_file())

    raw = (await session.execute(select(RawMessage))).scalars().one()
    assert raw.channel == "web_voice"
    assert raw.chat_id == AUTH_USERNAME
    assert raw.voice_transcript == "ahmet yılmaz 500 tl borç yazdım"
    assert raw.payload["voice"] is True
    # Ham ses saklanmaz, yalnızca izi (boyut/biçim) durur.
    assert "audio" not in raw.payload


async def test_biçim_uzantisi_groqa_oldugu_gibi_gecmez(client, auth_account, stt_provider):
    """Dosya adı istemciden gelir: yalnızca allowlist'teki uzantı korunur."""
    await _login(client)

    await client.post("/api/chat/voice", files=_voice_file(name="kayit.webm"))
    await client.post("/api/chat/voice", files=_voice_file(name="ses.m4a", mime="audio/mp4"))
    await client.post("/api/chat/voice", files=_voice_file(name="tuhaf.exe", mime="audio/webm"))

    assert [f for _a, f in stt_provider.calls] == ["voice.webm", "voice.m4a", "voice.webm"]


# ---------------------------------------------------------------- STT hataları

async def test_ses_anlasilmazsa_kayit_yapilmaz_nazikce_soylenir(
    client, auth_account, session, ahmet, stt_provider
):
    stt_provider.text = None
    await _login(client)

    r = await client.post("/api/chat/voice", files=_voice_file())
    assert r.status_code == 200, "anlaşılmayan ses bir arıza değil, bir cevaptır"
    body = r.json()
    assert body["transcript"] is None
    assert body["messages"][0]["outcome"] == "voice_failed"
    assert "yazarak" in body["messages"][0]["reply"]
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0


async def test_anlasilmayan_seste_bile_ham_kayit_durur(
    client, auth_account, session, stt_provider
):
    """Mesaj asla kaybolmaz: satır Groq'a GİTMEDEN önce açılır."""
    stt_provider.text = None
    await _login(client)
    await client.post("/api/chat/voice", files=_voice_file())

    raw = (await session.execute(select(RawMessage))).scalars().one()
    assert raw.channel == "web_voice"
    assert raw.voice_transcript is None


async def test_stt_kapaliysa_yazmasi_istenir(client, auth_account, session, monkeypatch):
    monkeypatch.setattr(stt, "get_stt_provider", lambda: None)
    await _login(client)

    r = await client.post("/api/chat/voice", files=_voice_file())
    assert r.status_code == 200
    assert r.json()["messages"][0]["outcome"] == "voice_disabled"
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0


async def test_bos_ses_reddedilir(client, auth_account, stt_provider):
    await _login(client)
    r = await client.post("/api/chat/voice", files=_voice_file(data=b""))
    assert r.status_code == 422
    assert not stt_provider.calls


async def test_cok_buyuk_kayit_reddedilir(client, auth_account, stt_provider):
    await _login(client)
    r = await client.post("/api/chat/voice", files=_voice_file(data=b"x" * (VOICE_MAX_BYTES + 1)))
    assert r.status_code == 413
    assert not stt_provider.calls


# ---------------------------------------------------------------- akış paritesi

async def test_belirsiz_kisi_seste_de_sorulur_secilince_kaydedilir(
    client, auth_account, session, stt_provider
):
    """Kişi eşleştirme güvenliği ses için de aynı: "hangisi?" sorulur, sonra
    normal buton akışı (/api/chat/confirm) devam eder."""
    duman = Person(full_name="Furkan Duman")
    yildiz = Person(full_name="Furkan Yıldız")
    session.add_all([duman, yildiz])
    await session.flush()
    stt_provider.text = "furkan 500 tl borç yazdım"
    await _login(client)

    r = await client.post("/api/chat/voice", files=_voice_file())
    assert r.json()["messages"][0]["outcome"] == "needs_confirmation"
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0

    r2 = await client.post("/api/chat/confirm", json={"action": f"person:pick:{duman.id}"})
    assert r2.json()["messages"][0]["outcome"] == "recorded"
    tx = (await session.execute(select(Transaction).where(Transaction.person_id == duman.id))).scalar_one()
    assert tx.source == TxSource.WEB_VOICE, "onay sonrası tamamlanan kayıt da sesten doğdu"


async def test_sesle_verilen_cevap_bekleyen_soruyu_yanitlar(
    client, auth_account, session, ahmet, stt_provider
):
    """Bekleyen bir soru varken gelen ses de yazılı cevapla aynı yere düşer
    (koşan formatın tutar sorusu — bkz. tests/test_chat_api.py)."""
    await _login(client)
    stt_provider.text = "ahmet yılmaz 70-20-50 saman"
    r = await client.post("/api/chat/voice", files=_voice_file())
    assert r.json()["messages"][0]["outcome"] == "running_amount_needed"

    stt_provider.text = "5 bin"
    r2 = await client.post("/api/chat/voice", files=_voice_file())
    assert r2.json()["messages"][0]["outcome"] == "recorded"
    tx = (await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))).scalar_one()
    assert tx.amount_try == Decimal("5000.00")


# ---------------------------------------------------------------- saf yardımcılar


class _Upload:
    def __init__(self, filename):
        self.filename = filename


def test_voice_filename_allowlist():
    assert _voice_filename(_Upload("kayit.webm")) == "voice.webm"
    assert _voice_filename(_Upload("ses.OGG")) == "voice.ogg"
    assert _voice_filename(_Upload("iphone.mp4")) == "voice.mp4"
    # Tanınmayan/eksik/tehlikeli ad sessizce varsayılana düşer.
    assert _voice_filename(_Upload("betik.sh")) == "voice.webm"
    assert _voice_filename(_Upload("uzantisiz")) == "voice.webm"
    assert _voice_filename(_Upload(None)) == "voice.webm"
    assert _voice_filename(_Upload("../../etc/passwd")) == "voice.webm"


def test_voice_metinleri_botla_ayni():
    """İki mantık OLMAYACAK: kullanıcıya gösterilen metin bot ile aynı yerden."""
    from app.bot.main import VOICE_ANLASILAMADI_METNI, VOICE_KAPALI_METNI

    assert web_chat.VOICE_ANLASILAMADI_METNI is VOICE_ANLASILAMADI_METNI
    assert web_chat.VOICE_KAPALI_METNI is VOICE_KAPALI_METNI
