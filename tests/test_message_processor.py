from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select

from app.models import Person, Product, RawMessage, Transaction, TxKind, TxSource
from app.services import llm_provider, message_processor
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent
from app.services.message_processor import ProcessOutcome
from app.services.parser import ParsedIntent


class _FakeLLMProvider:
    """Testlerde gerçek Ollama'ya bağlanmamak için: sabit bir ParsedIntent
    (ya da None) döner, çağrılıp çağrılmadığını işaretler."""

    def __init__(self, intent: ParsedIntent | None):
        self._intent = intent
        self.called = False

    async def parse(self, text: str) -> ParsedIntent | None:
        self.called = True
        return self._intent


def _mock_llm(monkeypatch, provider) -> None:
    """get_active_provider(session)'ı sahte/None bir sağlayıcıya çevirir
    (session parametresi bu testlerde önemsiz, yok sayılır)."""

    async def _get_active_provider(session):
        return provider

    monkeypatch.setattr(llm_provider, "get_active_provider", _get_active_provider)


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


async def test_raw_voice_transcript_doluysa_kaynak_sesli_yazilir(session, ahmet):
    """CLAUDE.md > "Telegram sesli mesajları için speech-to-text ekle":
    raw.voice_transcript doluysa (bot on_voice tarafından Groq çevirisi
    yazıldıktan sonra) bu satırdan doğan kayıt TELEGRAM_VOICE kaynaklı
    sayılmalı — record_resolved raw'dan türetir, ayrı bir parametre
    taşımaya gerek yoktur."""
    text = "ahmet yılmaz 500 tl borç yazdım"
    raw = await _make_raw(session, text, 99)
    raw.voice_transcript = text
    await session.flush()

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.source == TxSource.TELEGRAM_VOICE


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


# ------------------------------------------------------------------
# Faz 4 — LLM fallback (CLAUDE.md > "Faz 4 — LLM"). Kural parser her zaman
# önce denenir; yalnızca çözemezse (None) ve LLM aktifse fallback devreye
# girer. Testlerde gerçek Ollama'ya bağlanılmaz, provider mock'lanır.

_LLM_ANLAMSIZ_METIN = "ahmete bir miktar ödeme yapmak istiyorum"


async def test_kural_parser_cozerse_llm_hic_cagrilmaz(session, monkeypatch, ahmet):
    fake = _FakeLLMProvider(None)
    _mock_llm(monkeypatch, fake)

    text = "ahmet yılmaz 500 tl borç yazdım"
    raw = await _make_raw(session, text, 40)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    assert fake.called is False


async def test_borc_kelimesi_tahsilat_fiiliyle_karisan_cumle_llme_duser(session, monkeypatch, ahmet):
    # Bug (2026-07-26): "ahmet yılmaz 20 balya borcunu 15000 tl ödedi" kural
    # parser'ı yanıltıp sahte bir bakiye sorgusuna ("ahmet yılmaz 20 balya"
    # diye anlamsız bir isimle) dönüştürüyordu; bu da rule parser "çözdüm"
    # sandığı için LLM'e HİÇ düşmüyordu (process_raw_message'ın LLM dalı
    # izole çalışsa da bot yolunda devreye girmiyordu). Kural parser artık
    # bu karışık cümlede None dönüyor (bkz. test_parser.py), bu da LLM
    # fallback'in gerçekten tetiklendiğini doğruluyor.
    text = "ahmet yılmaz 20 balya borcunu 15000 tl ödedi"
    intent = ParsedIntent(kind="payment", person_name="ahmet yılmaz", amount=Decimal("15000"))
    fake = _FakeLLMProvider(intent)
    _mock_llm(monkeypatch, fake)

    raw = await _make_raw(session, text, 45)
    result = await message_processor.process_raw_message(session, raw, text)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.LLM_CONFIRMATION
    assert result.resolved.kind == "payment"
    assert result.resolved.person.id == ahmet.id
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0


