from decimal import Decimal

import pytest
import pytest_asyncio

from app.models import Person
from app.services.intent_resolver import ResolutionStatus, resolve
from app.services.parser import ParsedIntent


@pytest_asyncio.fixture(loop_scope="session")
async def two_ahmets(session):
    a = Person(full_name="Ahmet Yılmaz")
    b = Person(full_name="Ahmet Yıldız")
    session.add_all([a, b])
    await session.flush()
    return a, b


@pytest_asyncio.fixture(loop_scope="session")
async def furkan_duman(session):
    p = Person(full_name="Furkan Duman")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def two_furkans(session):
    a = Person(full_name="Furkan Duman")
    b = Person(full_name="Furkan Yılmaz")
    session.add_all([a, b])
    await session.flush()
    return a, b


@pytest_asyncio.fixture(loop_scope="session")
async def tek_ahmet(session):
    p = Person(full_name="Ahmet")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def ali_veli(session):
    p = Person(full_name="Ali Veli")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def tek_mehmet(session):
    p = Person(full_name="Mehmet")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def esma(session):
    p = Person(full_name="Esma")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def mehmet_ve_mehtap(session):
    mehmet = Person(full_name="Mehmet")
    mehtap = Person(full_name="Mehtap")
    session.add_all([mehmet, mehtap])
    await session.flush()
    return mehmet, mehtap


async def test_birebir_eslesme_kullanilir(session, two_ahmets):
    a, _ = two_ahmets
    intent = ParsedIntent(kind="debt", person_name="ahmet yılmaz", amount=Decimal("100"))
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == a.id


async def test_birden_fazla_aday_onay_ister(session, two_ahmets):
    intent = ParsedIntent(kind="debt", person_name="ahmet yı", amount=Decimal("100"))
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    assert len(resolved.person_candidates) >= 2


async def test_kisi_bulunamadi(session):
    intent = ParsedIntent(kind="debt", person_name="hic yok boyle biri", amount=Decimal("100"))
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.PERSON_NOT_FOUND
    assert resolved.person_name_raw == "hic yok boyle biri"


# --------------------------------------------------------------- isim eşleştirme + öngörücü
# teyit (CLAUDE.md > "İsim eşleştirme + öngörücü teyit", 2026-08): pg_trgm
# HİÇBİR aday bulamadığında (ör. "doman", "Furkan Duman"a karşı ~0.20
# benzerlik — SIMILARITY_CANDIDATE 0.35'in çok altında) son çare LLM'e
# danışılır. fake_llm.name_match LLM'in "eslesen_kisi" alanını taklit eder.


async def test_llm_onerisi_tek_aday_olarak_sunulur(session, furkan_duman, fake_llm):
    fake_llm.name_match = "Furkan Duman"
    intent = ParsedIntent(kind="debt", person_name="doman", amount=Decimal("100"))
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    assert len(resolved.person_candidates) == 1
    assert resolved.person_candidates[0].id == furkan_duman.id


async def test_llm_onerisi_otomatik_baglanmaz_sormadan_kaydetmez(session, furkan_duman, fake_llm):
    # LLM doğru kişiyi bulsa bile READY'ye hiç düşmemeli — kullanıcı yine
    # onaylamalı (NEEDS_CONFIRMATION), asla sormadan direkt kayıt olmamalı.
    fake_llm.name_match = "Furkan Duman"
    intent = ParsedIntent(kind="debt", person_name="doman", amount=Decimal("100"))
    resolved = await resolve(session, intent)
    assert resolved.status != ResolutionStatus.READY


async def test_llm_uydurulmus_isim_reddedilir(session, furkan_duman, fake_llm):
    # LLM kayıtlı listede OLMAYAN bir isim döndürürse (uydurma/halüsinasyon)
    # asla güvenilmez — mevcut "kişi bulunamadı" davranışı aynen sürer.
    fake_llm.name_match = "Hiç Kayıtlı Olmayan Biri"
    intent = ParsedIntent(kind="debt", person_name="doman", amount=Decimal("100"))
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.PERSON_NOT_FOUND


async def test_llm_hicbir_oneri_bulamazsa_kisi_bulunamadi(session, furkan_duman, fake_llm):
    fake_llm.name_match = None
    intent = ParsedIntent(kind="debt", person_name="doman", amount=Decimal("100"))
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.PERSON_NOT_FOUND


