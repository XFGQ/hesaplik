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
from sqlalchemy.orm import selectinload

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


@dataclass(slots=True)
class PersonTransactionRow:
    """Bir kişinin tek bir hareketi: bakiye TABLO çıktısı için (CLAUDE.md >
    "Bot sorgu anlama" Grup 1, madde 2). Ledger'daki Transaction'ın Telegram
    metin tablosuna uygun, düz (session'dan bağımsız) bir görünümü."""

    id: int
    occurred_at: datetime
    kind: TxKind
    amount_try: Decimal
    lines: list[tuple[str, Decimal, str]] = field(default_factory=list)


async def list_person_transactions(
    session: AsyncSession, person_id: int, limit: int | None = None
) -> tuple[list[PersonTransactionRow], int]:
    """Bir kişinin tüm onaylı hareketleri, kronolojik sırayla (eski->yeni).

    `limit` verilirse yalnızca SON `limit` kayıt döner; ikinci değer her
    zaman limitsiz TOPLAM sayıdır — çağıran taraf ("...ve N kayıt daha")
    diyebilsin diye (bkz. app/bot/main.py)."""
    stmt = (
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.product))
        .where(Transaction.person_id == person_id, Transaction.status == TxStatus.CONFIRMED)
        .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
    )
    txs = list((await session.execute(stmt)).scalars())
    total = len(txs)
    if limit is not None and total > limit:
        txs = txs[-limit:]

    rows = [
        PersonTransactionRow(
            id=t.id,
            occurred_at=t.occurred_at,
            kind=t.kind,
            amount_try=t.amount_try,
            lines=[(li.product.name, Decimal(li.qty), li.unit) for li in t.lines],
        )
        for t in txs
    ]
    return rows, total


async def search_persons(session: AsyncSession, term: str) -> list[PersonBalanceRow]:
    """Tek kelimelik serbest arama (CLAUDE.md > "Bot sorgu anlama" Grup 1,
    madde 5): isim/soyad/ilçe içinde `term` geçen tüm aktif kişiler,
    bakiyeleriyle. Telegram'ın kendi kişi aramasına benzer — "hangisi?" diye
    sormaz, doğrudan eşleşen HERKESİ listeler.

    Türkçe harflerde SQL ILIKE güvenilir olmadığı için (bkz.
    list_persons_with_balance'daki ilçe filtresi ile aynı gerekçe), tüm
    aktif kişiler çekilip Python tarafında normalize edilerek karşılaştırılır.
    """
    key = normalize(" ".join((term or "").split()))
    if not key:
        return []

    rows = await list_persons_with_balance(session, scope="all")
    return [
        r
        for r in rows
        if key in normalize(r.person.full_name)
        or (r.person.district and key in normalize(r.person.district))
    ]