async def test_llm_fallback_net_kayit_onay_ister(session, monkeypatch, ahmet):
    # Kural parser çözemiyor (LLM'e özgü serbest cümle), LLM ise net bir
    # borç niyeti dönüyor. Kişi/tutar net olsa da LLM kaynaklı olduğu için
    # doğrudan kaydedilmez — RECORDED değil, LLM_CONFIRMATION dönmeli.
    intent = ParsedIntent(kind="debt", person_name="ahmet yılmaz", amount=Decimal("500"))
    fake = _FakeLLMProvider(intent)
    _mock_llm(monkeypatch, fake)

    raw = await _make_raw(session, _LLM_ANLAMSIZ_METIN, 41)
    result = await message_processor.process_raw_message(session, raw, _LLM_ANLAMSIZ_METIN)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.LLM_CONFIRMATION
    assert result.resolved.person.id == ahmet.id
    assert result.resolved.amount == Decimal("500")

    await session.refresh(raw)
    assert raw.processed_at is None
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0


async def test_llm_belirsiz_kisi_de_onay_ister(session, monkeypatch):
    # Kişi eşleştirme güvenliği LLM kaynaklı niyetlerde de aynen uygulanır:
    # tek kelimeli "ahmet" iki adaya uyuyor, otomatik seçilmemeli.
    a = Person(full_name="Ahmet Yılmaz")
    b = Person(full_name="Ahmet Yıldız")
    session.add_all([a, b])
    await session.flush()

    intent = ParsedIntent(kind="debt", person_name="ahmet", amount=Decimal("500"))
    fake = _FakeLLMProvider(intent)
    _mock_llm(monkeypatch, fake)

    raw = await _make_raw(session, _LLM_ANLAMSIZ_METIN, 42)
    result = await message_processor.process_raw_message(session, raw, _LLM_ANLAMSIZ_METIN)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in result.resolved.person_candidates}
    assert {a.id, b.id} == candidate_ids


async def test_llm_erisilemezse_anlasilamadi_doner(session, monkeypatch):
    # LLM None dönerse (bağlantı hatası/timeout/geçersiz yanıt) sistem
    # çökmemeli, mevcut "anlayamadım" (UNRECOGNIZED) davranışına düşmeli.
    fake = _FakeLLMProvider(None)
    _mock_llm(monkeypatch, fake)

    raw = await _make_raw(session, _LLM_ANLAMSIZ_METIN, 43)
    result = await message_processor.process_raw_message(session, raw, _LLM_ANLAMSIZ_METIN)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.UNRECOGNIZED


async def test_rapor_ver_menu_kayit_olusturmaz(session):
    text = "rapor ver"
    raw = await _make_raw(session, text, 50)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.REPORT_MENU
    await session.refresh(raw)
    assert raw.processed_at is None
    assert raw.transaction_id is None


async def test_isim_ekstresi_report_person_pdf_uretir(session, ahmet):
    text = "ahmet yılmaz ekstresi"
    raw = await _make_raw(session, text, 51)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.REPORT_PERSON
    assert result.resolved.person.id == ahmet.id
    assert result.report_pdf is not None
    assert result.report_pdf.startswith(b"%PDF")
    await session.refresh(raw)
    assert raw.processed_at is None
    assert raw.transaction_id is None


async def test_report_person_bulunamayan_kisi(session):
    text = "hiç yok böyle biri ekstresi"
    raw = await _make_raw(session, text, 52)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PERSON_NOT_FOUND
    assert result.resolved.kind == "report_person"


async def test_report_person_belirsiz_kisi_onay_ister(session):
    a = Person(full_name="Ahmet Yılmaz")
    b = Person(full_name="Ahmet Yıldız")
    session.add_all([a, b])
    await session.flush()

    text = "ahmet yı ekstresi"
    raw = await _make_raw(session, text, 53)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    assert result.resolved.kind == "report_person"
    candidate_ids = {p.id for p in result.resolved.person_candidates}
    assert {a.id, b.id} == candidate_ids

    picked = ResolvedIntent(status=ResolutionStatus.READY, kind="report_person", person=a)
    follow_up = await message_processor.handle_resolved(session, raw, picked, text)
    assert follow_up.outcome == ProcessOutcome.REPORT_PERSON
    assert follow_up.report_pdf.startswith(b"%PDF")


# ------------------------------------------------- kişi bilgisi (CLAUDE.md > "DÜZELTME —
# 'bilgi ver' belirsiz, SOR")


async def test_info_menu_belirsiz_bilgi_bekletilir(session, ahmet):
    text = "ahmet yılmaz bilgi ver"
    raw = await _make_raw(session, text, 60)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.INFO_MENU
    assert result.resolved.person.id == ahmet.id
    await session.refresh(raw)
    assert raw.processed_at is None
    assert raw.transaction_id is None


