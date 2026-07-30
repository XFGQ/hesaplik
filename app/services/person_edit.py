"""Kişi bilgisi düzenleme (CLAUDE.md > "Silme mesajı + kişi düzenleme —
Grup 4"). Yalnızca izin verilen alanlar güncellenebilir; her değişiklik
audit_log'a (kim, alan, eski->yeni) yazılır. Bakiye/işlem etkilenmez —
bu yalnızca kişi kartındaki bilgileri günceller (parasal bir hareket değil,
append-only ledger kuralına dokunmaz).
"""

from __future__ import annotations

from app.models import AuditLog, Person

ALLOWED_FIELDS = {"full_name", "phone", "city", "district", "address", "note"}


class PersonEditError(Exception):
    """İş kuralı ihlali (izin verilmeyen alan, kişi bulunamadı vb.)."""


async def update_person_field(session, person_id: int, field: str, value: str, actor: str) -> Person:
    """`field` ALLOWED_FIELDS dışındaysa reddedilir. Kişi bulunamazsa ya da
    pasifse (arşivlenmişse) reddedilir. Değişiklik audit_log'a eski/yeni
    değerle birlikte yazılır."""
    if field not in ALLOWED_FIELDS:
        raise PersonEditError(f"Düzenlenemeyen alan: {field}")

    person = await session.get(Person, person_id)
    if person is None or not person.is_active:
        raise PersonEditError(f"Kişi bulunamadı: {person_id}")

    old_value = getattr(person, field)
    setattr(person, field, value)
    await session.flush()

    session.add(
        AuditLog(
            actor=actor,
            action="edit_person",
            entity="persons",
            entity_id=str(person_id),
            before={"field": field, "value": old_value},
            after={"field": field, "value": value},
        )
    )
    await session.flush()
    return person