async def test_llm_net_eslesmede_hic_cagirilmaz(session, two_ahmets, fake_llm):
    # Net (birebir) eşleşmede pg_trgm'e bile gerek yok — LLM'e hiç
    # danışılmamalı (hızlı yol etkilenmemeli).
    intent = ParsedIntent(kind="debt", person_name="ahmet yılmaz", amount=Decimal("100"))
    await resolve(session, intent)
    assert fake_llm.chat_json_calls == []


async def test_llm_pgtrgm_aday_varken_cagirilmaz(session, two_ahmets, fake_llm):
    # pg_trgm zaten aday bulduysa (ör. "ahmet yı") LLM'e hiç danışılmamalı —
    # yalnızca SIFIR aday durumunda son çare devreye girer.
    intent = ParsedIntent(kind="debt", person_name="ahmet yı", amount=Decimal("100"))
    await resolve(session, intent)
    assert fake_llm.chat_json_calls == []


async def test_none_intent_anlasilmadi(session):
    resolved = await resolve(session, None)
    assert resolved.status == ResolutionStatus.UNRECOGNIZED


async def test_tutar_yoksa_anlasilmadi(session, two_ahmets):
    intent = ParsedIntent(kind="debt", person_name="ahmet yılmaz", amount=None)
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.UNRECOGNIZED


async def test_urun_hazir_durumda_otomatik_olusur(session, two_ahmets):
    intent = ParsedIntent(
        kind="debt", person_name="ahmet yılmaz", qty=Decimal("20"),
        unit="balya", product="saman", amount=Decimal("15000"),
    )
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.READY
    assert resolved.product is not None
    assert resolved.product.name == "saman"
    assert resolved.product.base_unit == "balya"


async def test_belirsizken_urun_olusturulmaz(session):
    intent = ParsedIntent(
        kind="debt", person_name="hic yok boyle biri", qty=Decimal("1"),
        unit="adet", product="hiç-bilinmeyen-ürün-xyz", amount=Decimal("10"),
    )
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.PERSON_NOT_FOUND
    assert resolved.product is None
    assert resolved.product_name_raw == "hiç-bilinmeyen-ürün-xyz"


async def test_bakiye_sorgusunda_tutar_gerekmez(session, two_ahmets):
    a, _ = two_ahmets
    intent = ParsedIntent(kind="balance_query", person_name="ahmet yılmaz")
    resolved = await resolve(session, intent)
    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == a.id


# ------------------------------------------------------------------
# Kişi eşleştirme güvenliği (CLAUDE.md 2026-07-25): "furkan yılmaz" yazılınca
# yalnızca "furkan duman" varken sisteme otomatik ona yazılmamalı. Soyad
# ayırt edicidir; iki kelimeli girdi fuzzy eşleşmeyle asla otomatik bağlanmaz.

async def test_farkli_soyad_otomatik_baglanmaz(session, furkan_duman):
    intent = ParsedIntent(kind="debt", person_name="furkan yılmaz", amount=Decimal("500"))
    resolved = await resolve(session, intent)

    assert resolved.status in (
        ResolutionStatus.PERSON_NOT_FOUND,
        ResolutionStatus.NEEDS_CONFIRMATION,
    )
    assert resolved.person is None
    if resolved.status == ResolutionStatus.NEEDS_CONFIRMATION:
        # Aday olarak sunulabilir ama otomatik seçilmiş olamaz.
        assert furkan_duman.id in {p.id for p in resolved.person_candidates}


async def test_tek_kelime_iki_furkan_onay_ister(session, two_furkans):
    duman, yilmaz = two_furkans
    intent = ParsedIntent(kind="debt", person_name="furkan", amount=Decimal("500"))
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in resolved.person_candidates}
    assert duman.id in candidate_ids
    assert yilmaz.id in candidate_ids


async def test_iki_furkandan_birebir_soyadli_net_eslesir(session, two_furkans):
    duman, _yilmaz = two_furkans
    intent = ParsedIntent(kind="debt", person_name="furkan duman", amount=Decimal("500"))
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == duman.id


async def test_bakiye_sorgusunda_da_tek_kelime_iki_furkan_onay_ister(session, two_furkans):
    # Kişi eşleştirme güvenliği yalnızca borç/tahsilat değil, bakiye
    # sorgusu için de geçerli olmalı: "furkan borcunu söyle" iki Furkan
    # varken hangisi olduğunu sormalı, otomatik birine yazmamalı/göstermemeli.
    duman, yilmaz = two_furkans
    intent = ParsedIntent(kind="balance_query", person_name="furkan")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in resolved.person_candidates}
    assert duman.id in candidate_ids
    assert yilmaz.id in candidate_ids