async def test_person_contact_net_niyet_dogrudan_calisir(session, ahmet):
    text = "ahmet yılmaz telefonu"
    raw = await _make_raw(session, text, 61)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PERSON_CONTACT
    assert result.resolved.person.id == ahmet.id


async def test_info_menu_bulunamayan_kisi(session):
    text = "hiç yok böyle biri bilgi ver"
    raw = await _make_raw(session, text, 62)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PERSON_NOT_FOUND
    assert result.resolved.kind == "info_menu"


async def test_info_menu_secilince_bakiye_gosterimi(session, ahmet):
    # Bot'ta "Bakiye / borç" butonuna basılınca kayıt değil bakiye
    # gösterimi olmalı — READY bir ResolvedIntent ile devam edilir.
    text = "ahmet yılmaz bilgi ver"
    raw = await _make_raw(session, text, 63)
    result = await message_processor.process_raw_message(session, raw, text)
    assert result.outcome == ProcessOutcome.INFO_MENU

    picked = ResolvedIntent(status=ResolutionStatus.READY, kind="balance_query", person=ahmet)
    follow_up = await message_processor.handle_resolved(session, raw, picked, text)
    assert follow_up.outcome == ProcessOutcome.BALANCE
    assert follow_up.resolved.person.id == ahmet.id


async def test_genel_rapor_regexle_dogrudan_uretilir(session):
    text = "genel rapor"
    raw = await _make_raw(session, text, 54)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.REPORT_GENERAL
    assert result.report_pdf.startswith(b"%PDF")
    assert result.report_stats is not None
    await session.refresh(raw)
    assert raw.processed_at is None


async def test_gunluk_rapor_regexle_dogrudan_uretilir(session):
    text = "bugünün raporu"
    raw = await _make_raw(session, text, 55)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.REPORT_DAILY
    assert result.report_pdf.startswith(b"%PDF")
    assert result.report_stats is not None


# ------------------------------------------------------------------
# Rapor komutları — gelişmiş anlama (CLAUDE.md): kural parser çözemediği
# rapor cümlelerinde LLM devreye girmeli ve doğru rapora yönlendirmeli.


async def test_regex_cozemedigi_rapor_cumlesinde_llm_devreye_girer(session, monkeypatch):
    # "bana bir durum raporu hazırla": ne "genel"/"tüm" gibi bir genel
    # niteleyici ne "bugün" gibi bir gün niteleyicisi içeriyor — kural
    # parser bunu uydurmadan None döner (bkz. test_parser.py). LLM
    # "islem": "rapor", "tur": "genel" dönerse sistem doğru rapora
    # (REPORT_GENERAL) yönlenmeli.
    text = "bana bir durum raporu hazırla"
    intent = ParsedIntent(kind="report_general")
    fake = _FakeLLMProvider(intent)
    _mock_llm(monkeypatch, fake)

    raw = await _make_raw(session, text, 56)
    result = await message_processor.process_raw_message(session, raw, text)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.REPORT_GENERAL
    assert result.report_pdf.startswith(b"%PDF")


async def test_regex_cozemedigi_kisi_raporu_llm_ile_dogru_kisiye_yonlenir(session, ahmet, monkeypatch):
    # LLM "tur": "kisi" dönerse (ör. "ahmet için bir hesap özeti çıkar" gibi
    # kural parser'ın çözemediği serbest bir cümle — "özeti" report_person
    # anahtar kelimeleri arasında değil), aynı kişi eşleştirme güvenlik
    # kuralları (pg_trgm) uygulanarak report_person akışına girmeli.
    text = "ahmet için bir hesap özeti çıkar"
    intent = ParsedIntent(kind="report_person", person_name="ahmet yılmaz")
    fake = _FakeLLMProvider(intent)
    _mock_llm(monkeypatch, fake)

    raw = await _make_raw(session, text, 57)
    result = await message_processor.process_raw_message(session, raw, text)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.REPORT_PERSON
    assert result.resolved.person.id == ahmet.id
    assert result.report_pdf.startswith(b"%PDF")


