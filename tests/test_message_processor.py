from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select

from app.models import Person, RawMessage, Transaction, TxKind, TxSource
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
    monkeypatch.setattr(llm_provider, "get_provider", lambda: fake)

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
    monkeypatch.setattr(llm_provider, "get_provider", lambda: fake)

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
    monkeypatch.setattr(llm_provider, "get_provider", lambda: fake)

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
    monkeypatch.setattr(llm_provider, "get_provider", lambda: fake)

    raw = await _make_raw(session, _LLM_ANLAMSIZ_METIN, 42)
    result = await message_processor.process_raw_message(session, raw, _LLM_ANLAMSIZ_METIN)

    assert result.outcome == ProcessOutcome.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in result.resolved.person_candidates}
    assert {a.id, b.id} == candidate_ids


async def test_llm_erisilemezse_anlasilamadi_doner(session, monkeypatch):
    # LLM None dönerse (bağlantı hatası/timeout/geçersiz yanıt) sistem
    # çökmemeli, mevcut "anlayamadım" (UNRECOGNIZED) davranışına düşmeli.
    fake = _FakeLLMProvider(None)
    monkeypatch.setattr(llm_provider, "get_provider", lambda: fake)

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
    monkeypatch.setattr(llm_provider, "get_provider", lambda: fake)

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
    monkeypatch.setattr(llm_provider, "get_provider", lambda: fake)

    raw = await _make_raw(session, text, 57)
    result = await message_processor.process_raw_message(session, raw, text)

    assert fake.called is True
    assert result.outcome == ProcessOutcome.REPORT_PERSON
    assert result.resolved.person.id == ahmet.id
    assert result.report_pdf.startswith(b"%PDF")


async def test_llm_kapaliyken_kural_parser_cozemezse_hic_cagrilmaz(session, monkeypatch):
    # LLM_PROVIDER=none iken get_provider() None döner, LLM'e hiç gidilmez
    # — mevcut davranış aynen korunur. Gerçek ortamın .env'i (yerelde
    # Ollama açık olabilir) burada önemli değil; get_provider() doğrudan
    # devre dışı bırakılarak test bu duruma bağımlı olmaktan çıkarılıyor.
    monkeypatch.setattr(llm_provider, "get_provider", lambda: None)

    raw = await _make_raw(session, _LLM_ANLAMSIZ_METIN, 44)
    result = await message_processor.process_raw_message(session, raw, _LLM_ANLAMSIZ_METIN)

    assert result.outcome == ProcessOutcome.UNRECOGNIZED
