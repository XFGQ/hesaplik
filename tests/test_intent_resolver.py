from decimal import Decimal

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