async def test_llm_kapaliyken_kural_parser_cozemezse_hic_cagrilmaz(session, monkeypatch):
    # llm_primary=none iken get_active_provider() None döner, LLM'e hiç
    # gidilmez — mevcut davranış aynen korunur. Gerçek ortamın durumu
    # (yerelde Ollama/vLLM açık olabilir) burada önemli değil;
    # get_active_provider() doğrudan devre dışı bırakılarak test bu
    # duruma bağımlı olmaktan çıkarılıyor.
    _mock_llm(monkeypatch, None)

    raw = await _make_raw(session, _LLM_ANLAMSIZ_METIN, 44)
    result = await message_processor.process_raw_message(session, raw, _LLM_ANLAMSIZ_METIN)

    assert result.outcome == ProcessOutcome.UNRECOGNIZED


# ------------------------------------------------------------------
# Grup 1 (CLAUDE.md > "Bot sorgu anlama — kapsamlı genişletme"): bare bakiye
# anahtar kelimeleri, bakiye tablo verisi, fiilsiz liste sorguları ve tek
# kelime arama.


async def test_bakiye_bare_anahtar_kelime_ile_calisir(session, ahmet):
    text1 = "ahmet yılmaz 1000 tl borç yazdım"
    raw1 = await _make_raw(session, text1, 60)
    await message_processor.process_raw_message(session, raw1, text1)

    text2 = "ahmet yılmaz bakiye"
    raw2 = await _make_raw(session, text2, 61)
    result = await message_processor.process_raw_message(session, raw2, text2)

    assert result.outcome == ProcessOutcome.BALANCE
    assert result.balance.balance_try == Decimal("1000.00")


async def test_bakiye_sorgusu_hareket_tablosu_doner(session, ahmet):
    text1 = "ahmet yılmaz 1000 tl borç yazdım"
    raw1 = await _make_raw(session, text1, 62)
    await message_processor.process_raw_message(session, raw1, text1)

    text2 = "ahmet yılmaz 400 tl ödedi"
    raw2 = await _make_raw(session, text2, 63)
    await message_processor.process_raw_message(session, raw2, text2)

    text3 = "ahmet yılmaz durum"
    raw3 = await _make_raw(session, text3, 64)
    result = await message_processor.process_raw_message(session, raw3, text3)

    assert result.outcome == ProcessOutcome.BALANCE
    assert result.transactions_total == 2
    assert len(result.transactions) == 2
    assert result.transactions[0].amount_try == Decimal("1000.00")
    assert result.transactions[1].amount_try == Decimal("400.00")


async def test_sorgu_bare_kisiler_listele(session, ahmet):
    text = "kişiler"
    raw = await _make_raw(session, text, 65)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.LIST
    assert [r.person.id for r in result.persons] == [ahmet.id]


async def test_sorgu_bare_bergamalilar_ilceye_gore(session):
    bergama = Person(full_name="Bergamalı Ahmet", district="Bergama")
    izmir = Person(full_name="İzmirli Mehmet", district="İzmir")
    session.add_all([bergama, izmir])
    await session.flush()

    text = "bergamalılar"
    raw = await _make_raw(session, text, 66)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.LIST
    assert result.resolved.district == "bergama"
    assert [r.person.id for r in result.persons] == [bergama.id]


async def test_arama_tek_kelime_isimde_gecen_herkesi_listeler(session):
    yilmaz = Person(full_name="Ahmet Yılmaz")
    kaya = Person(full_name="Ahmet Kaya")
    mehmet = Person(full_name="Mehmet Duman")
    session.add_all([yilmaz, kaya, mehmet])
    await session.flush()

    text = "ahmet"
    raw = await _make_raw(session, text, 67)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.SEARCH
    assert result.resolved.query == "ahmet"
    assert {r.person.id for r in result.persons} == {yilmaz.id, kaya.id}
    await session.refresh(raw)
    assert raw.processed_at is None
    assert raw.transaction_id is None


async def test_arama_eslesme_yoksa_bos_liste(session):
    text = "zzzyokbirisi"
    raw = await _make_raw(session, text, 68)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.SEARCH
    assert result.persons == []


async def test_arama_ile_komutlu_bakiye_sorgusu_karismaz(session, ahmet):
    # "ahmet yılmaz bakiye" iki kelime + komutlu (bare "bakiye" anahtarı) —
    # arama değil bakiye sorgusu olmalı (madde 5'teki DİKKAT notu).
    text = "ahmet yılmaz bakiye"
    raw = await _make_raw(session, text, 69)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.BALANCE
    assert result.resolved.person.id == ahmet.id


