"""Kişi arşivleme servisi (CLAUDE.md > "Bot kişi silme = arşivleme").

Hiçbir şey gerçekten silinmez. "Sil" = arşivle: kişi kartı + o anki bakiye +
TÜM işlemlerinin (durumu ne olursa olsun) snapshot'ı archived_persons'a
yazılır, sonra persons.is_active=false yapılır (satır DB'de kalır, defterde/
aramada görünmez). transactions'a dokunulmaz — append-only kuralı burada da
geçerli, bu fonksiyon yalnızca snapshot kopyalar.

Bu modül yalnızca altyapıdır; Telegram bot komutuna henüz bağlanmadı.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ArchivedPerson, AuditLog, Person, Transaction, TransactionLine
from app.services import ledger


class PersonArchiveError(Exception):
    """İş kuralı ihlali."""


async def archive_person(
    session: AsyncSession, person_id: int, archived_by: str, reason: str
) -> ArchivedPerson:
    """Kişiyi arşive taşır. Kişi kartı + bakiye + tüm işlemler snapshot'ı
    archived_persons'a yazılır, sonra kişi pasifleştirilir (is_active=false).
    transactions'taki gerçek kayıtlar SİLİNMEZ, yalnızca kopyalanır."""
    person = await session.get(Person, person_id)
    if person is None:
        raise PersonArchiveError(f"Kişi bulunamadı: {person_id}")
    if not person.is_active:
        raise PersonArchiveError("Kişi zaten arşivlenmiş")

    balance = await ledger.balance_of(session, person_id)

    stmt = (
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.product))
        .where(Transaction.person_id == person_id)
        .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
    )
    txs = list((await session.execute(stmt)).scalars())

    transactions_snapshot = [
        {
            "id": t.id,
            "kind": t.kind.value,
            "status": t.status.value,
            "occurred_at": t.occurred_at.isoformat(),
            "amount_try": str(t.amount_try),
            "note": t.note,
            "lines": [
                {
                    "product_name": li.product.name,
                    "qty": str(li.qty),
                    "unit": li.unit,
                    "unit_price": str(li.unit_price),
                    "line_total": str(li.line_total),
                }
                for li in t.lines
            ],
        }
        for t in txs
    ]

    archived = ArchivedPerson(
        original_person_id=person.id,
        full_name=person.full_name,
        phone=person.phone,
        city=person.city,
        district=person.district,
        person_created_at=person.created_at,
        balance_try=balance.balance_try,
        transactions_snapshot=transactions_snapshot,
        archived_by=archived_by,
        archive_reason=reason,
    )
    session.add(archived)

    person.is_active = False
    await session.flush()

    session.add(
        AuditLog(
            actor=archived_by,
            action="archive_person",
            entity="persons",
            entity_id=str(person_id),
            before={"id": person.id, "full_name": person.full_name},
            after={"archived": True, "reason": reason, "balance_try": str(balance.balance_try)},
        )
    )
    await session.flush()
    return archived