# ------------------------------------------------------------------
# KRİTİK — LLM isim bozuyor (CLAUDE.md 2026-07-27): Türkçe ek temizleme
# LLM'e bırakıldığında "mehmetten" -> "mehtap" gibi harf uydurmalar ve
# "ahmetin" gibi eki temizlenemeyen isimler kişiyi bulamıyordu. Artık ek
# temizleme koddadır (name_utils.strip_turkish_suffix), LLM/regex ne
# döndürürse döndürsün burada uygulanır.

async def test_mehmetten_mehmete_eslesir_mehtaba_asla(session, mehmet_ve_mehtap):
    mehmet, mehtap = mehmet_ve_mehtap
    intent = ParsedIntent(kind="payment", person_name="mehmetten", amount=Decimal("5000"))
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == mehmet.id
    assert resolved.person.id != mehtap.id


@pytest.mark.parametrize("raw_name", ["ahmetin", "ahmet in", "ahmete", "ahmetten"])
async def test_ahmet_ek_varyantlari_tutarli_sekilde_ayni_kisiye_isaret_eder(
    session, tek_ahmet, raw_name
):
    intent = ParsedIntent(kind="balance_query", person_name=raw_name)
    resolved = await resolve(session, intent)

    assert resolved.status in (ResolutionStatus.READY, ResolutionStatus.NEEDS_CONFIRMATION), raw_name
    if resolved.status == ResolutionStatus.READY:
        assert resolved.person.id == tek_ahmet.id, raw_name
    else:
        candidate_ids = {p.id for p in resolved.person_candidates}
        assert candidate_ids == {tek_ahmet.id}, raw_name


async def test_ek_temizleme_sonrasi_da_coklu_aday_hangisi_diye_sorar(session, two_ahmets):
    # "ahmetin" -> "ahmet"e soyulur ama iki Ahmet varsa yine otomatik
    # birine bağlanmaz, ikisi de aday olarak sunulur.
    a, b = two_ahmets
    intent = ParsedIntent(kind="balance_query", person_name="ahmetin")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in resolved.person_candidates}
    assert a.id in candidate_ids
    assert b.id in candidate_ids


# ------------------------------------------------------------------
# LLM serbest cümleden kişi adını yanlış çıkarıyor (CLAUDE.md 2026-07-28
# bug'ı): "ahmetin hesabının dökümünü çıkar" -> LLM kişi adını "Ahmetin
# Hesabının" olarak çıkarmıştı ("hesabının" bağlam kelimesi isme
# katılmıştı). Artık strip_turkish_suffix bu bağlam kelimelerini ayıklar
# (bkz. name_utils.strip_context_words), kaynak regex olsun LLM olsun fark
# etmez — ikisi de aynı fonksiyondan geçer.

async def test_baglam_kelimesi_katilan_isim_iki_ahmet_varsa_onay_ister(session, two_ahmets):
    a, b = two_ahmets
    # LLM'in ürettiği ham (bağlam kelimesi katılmış) kişi adı.
    intent = ParsedIntent(kind="report_person", person_name="Ahmetin Hesabının")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in resolved.person_candidates}
    assert a.id in candidate_ids
    assert b.id in candidate_ids


async def test_baglam_kelimesi_katilan_isim_tek_kisiyle_net_eslesir(session, tek_mehmet):
    intent = ParsedIntent(kind="balance_query", person_name="mehmetin durumu")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == tek_mehmet.id


async def test_baglam_kelimesi_ayiklaninca_iki_kelimeli_isim_korunur(session, ali_veli):
    # "ekstresi" ayıklanır ama "ali veli" iki kelimeli isim olarak kalmalı
    # (bkz. name_utils.strip_context_words); soyaddaki iyelik eki
    # (velinin -> ?) tam çözülemese de (bkz. name_utils modül notu: tamponlu/
    # tamponsuz iyelik ayrımı bazen belirsiz kalır) isim İKİ KELİME olarak
    # korunduğu ve gerçek Ali Veli'ye yeterince yakın kaldığı için sistem
    # ya doğrudan eşleşir ya da onu aday olarak sunar — sessizce başka
    # birine ya da "kişi yok"a düşmez.
    intent = ParsedIntent(kind="report_person", person_name="ali velinin ekstresi")
    resolved = await resolve(session, intent)

    assert resolved.status in (ResolutionStatus.READY, ResolutionStatus.NEEDS_CONFIRMATION)
    if resolved.status == ResolutionStatus.READY:
        assert resolved.person.id == ali_veli.id
    else:
        assert ali_veli.id in {p.id for p in resolved.person_candidates}