# ------------------------------------------------------------------
# Bug (CLAUDE.md > yazım hatası/ek varyasyonu bug'ı, 2026-08): kural
# parser'ın tek kelimelik "arama" yakalayıcısı (_try_single_word_search)
# HER eşleşmeyen kelimeyi bir isim/ilçe araması sanıyordu — bu da parse()
# hiçbir zaman None dönmediği için LLM fallback'in "kişileer",
# "ahmetbeylilier" gibi yazım hatalarında HİÇ devreye girmemesine yol
# açıyordu. Arama sonuçsuz kalınca artık LLM'e bir şans daha veriliyor.


async def test_arama_sonuc_bulamayinca_llme_dusup_dogru_niyete_donusur(session, ahmet, monkeypatch):
    # "kişileer" ("kişiler" yazım hatası): kural parser tek kelime olduğu
    # için kind="search" döner (None DEĞİL), hiç kimseyle eşleşmez. LLM
    # devreye girip list_all niyeti verince bot listeyi göstermeli.
    intent = ParsedIntent(kind="list_all")
    fake = _FakeLLMProvider(intent)
    _mock_llm(monkeypatch, fake)

    text = "kişileer"
    raw = await _make_raw(session, text, 100)
    result = await message_processor.process_raw_message(session, raw, text)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.LIST
    assert [r.person.id for r in result.persons] == [ahmet.id]


async def test_arama_sonuc_bulamayinca_llm_de_cozemezse_orijinal_arama_sonucu_kalir(
    session, monkeypatch
):
    # LLM de bir şey çıkaramazsa (None) kullanıcı deneyimi bozulmamalı —
    # orijinal "eşleşen kişi yok" sonucu aynen kalmalı.
    fake = _FakeLLMProvider(None)
    _mock_llm(monkeypatch, fake)

    text = "zzzyokbirisi"
    raw = await _make_raw(session, text, 101)
    result = await message_processor.process_raw_message(session, raw, text)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.SEARCH
    assert result.persons == []


async def test_arama_sonuc_varsa_llme_hic_gidilmez(session, ahmet, monkeypatch):
    # Gerçek bir isim araması sonuç bulduğunda LLM'e HİÇ gidilmemeli —
    # hızlı arama deneyimi bir ekstra ağ çağrısıyla yavaşlatılmamalı.
    fake = _FakeLLMProvider(ParsedIntent(kind="list_all"))
    _mock_llm(monkeypatch, fake)

    text = "ahmet"
    raw = await _make_raw(session, text, 102)
    result = await message_processor.process_raw_message(session, raw, text)

    assert fake.called is False
    assert result.outcome == ProcessOutcome.SEARCH
    assert [r.person.id for r in result.persons] == [ahmet.id]


async def test_ilce_sorgusu_kimler_var_kalibiyla_llm_uzerinden_calisir(session, monkeypatch):
    # "bergamadan kimler var": kural parser hiçbir kalıba uymadığı için
    # None döner (regex'in kendisi zaten LLM'e düşer, retry mekanizması
    # gerekmez) — LLM'in list_district(bergama) çıkarabildiğini doğrular.
    bergama = Person(full_name="Bergamalı Ahmet", district="Bergama")
    izmir = Person(full_name="İzmirli Mehmet", district="İzmir")
    session.add_all([bergama, izmir])
    await session.flush()

    intent = ParsedIntent(kind="list_district", district="bergama")
    fake = _FakeLLMProvider(intent)
    _mock_llm(monkeypatch, fake)

    text = "bergamadan kimler var"
    raw = await _make_raw(session, text, 103)
    result = await message_processor.process_raw_message(session, raw, text)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.LIST
    assert result.resolved.district == "bergama"
    assert [r.person.id for r in result.persons] == [bergama.id]


# ------------------------------------------------------------------
# Grup 2 (CLAUDE.md > "Bot kayıt akışı"): önceki->güncel bakiye, kısa kayıt
# biçimi, create_person, çoklu kişi + kayıt.


async def test_kayit_onceki_ve_guncel_bakiye_hesaplanir(session, ahmet):
    text1 = "ahmet yılmaz 10000 tl borç yazdım"
    raw1 = await _make_raw(session, text1, 70)
    result1 = await message_processor.process_raw_message(session, raw1, text1)

    assert result1.outcome == ProcessOutcome.RECORDED
    assert result1.balance_before.balance_try == Decimal("0.00")
    assert result1.balance.balance_try == Decimal("10000.00")

    text2 = "ahmet yılmaz 4000 tl ödedi"
    raw2 = await _make_raw(session, text2, 71)
    result2 = await message_processor.process_raw_message(session, raw2, text2)

    assert result2.outcome == ProcessOutcome.RECORDED
    assert result2.balance_before.balance_try == Decimal("10000.00")
    assert result2.balance.balance_try == Decimal("6000.00")


