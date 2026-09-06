"""raw_messages'a yazılan izleme verisi (admin paneli "İşlem Akışı", Faz 7).

Buradaki testler iki şeyi korur:
1. Her işlenen mesaj için "sistem ne algıladı / ne yaptı" bilgisi YAZILIR —
   yazılmazsa panel boş kalır ve bir hata sonradan incelenemez.
2. İzleme YAN ETKİDİR: alanların doldurulması işin sonucunu (kaydedildi mi,
   soru mu soruldu) DEĞİŞTİRMEZ.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db
from app.models import Person, RawMessage
from app.services import message_processor, message_trace
from app.services.message_processor import ProcessOutcome
from app.services.parser import ParsedIntent


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    p = Person(full_name="Ahmet Yılmaz")
    session.add(p)
    await session.flush()
    return p


async def _make_raw(session, text: str, update_id: int) -> RawMessage:
    raw = RawMessage(
        channel="telegram",
        external_id=str(update_id),
        chat_id="42",
        payload={"message": {"chat": {"id": 42}, "text": text}},
    )
    session.add(raw)
    await session.flush()
    return raw


# ---------------------------------------------------------------- ham metin

def test_payload_text_telegram_mesaji():
    payload = {"message": {"chat": {"id": 1}, "text": "ahmet 500 tl verdi"}}
    assert message_trace.payload_text(payload) == "ahmet 500 tl verdi"


def test_payload_text_buton_basimi():
    payload = {"callback_query": {"data": "person:7", "message": {"chat": {"id": 1}}}}
    assert message_trace.payload_text(payload) == "(buton) person:7"


def test_payload_text_ses_kaydi():
    payload = {"message": {"chat": {"id": 1}, "voice": {"file_id": "abc"}}}
    assert message_trace.payload_text(payload) == "(ses kaydı)"


def test_payload_text_web_ses_kaydi():
    """Web mikrofon kaydı: payload'da metin yok, ham ses de saklanmaz —
    çeviri başarısızsa panelde boş satır değil "(ses kaydı)" görünsün."""
    payload = {"voice": True, "filename": "voice.webm", "size": 33904}
    assert message_trace.payload_text(payload) == "(ses kaydı)"


def test_display_text_web_sesinde_ceviriyi_tercih_eder():
    raw = RawMessage(
        payload={"voice": True, "filename": "voice.webm", "size": 100},
        voice_transcript="ahmet 500 tl borç",
    )
    assert message_trace.display_text(raw) == "ahmet 500 tl borç"


def test_payload_text_bilinmeyen_bicim_none():
    assert message_trace.payload_text({"update_id": 5}) is None
    assert message_trace.payload_text(None) is None


def test_display_text_voice_transcript_varsa_onu_doner():
    raw = RawMessage(
        payload={"message": {"chat": {"id": 1}, "voice": {"file_id": "abc"}}},
        voice_transcript="ahmet 500 tl verdi",
    )
    assert message_trace.display_text(raw) == "ahmet 500 tl verdi"


def test_display_text_voice_transcript_yoksa_payload_text_kullanilir():
    raw = RawMessage(payload={"message": {"chat": {"id": 1}, "text": "ahmet 500 tl verdi"}})
    assert message_trace.display_text(raw) == "ahmet 500 tl verdi"


# ---------------------------------------------------------------- kayıt akışı

async def test_borc_kaydinda_izleme_yazilir(session, ahmet):
    text = "ahmet yılmaz 20 balya saman aldı 15000 tl borç"
    raw = await _make_raw(session, text, 9001)

    result = await message_processor.process_raw_message(session, raw, text)
    assert result.outcome == ProcessOutcome.RECORDED  # davranış değişmedi

    await session.refresh(raw)
    assert raw.detected_kind == message_trace.KIND_DEBT
    assert raw.detected_person == "Ahmet Yılmaz"
    assert raw.detected_amount == Decimal("15000.00")
    assert raw.detected_product is not None
    assert "saman" in raw.detected_product.lower()
    assert raw.detected_qty == Decimal("20.00")
    assert raw.detected_unit == "balya"
    assert raw.parse_source == message_trace.SOURCE_REGEX
    assert raw.parse_ms is not None and raw.parse_ms >= 0
    assert raw.outcome == message_trace.OUTCOME_RECORDED
    assert raw.transaction_id == result.transaction_id


async def test_tahsilat_kaydinda_kind_payment(session, ahmet):
    text = "ahmet yılmaz 2000 tl ödedi"
    raw = await _make_raw(session, text, 9002)

    result = await message_processor.process_raw_message(session, raw, text)
    assert result.outcome == ProcessOutcome.RECORDED

    await session.refresh(raw)
    assert raw.detected_kind == message_trace.KIND_PAYMENT
    assert raw.detected_amount == Decimal("2000.00")
    assert raw.outcome == message_trace.OUTCOME_RECORDED


async def test_bakiye_sorgusu_yanitlandi(session, ahmet):
    text = "ahmet yılmaz bakiye"
    raw = await _make_raw(session, text, 9003)

    result = await message_processor.process_raw_message(session, raw, text)
    assert result.outcome == ProcessOutcome.BALANCE

    await session.refresh(raw)
    assert raw.detected_kind == message_trace.KIND_QUERY
    assert raw.detected_person == "Ahmet Yılmaz"
    assert raw.outcome == message_trace.OUTCOME_ANSWERED
    # Salt okunur sorgu deftere yazmaz.
    assert raw.transaction_id is None


async def test_anlasilamayan_mesaj_yok_sayildi(session):
    text = "bugün hava çok güzel"
    raw = await _make_raw(session, text, 9004)

    result = await message_processor.process_raw_message(session, raw, text)
    assert result.outcome == ProcessOutcome.UNRECOGNIZED

    await session.refresh(raw)
    assert raw.detected_kind == message_trace.KIND_NONE
    assert raw.parse_source == message_trace.SOURCE_NONE
    assert raw.outcome == message_trace.OUTCOME_IGNORED
    assert "anlaşılamadı" in raw.outcome_detail


async def test_coklu_kisi_adayinda_soru_soruldu(session):
    session.add_all([Person(full_name="Furkan Duman"), Person(full_name="Furkan Yıldırım")])
    await session.flush()

    text = "furkan 500 tl verdi"
    raw = await _make_raw(session, text, 9005)

    result = await message_processor.process_raw_message(session, raw, text)
    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION

    await session.refresh(raw)
    assert raw.outcome == message_trace.OUTCOME_ASKED
    # Kişi netleşmedi ama ham isim izde durur: paneli inceleyen kişi neyin
    # sorulduğunu görebilmeli.
    assert raw.detected_person == "furkan"


async def test_llm_yolunda_parse_source_llm(session, ahmet, fake_llm):
    """Kural parser çözemeyip LLM devreye girerse kaynak "llm" işaretlenir —
    hangi mesajların yavaş yola düştüğü panelde görünsün."""
    fake_llm.intent = ParsedIntent(
        kind="debt", person_name="ahmet yılmaz", amount=Decimal("750")
    )
    text = "geçen hafta konuştuğumuz şeyi hallettim sanırım"
    raw = await _make_raw(session, text, 9006)

    result = await message_processor.process_raw_message(session, raw, text)
    assert fake_llm.called
    assert result.outcome == ProcessOutcome.LLM_CONFIRMATION  # LLM doğrudan kaydetmez

    await session.refresh(raw)
    assert raw.parse_source == message_trace.SOURCE_LLM
    assert raw.outcome == message_trace.OUTCOME_ASKED
    assert raw.detected_kind == message_trace.KIND_DEBT
    assert raw.detected_amount == Decimal("750.00")


async def test_coklu_istekte_parca_isaretlenir(session, ahmet):
    """Tek Telegram güncellemesi = tek raw_messages satırı, ama birden çok
    işlem olabilir. Satır son işlenen parçanın izini taşır; parça metni tam
    mesajdan farklıysa outcome_detail bunu söyler."""
    tam_mesaj = "ahmet yılmaz 500 tl ödedi\nahmet yılmaz 300 tl ödedi"
    raw = await _make_raw(session, tam_mesaj, 9007)

    await message_processor.process_raw_message(session, raw, "ahmet yılmaz 500 tl ödedi")
    await message_processor.process_raw_message(session, raw, "ahmet yılmaz 300 tl ödedi")

    await session.refresh(raw)
    assert 'parça: "ahmet yılmaz 300 tl ödedi"' in raw.outcome_detail


async def test_islem_hatasi_hata_olarak_izlenir(session, ahmet, engine, monkeypatch):
    """İşleme sırasında istisna çıkarsa asıl session geri alınır — izin de
    onunla kaybolmaması için hata AYRI bir bağlantıya yazılır. Panelde
    "hata" görünmezse arıza sessizce kaybolur."""
    monkeypatch.setattr(
        app.db, "SessionLocal", async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    )

    text = "ahmet yılmaz 1000 tl ödedi"
    raw = await _make_raw(session, text, 9009)
    await session.commit()  # iz başka bağlantıdan yazılacak, satır görünür olmalı

    async def _patla(*_args, **_kwargs):
        raise RuntimeError("defter kilitli")

    monkeypatch.setattr(message_processor, "record_resolved", _patla)

    with pytest.raises(RuntimeError):
        await message_processor.process_raw_message(session, raw, text)

    await session.rollback()
    await session.refresh(raw)
    assert raw.outcome == message_trace.OUTCOME_ERROR
    assert "defter kilitli" in raw.outcome_detail
    assert raw.detected_kind == message_trace.KIND_PAYMENT
    assert raw.transaction_id is None  # hiçbir şey kaydedilmedi


async def test_izleme_kaydi_bozamaz(session, ahmet, monkeypatch):
    """İzleme yan etkidir: message_trace patlasa bile borç yine kaydedilir."""

    def _patla(*_args, **_kwargs):
        raise RuntimeError("izleme bozuk")

    monkeypatch.setattr(message_trace, "fill", _patla)

    text = "ahmet yılmaz 1000 tl ödedi"
    raw = await _make_raw(session, text, 9008)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    assert result.transaction_id is not None
