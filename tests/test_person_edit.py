import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Person
from app.services import person_archive, person_edit
from app.services.person_edit import PersonEditError


async def _person(session, full_name="Ahmet Yılmaz", **kw):
    p = Person(full_name=full_name, **kw)
    session.add(p)
    await session.flush()
    return p


async def test_district_guncellenir(session):
    ahmet = await _person(session, district="Bergama")

    updated = await person_edit.update_person_field(
        session, ahmet.id, "district", "Ahmetbeyler", actor="furkan"
    )

    assert updated.district == "Ahmetbeyler"
    await session.refresh(ahmet)
    assert ahmet.district == "Ahmetbeyler"


async def test_full_name_guncellenir(session):
    ahmet = await _person(session, full_name="Mehmet")

    await person_edit.update_person_field(session, ahmet.id, "full_name", "Akif", actor="furkan")

    await session.refresh(ahmet)
    assert ahmet.full_name == "Akif"


async def test_izin_verilmeyen_alan_reddedilir(session):
    ahmet = await _person(session)

    with pytest.raises(PersonEditError, match="Düzenlenemeyen alan"):
        await person_edit.update_person_field(session, ahmet.id, "id", "999", actor="furkan")

    with pytest.raises(PersonEditError, match="Düzenlenemeyen alan"):
        await person_edit.update_person_field(
            session, ahmet.id, "is_active", "false", actor="furkan"
        )


async def test_olmayan_kisi_reddedilir(session):
    with pytest.raises(PersonEditError, match="bulunamadı"):
        await person_edit.update_person_field(session, 999999, "phone", "5551112233", actor="furkan")


async def test_arsivlenmis_kisi_duzenlenemez(session):
    ahmet = await _person(session)
    await person_archive.archive_person(session, ahmet.id, archived_by="furkan", reason="r")

    with pytest.raises(PersonEditError, match="bulunamadı"):
        await person_edit.update_person_field(session, ahmet.id, "phone", "5551112233", actor="furkan")


async def test_degisiklik_audit_loga_eski_ve_yeni_degerle_yazilir(session):
    ahmet = await _person(session, phone="5550000000")

    await person_edit.update_person_field(session, ahmet.id, "phone", "5551112233", actor="furkan")

    log = (
        await session.execute(
            select(AuditLog).where(AuditLog.action == "edit_person", AuditLog.entity_id == str(ahmet.id))
        )
    ).scalar_one()
    assert log.actor == "furkan"
    assert log.entity == "persons"
    assert log.before == {"field": "phone", "value": "5550000000"}
    assert log.after == {"field": "phone", "value": "5551112233"}


async def test_her_alan_ayri_ayri_guncellenebilir(session):
    ahmet = await _person(session)

    for field, value in [
        ("phone", "5551112233"),
        ("city", "İzmir"),
        ("district", "Bergama"),
        ("address", "Örnek Mah. No:1"),
        ("note", "Düzenli müşteri"),
    ]:
        await person_edit.update_person_field(session, ahmet.id, field, value, actor="furkan")

    await session.refresh(ahmet)
    assert ahmet.phone == "5551112233"
    assert ahmet.city == "İzmir"
    assert ahmet.district == "Bergama"
    assert ahmet.address == "Örnek Mah. No:1"
    assert ahmet.note == "Düzenli müşteri"

    count = (
        await session.execute(select(func.count(AuditLog.id)).where(AuditLog.action == "edit_person"))
    ).scalar_one()
    assert count == 5