async def test_kisa_kayit_bicimi_borc_olarak_kaydedilir(session, ahmet):
    text = "ahmet yılmaz 30 saman 5000tl"
    raw = await _make_raw(session, text, 72)

    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    assert result.resolved.kind == "debt"
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.kind == TxKind.DEBIT
    assert tx.amount_try == Decimal("5000.00")
    assert len(tx.lines) == 1
    assert tx.lines[0].qty == Decimal("30")


async def test_coklu_kisi_kisa_kayit_bicimiyle_hangisi_sorar(session):
    a = Person(full_name="Ahmet Yılmaz")
    b = Person(full_name="Ahmet Yıldız")
    session.add_all([a, b])
    await session.flush()

    text = "ahmet 30 saman 5000tl"
    raw = await _make_raw(session, text, 73)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    assert result.resolved.kind == "debt"
    candidate_ids = {p.id for p in result.resolved.person_candidates}
    assert {a.id, b.id} == candidate_ids

    # Aday seçilince (bot'ta _finish_pending'in yaptığı gibi) kayıt yapılır
    # VE onay mesajı için önceki/güncel bakiye hesaplanır.
    saman = Product(name="Saman", base_unit="adet")
    session.add(saman)
    await session.flush()
    picked = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind="debt",
        person=a,
        qty=Decimal("30"),
        product=saman,
        amount=Decimal("5000"),
    )
    follow_up = await message_processor.handle_resolved(session, raw, picked, text)

    assert follow_up.outcome == ProcessOutcome.RECORDED
    assert follow_up.resolved.person.id == a.id
    assert follow_up.balance_before.balance_try == Decimal("0.00")
    assert follow_up.balance.balance_try == Decimal("5000.00")


async def test_create_person_yeni_kisi_akisini_baslatir(session):
    text = "ahmet duman kayıt et"
    raw = await _make_raw(session, text, 75)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PERSON_NOT_FOUND
    assert result.resolved.kind == "create_person"
    assert result.resolved.person_name_raw == "ahmet duman"
    assert (await session.execute(select(func.count(Person.id)))).scalar_one() == 0


async def test_create_person_kisi_zaten_varsa_borc_olusturmaz(session, ahmet):
    text = "ahmet yılmaz kayıt et"
    raw = await _make_raw(session, text, 76)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.CREATE_PERSON
    assert result.resolved.person.id == ahmet.id
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0


async def test_create_person_coklu_aday_hangisi_sorar(session):
    a = Person(full_name="Ahmet Yılmaz")
    b = Person(full_name="Ahmet Yıldız")
    session.add_all([a, b])
    await session.flush()

    text = "ahmet yı kayıt et"
    raw = await _make_raw(session, text, 77)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    assert result.resolved.kind == "create_person"
    candidate_ids = {p.id for p in result.resolved.person_candidates}
    assert {a.id, b.id} == candidate_ids


# ------------------------------------------------------------------
# Grup 3 (CLAUDE.md > "Bot kişi silme = arşivleme"): "furkanı sil" gibi bir
# komut hiçbir şeyi burada hemen silmez — kişi netleşince (READY) yalnızca
# onay mesajında gösterilecek bakiye hesaplanır, gerçek arşivleme bot
# tarafında yazarak-onaydan sonra yapılır (bkz. tests/test_bot_archive_flow.py).


async def test_archive_person_kisi_netse_onay_icin_bakiye_hesaplanir(session, ahmet):
    text1 = "ahmet yılmaz 1000 tl borç yazdım"
    raw1 = await _make_raw(session, text1, 80)
    await message_processor.process_raw_message(session, raw1, text1)

    # Çıplak accusative eki ("yılmazı") kasten sökülmez (bkz. name_utils.py
    # > "esma" bug'ı), bu yüzden iki kelimeli isimde tam eşleşme için ekSİZ
    # yazım kullanılır — iki kelimeli girdide fuzzy asla otomatik bağlanmaz.
    text2 = "ahmet yılmaz sil"
    raw2 = await _make_raw(session, text2, 81)
    result = await message_processor.process_raw_message(session, raw2, text2)

    assert result.outcome == ProcessOutcome.ARCHIVE_CONFIRM
    assert result.resolved.kind == "archive_person"
    assert result.resolved.person.id == ahmet.id
    assert result.balance.balance_try == Decimal("1000.00")
    # Hiçbir şey gerçekten arşivlenmedi/silinmedi — yalnızca onay bekleniyor.
    await session.refresh(ahmet)
    assert ahmet.is_active is True
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 1