async def test_baglam_kelimesi_olmayan_normal_isim_bozulmaz(session, two_ahmets):
    a, b = two_ahmets
    intent = ParsedIntent(kind="debt", person_name="ahmet yılmaz", amount=Decimal("100"))
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == a.id


# ------------------------------------------------------------------
# Kişi bilgisi (CLAUDE.md > "DÜZELTME — 'bilgi ver' belirsiz, SOR"):
# info_menu/person_contact de balance_query gibi kişi gerektirir, tutar
# gerektirmez; hitap kelimesi ("esma abla") burada da ayıklanmalı.

async def test_info_menu_tutar_gerekmez(session, esma):
    intent = ParsedIntent(kind="info_menu", person_name="esma")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == esma.id


async def test_info_menu_hitapli_isimle_kisiye_net_eslesir(session, esma):
    # "esma abla" -> hitap ayıklanır -> "esma" -> birebir eşleşir. (Tamponsuz
    # yönelme eki bug'ı düzeltildiğinden "esma" artık "esm"e kesilmiyor —
    # bkz. CLAUDE.md > "KRİTİK BUG" ve test_name_utils.py.)
    intent = ParsedIntent(kind="info_menu", person_name="esma abla")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == esma.id


async def test_person_contact_tutar_gerekmez(session, esma):
    intent = ParsedIntent(kind="person_contact", person_name="esma")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == esma.id


async def test_info_menu_belirsiz_kisi_onay_ister(session, two_ahmets):
    a, b = two_ahmets
    intent = ParsedIntent(kind="info_menu", person_name="ahmet")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in resolved.person_candidates}
    assert a.id in candidate_ids
    assert b.id in candidate_ids


# ------------------------------------------------------------------
# Kişi silme/arşivleme (CLAUDE.md > "Bot kişi silme = arşivleme — Grup 3"):
# archive_person/archive_and_recreate de balance_query/info_menu gibi kişi
# gerektirir, tutar gerektirmez, ve AYNI kişi eşleştirme güvenlik akışından
# geçer (çoklu aday -> "hangisi?" sor, otomatik seçme yok).


async def test_archive_person_tutar_gerekmez(session, furkan_duman):
    intent = ParsedIntent(kind="archive_person", person_name="furkan duman")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == furkan_duman.id


async def test_archive_person_coklu_aday_hangisi_sorar(session, two_furkans):
    duman, yilmaz = two_furkans
    intent = ParsedIntent(kind="archive_person", person_name="furkan")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in resolved.person_candidates}
    assert duman.id in candidate_ids
    assert yilmaz.id in candidate_ids


async def test_archive_person_bulunamayan_kisi(session):
    intent = ParsedIntent(kind="archive_person", person_name="hic yok boyle biri")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.PERSON_NOT_FOUND
    assert resolved.person_name_raw == "hic yok boyle biri"


async def test_archive_person_ek_ile_yazilan_isim_de_eslesir(session, furkan_duman):
    # "furkanı sil" -> parser person_name = "furkanı" (ham, ek soyulmadan
    # bırakılır — mevcut mimariyle tutarlı, bkz. test_parser.py). Tek kelime
    # + soyadlı bir tam isme karşı fuzzy skor 0.7 eşiğini her zaman aşmayabilir
    # (soyad ayırt edicidir prensibiyle tutarlı, bkz. diğer ek varyantı
    # testleri) — READY ya da tek adaylı NEEDS_CONFIRMATION kabul edilir,
    # ikisinde de kişi asla başka birine/hiç kimseye kaçmamalı.
    intent = ParsedIntent(kind="archive_person", person_name="furkanı")
    resolved = await resolve(session, intent)

    assert resolved.status in (ResolutionStatus.READY, ResolutionStatus.NEEDS_CONFIRMATION)
    if resolved.status == ResolutionStatus.READY:
        assert resolved.person.id == furkan_duman.id
    else:
        candidate_ids = {p.id for p in resolved.person_candidates}
        assert candidate_ids == {furkan_duman.id}


async def test_archive_and_recreate_de_ayni_guvenlikten_gecer(session, two_furkans):
    duman, yilmaz = two_furkans
    intent = ParsedIntent(kind="archive_and_recreate", person_name="furkan")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in resolved.person_candidates}
    assert duman.id in candidate_ids
    assert yilmaz.id in candidate_ids


