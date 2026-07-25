"""Kişi listeleme/filtreleme sorguları.

Bot (Telegram sorgu komutları, CLAUDE.md > "Telegram sorgu komutları") ve
web API aynı fonksiyonu kullanır — bakiye ve açık kalem mantığı burada tek
yerde, `ledger.balance_of` ile aynı SUM prensibiyle ama toplu (N+1 yok).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person, Product, Transaction, TransactionLine, TxKind, TxStatus
from app.services.catalog import normalize

SCOPES = {"all", "debtors", "creditors"}


@dataclass(slots=True)
class PersonBalanceRow:
    person: Person
    balance_try: Decimal
    items: list[tuple[str, Decimal, str]] = field(default_factory=list)
    last_activity: datetime | None = None


async def list_persons_with_balance(
    session: AsyncSession,
    scope: str = "all",
    district: str | None = None,
    q: str | None = None,
    order: str = "balance_desc",
) -> list[PersonBalanceRow]:
    """Kişi + bakiye + açık kalemler.

    scope: "all" | "debtors" (bakiye > 0) | "creditors" (bakiye < 0).
    district: verilirse yalnızca o ilçedeki kişiler — eşleşme normalize
      edilir (küçük harf, Türkçe İ/I, boşluk), SQL ILIKE Türkçe harflerde
      güvenilir olmadığı için Python tarafında karşılaştırılır.
    order: "balance_desc" (varsayılan, en çok borçlu üstte) | "name".
    """
    if scope not in SCOPES:
        raise ValueError(f"Bilinmeyen filtre: {scope}")

    sign = case((Transaction.kind == TxKind.DEBIT, 1), else_=-1)
    balance_expr = func.coalesce(func.sum(Transaction.amount_try * sign), 0)

    stmt = (
        select(
            Person,
            balance_expr.label("balance_try"),
            func.max(Transaction.occurred_at).label("last_activity"),
        )
        .outerjoin(
            Transaction,
            (Transaction.person_id == Person.id) & (Transaction.status == TxStatus.CONFIRMED),
        )
        .where(Person.is_active.is_(True))
        .group_by(Person.id)
    )

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Person.full_name.ilike(like) | Person.phone.ilike(like))

    if scope == "debtors":
        stmt = stmt.having(balance_expr > 0)
    elif scope == "creditors":
        stmt = stmt.having(balance_expr < 0)

    stmt = stmt.order_by(Person.full_name if order == "name" else balance_expr.desc())

    rows = (await session.execute(stmt)).all()

    if district:
        key = normalize(" ".join(district.split()))
        rows = [r for r in rows if r[0].district and normalize(r[0].district) == key]

    if not rows:
        return []

    items_map = await _open_items_map(session, [r[0].id for r in rows])

    return [
        PersonBalanceRow(
            person=p, balance_try=Decimal(balance), items=items_map.get(p.id, []), last_activity=last
        )
        for p, balance, last in rows
    ]


async def _open_items_map(
    session: AsyncSession, person_ids: list[int]
) -> dict[int, list[tuple[str, Decimal, str]]]:
    """Kişi başına açık kalemler. N+1 sorgusu yok, tek sorgu."""
    sign = case((Transaction.kind == TxKind.DEBIT, 1), else_=-1)
    qty = func.sum(TransactionLine.qty * sign)
    stmt = (
        select(Transaction.person_id, Product.name, TransactionLine.unit, qty.label("qty"))
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .join(Product, Product.id == TransactionLine.product_id)
        .where(
            Transaction.person_id.in_(person_ids),
            Transaction.status == TxStatus.CONFIRMED,
        )
        .group_by(Transaction.person_id, Product.name, TransactionLine.unit)
        .having(qty != 0)
    )
    out: dict[int, list[tuple[str, Decimal, str]]] = {}
    for person_id, name, unit, q in (await session.execute(stmt)).all():
        out.setdefault(person_id, []).append((name, Decimal(q), unit))
    return out