async def test_archive_and_recreate_kisi_netse_onay_icin_bakiye_hesaplanir(session, ahmet):
    text = "ahmet yılmaz sil yeniden oluştur"
    raw = await _make_raw(session, text, 82)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.ARCHIVE_CONFIRM
    assert result.resolved.kind == "archive_and_recreate"
    assert result.resolved.person.id == ahmet.id
    assert result.balance.balance_try == Decimal("0.00")


async def test_archive_person_coklu_aday_hangisi_sorar(session):
    a = Person(full_name="Furkan Duman")
    b = Person(full_name="Furkan Yılmaz")
    session.add_all([a, b])
    await session.flush()

    text = "furkanı sil"
    raw = await _make_raw(session, text, 83)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    assert result.resolved.kind == "archive_person"
    candidate_ids = {p.id for p in result.resolved.person_candidates}
    assert {a.id, b.id} == candidate_ids

    # Aday seçilince (bot'ta _finish_pending'in yaptığı gibi) yine yalnızca
    # onay bekleyen ARCHIVE_CONFIRM'e gidilir, hiçbir şey hemen arşivlenmez.
    picked = ResolvedIntent(status=ResolutionStatus.READY, kind="archive_person", person=a)
    follow_up = await message_processor.handle_resolved(session, raw, picked, text)
    assert follow_up.outcome == ProcessOutcome.ARCHIVE_CONFIRM
    assert follow_up.resolved.person.id == a.id


async def test_archive_person_bulunamayan_kisi(session):
    text = "hiç yok böyle biri sil"
    raw = await _make_raw(session, text, 84)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PERSON_NOT_FOUND
    assert result.resolved.kind == "archive_person"


# ------------------------------------------------------------------
# Grup 4 (CLAUDE.md > "Silme mesajı + kişi düzenleme"): NET komut (alan+değer
# belli) -> EDIT_PERSON_CONFIRM, BELİRSİZ komut -> EDIT_PERSON_MENU. Gerçek
# güncelleme burada değil, bot tarafında onaydan sonra yapılır.


async def test_edit_person_net_komut_onay_icin_bekletilir(session, ahmet):
    text = "ahmet yılmaz ilçe ahmetbeyler yap"
    raw = await _make_raw(session, text, 90)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.EDIT_PERSON_CONFIRM
    assert result.resolved.person.id == ahmet.id
    assert result.resolved.field_name == "district"
    assert result.resolved.new_value == "ahmetbeyler"
    # Hiçbir şey gerçekten güncellenmedi.
    await session.refresh(ahmet)
    assert ahmet.district is None


async def test_edit_person_belirsiz_komut_menu_ister(session, ahmet):
    text = "ahmet yılmaz düzenle"
    raw = await _make_raw(session, text, 91)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.EDIT_PERSON_MENU
    assert result.resolved.person.id == ahmet.id
    assert result.resolved.field_name is None
    assert result.resolved.new_value is None


async def test_edit_person_yazim_hatali_komut_da_menu_ister(session, ahmet):
    text = "ahmet yılmaz düzenlee"
    raw = await _make_raw(session, text, 92)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.EDIT_PERSON_MENU
    assert result.resolved.person.id == ahmet.id


async def test_edit_person_coklu_aday_hangisi_sorar(session):
    a = Person(full_name="Furkan Duman")
    b = Person(full_name="Furkan Yılmaz")
    session.add_all([a, b])
    await session.flush()

    text = "furkan ilçe bergama yap"
    raw = await _make_raw(session, text, 93)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    assert result.resolved.kind == "edit_person"
    candidate_ids = {p.id for p in result.resolved.person_candidates}
    assert {a.id, b.id} == candidate_ids

    picked = ResolvedIntent(
        status=ResolutionStatus.READY, kind="edit_person", person=a,
        field_name="district", new_value="bergama",
    )
    follow_up = await message_processor.handle_resolved(session, raw, picked, text)
    assert follow_up.outcome == ProcessOutcome.EDIT_PERSON_CONFIRM
    assert follow_up.resolved.person.id == a.id