# ------------------------------------------------------------------
# Kişi düzenleme (CLAUDE.md > "Silme mesajı + kişi düzenleme — Grup 4"):
# edit_person de tutar gerektirmez ve AYNI kişi eşleştirme güvenlik
# akışından geçer (çoklu aday -> "hangisi?" sor).


async def test_edit_person_tutar_gerekmez_ve_alan_deger_tasinir(session, furkan_duman):
    intent = ParsedIntent(
        kind="edit_person", person_name="furkan duman", field="district", new_value="ahmetbeyler"
    )
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.person.id == furkan_duman.id
    assert resolved.field_name == "district"
    assert resolved.new_value == "ahmetbeyler"


async def test_edit_person_belirsiz_menu_field_none_tasinir(session, furkan_duman):
    intent = ParsedIntent(kind="edit_person", person_name="furkan duman", field=None, new_value=None)
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.field_name is None
    assert resolved.new_value is None


async def test_edit_person_coklu_aday_hangisi_sorar(session, two_furkans):
    duman, yilmaz = two_furkans
    intent = ParsedIntent(kind="edit_person", person_name="furkan", field="phone", new_value="555")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    candidate_ids = {p.id for p in resolved.person_candidates}
    assert duman.id in candidate_ids
    assert yilmaz.id in candidate_ids
    # Aday seçimi sonrası akışın alan/değeri kaybetmemesi için pending'e
    # taşınabilmesi gerekir.
    assert resolved.field_name == "phone"
    assert resolved.new_value == "555"


async def test_edit_person_bulunamayan_kisi(session):
    intent = ParsedIntent(kind="edit_person", person_name="hic yok boyle biri", field="city", new_value="izmir")
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.PERSON_NOT_FOUND
    assert resolved.person_name_raw == "hic yok boyle biri"


# ------------------------------------------------------------------
# Ürün yazım düzeltme (fuzzy) (CLAUDE.md > "Ürün yazım düzeltme (fuzzy)",
# Grup 5): kişi netleştikten sonra ürün adı bulanıksa (mevcut bir ürüne
# yakınsa) resolve() otomatik bağlamaz/oluşturmaz, PRODUCT_NEEDS_CONFIRMATION
# döner — bot Evet/Hayır yeni ürün/İptal sormalı.


@pytest_asyncio.fixture(loop_scope="session")
async def saman(session):
    from app.models import Product

    p = Product(name="Saman", base_unit="balya")
    session.add(p)
    await session.flush()
    return p


async def test_urun_tam_eslesirse_dogrudan_baglanir(session, furkan_duman, saman):
    intent = ParsedIntent(
        kind="debt", person_name="furkan duman", qty=Decimal("20"), product="saman", amount=Decimal("5000"),
    )
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.product.id == saman.id
    assert resolved.product_suggestion is None


async def test_urun_yaziminda_hata_varsa_onay_ister(session, furkan_duman, saman):
    intent = ParsedIntent(
        kind="debt", person_name="furkan duman", qty=Decimal("20"), product="samaan", amount=Decimal("5000"),
    )
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.PRODUCT_NEEDS_CONFIRMATION
    assert resolved.person.id == furkan_duman.id
    assert resolved.product is None
    assert resolved.product_suggestion.id == saman.id
    assert resolved.product_name_raw == "samaan"
    # Onay bekleniyor, kayıt için gereken diğer alanlar kaybolmamalı.
    assert resolved.qty == Decimal("20")
    assert resolved.amount == Decimal("5000")


async def test_urun_hic_benzemiyorsa_sormadan_yeni_sayilir(session, furkan_duman):
    intent = ParsedIntent(
        kind="debt", person_name="furkan duman", qty=Decimal("5"), product="tuz", amount=Decimal("500"),
    )
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.READY
    assert resolved.product is not None
    assert resolved.product.name == "tuz"
    assert resolved.product_suggestion is None


async def test_urun_belirsizken_kisi_de_coklu_ise_once_kisi_sorulur(session, two_furkans, saman):
    # Kişi netleşmeden ürün hiç kontrol edilmez (CLAUDE.md > "Ürün: yalnızca
    # kişi netleşince READY çağrılır") — çoklu aday varsa önce o sorulmalı.
    intent = ParsedIntent(
        kind="debt", person_name="furkan", qty=Decimal("20"), product="samaan", amount=Decimal("5000"),
    )
    resolved = await resolve(session, intent)

    assert resolved.status == ResolutionStatus.NEEDS_CONFIRMATION
    assert resolved.product_suggestion is None