async def test_edit_person_bulunamayan_kisi(session):
    text = "hiç yok böyle biri düzenle"
    raw = await _make_raw(session, text, 94)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PERSON_NOT_FOUND
    assert result.resolved.kind == "edit_person"


# ------------------------------------------------------------------
# Grup 5 (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)"): hatalı ürün adları
# ("samaan", "saman 15") sessizce yeni ürün olarak açılmamalı — kişi
# netleştikten sonra ürün belirsizse PRODUCT_NEEDS_CONFIRMATION dönmeli,
# hiçbir kayıt/ürün oluşturulmamalı.


async def test_urun_tam_eslesirse_kayit_sormadan_tamamlanir(session, ahmet):
    saman = Product(name="Saman", base_unit="balya")
    session.add(saman)
    await session.flush()

    text = "ahmet yılmaz 20 balya saman aldı 5000 tl borç"
    raw = await _make_raw(session, text, 95)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    tx = await session.get(Transaction, result.transaction_id)
    assert tx.lines[0].product_id == saman.id
    assert (await session.execute(select(func.count(Product.id)))).scalar_one() == 1


async def test_urun_yazim_hatasi_onay_bekletir_kayit_yapmaz(session, ahmet):
    saman = Product(name="Saman", base_unit="balya")
    session.add(saman)
    await session.flush()

    text = "ahmet yılmaz 20 balya samaan aldı 5000 tl borç"
    raw = await _make_raw(session, text, 96)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION
    assert result.resolved.person.id == ahmet.id
    assert result.resolved.product_name_raw == "samaan"
    assert result.resolved.product_suggestion.id == saman.id
    assert result.resolved.product is None

    await session.refresh(raw)
    assert raw.processed_at is None
    assert raw.transaction_id is None
    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0
    assert (await session.execute(select(func.count(Product.id)))).scalar_one() == 1  # yeni ürün açılmadı


async def test_urun_saman_15_rakamla_onay_bekletir(session, ahmet):
    saman = Product(name="Saman", base_unit="balya")
    session.add(saman)
    await session.flush()

    text = "ahmet yılmaz 20 balya saman 15 aldı 5000 tl borç"
    raw = await _make_raw(session, text, 97)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION
    assert result.resolved.product_suggestion.id == saman.id


async def test_urun_hic_benzemezse_sormadan_yeni_urun_kaydedilir(session, ahmet):
    text = "ahmet yılmaz 20 balya tuz aldı 5000 tl borç"
    raw = await _make_raw(session, text, 98)
    result = await message_processor.process_raw_message(session, raw, text)

    assert result.outcome == ProcessOutcome.RECORDED
    tx = await session.get(Transaction, result.transaction_id)
    urun = await session.get(Product, tx.lines[0].product_id)
    assert urun.name == "tuz"


async def test_urun_onerisi_evet_ile_mevcut_uruna_baglanir(session, ahmet):
    # Bot'ta "Evet" butonuna basılınca (kişi zaten netleşmiş, öneri
    # bağlanır) — handle_resolved doğrudan öneri ürünüyle çağrılır (bkz.
    # app/bot/main.py > _handle_product_confirm'in yaptığı gibi).
    saman = Product(name="Saman", base_unit="balya")
    session.add(saman)
    await session.flush()

    text = "ahmet yılmaz 20 balya samaan aldı 5000 tl borç"
    raw = await _make_raw(session, text, 99)
    result = await message_processor.process_raw_message(session, raw, text)
    assert result.outcome == ProcessOutcome.PRODUCT_NEEDS_CONFIRMATION

    picked = ResolvedIntent(
        status=ResolutionStatus.READY, kind="debt", person=ahmet,
        qty=Decimal("20"), unit="balya", product=saman, amount=Decimal("5000"),
    )
    follow_up = await message_processor.handle_resolved(session, raw, picked, text, source="rule")
    assert follow_up.outcome == ProcessOutcome.RECORDED
    tx = await session.get(Transaction, follow_up.transaction_id)
    assert tx.lines[0].product_id == saman.id
    assert (await session.execute(select(func.count(Product.id)))).scalar_one() == 1
