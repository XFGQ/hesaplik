from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import (
    Person,
    PriceHistory,
    Product,
    Transaction,
    TransactionLine,
    TxKind,
    TxSource,
    TxStatus,
)
from app.schemas import (
    BalanceOut,
    DebtIn,
    ItemOut,
    PaymentIn,
    PersonIn,
    PersonOut,
    PersonWithBalanceOut,
    ProductIn,
    ProductOut,
    ReverseIn,
    TxDetailOut,
    TxLineOut,
    TxOut,
)
from app.services import ledger
from app.services.ledger import LedgerError, LineInput, TxMeta

router = APIRouter(prefix="/api")


def _actor() -> str:
    # Faz 3'te JWT'den gelecek.
    return "web"


# --------------------------------------------------------------- kişiler

@router.get("/persons", response_model=list[PersonWithBalanceOut])
async def list_persons(q: str | None = None, session: AsyncSession = Depends(get_session)):
    """Liste ekranı tek istekte dolsun: bakiye de burada gelir."""
    sign = case((Transaction.kind == TxKind.DEBIT, 1), else_=-1)
    balance = func.coalesce(func.sum(Transaction.amount_try * sign), 0).label("balance_try")

    stmt = (
        select(Person.id, Person.full_name, Person.phone, balance)
        .outerjoin(
            Transaction,
            (Transaction.person_id == Person.id) & (Transaction.status == TxStatus.CONFIRMED),
        )
        .where(Person.is_active.is_(True))
        .group_by(Person.id, Person.full_name, Person.phone)
        .order_by(Person.full_name)
    )
    if q:
        stmt = stmt.where(Person.full_name.ilike(f"%{q}%"))

    rows = (await session.execute(stmt.limit(200))).all()
    return [
        PersonWithBalanceOut(id=r.id, full_name=r.full_name, phone=r.phone,
                             balance_try=Decimal(r.balance_try))
        for r in rows
    ]


@router.post("/persons", response_model=PersonOut, status_code=201)
async def create_person(body: PersonIn, session: AsyncSession = Depends(get_session)):
    person = Person(full_name=body.full_name.strip(), phone=body.phone, note=body.note)
    session.add(person)
    await session.flush()
    return person


@router.get("/persons/{person_id}", response_model=PersonOut)
async def get_person(person_id: int, session: AsyncSession = Depends(get_session)):
    person = await session.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "Kişi bulunamadı")
    return person


@router.get("/persons/{person_id}/balance", response_model=BalanceOut)
async def balance(person_id: int, session: AsyncSession = Depends(get_session)):
    if await session.get(Person, person_id) is None:
        raise HTTPException(404, "Kişi bulunamadı")
    bal = await ledger.balance_of(session, person_id)
    return BalanceOut(
        person_id=bal.person_id,
        balance_try=bal.balance_try,
        is_receivable=bal.is_receivable,
        items=[ItemOut(product_name=n, qty=q, unit=u) for n, q, u in bal.items],
    )


@router.get("/persons/{person_id}/transactions", response_model=list[TxDetailOut])
async def person_transactions(
    person_id: int, limit: int = 100, session: AsyncSession = Depends(get_session)
):
    """Hareket dökümü. Ters kaydı olan hareketler is_reversed ile işaretlenir."""
    stmt = (
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.product))
        .where(Transaction.person_id == person_id, Transaction.status != TxStatus.REJECTED)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(limit)
    )
    txs = list((await session.execute(stmt)).scalars())

    reversed_ids = set(
        (
            await session.execute(
                select(Transaction.reverses_id).where(Transaction.reverses_id.isnot(None))
            )
        ).scalars()
    )

    return [
        TxDetailOut(
            id=t.id,
            person_id=t.person_id,
            kind=t.kind.value,
            amount_try=t.amount_try,
            occurred_at=t.occurred_at,
            status=t.status.value,
            source=t.source.value,
            note=t.note,
            reverses_id=t.reverses_id,
            is_reversed=t.id in reversed_ids,
            lines=[
                TxLineOut(
                    product_name=li.product.name,
                    qty=li.qty,
                    unit=li.unit,
                    unit_price=li.unit_price,
                    line_total=li.line_total,
                )
                for li in t.lines
            ],
        )
        for t in txs
    ]


# --------------------------------------------------------------- ürünler

@router.get("/products", response_model=list[ProductOut])
async def list_products(session: AsyncSession = Depends(get_session)):
    """Güncel fiyatıyla birlikte. Ekleme ekranı bunu kullanır."""
    latest = (
        select(
            PriceHistory.product_id,
            func.max(PriceHistory.valid_from).label("vf"),
        )
        .group_by(PriceHistory.product_id)
        .subquery()
    )
    stmt = (
        select(Product.id, Product.name, Product.base_unit, PriceHistory.unit_price)
        .outerjoin(latest, latest.c.product_id == Product.id)
        .outerjoin(
            PriceHistory,
            (PriceHistory.product_id == Product.id) & (PriceHistory.valid_from == latest.c.vf),
        )
        .where(Product.is_active.is_(True))
        .order_by(Product.name)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ProductOut(id=r.id, name=r.name, base_unit=r.base_unit, unit_price=r.unit_price)
        for r in rows
    ]


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductIn, session: AsyncSession = Depends(get_session)):
    from datetime import date

    product = Product(name=body.name.strip(), base_unit=body.base_unit.strip())
    session.add(product)
    await session.flush()
    session.add(
        PriceHistory(
            product_id=product.id,
            unit_price=body.unit_price,
            valid_from=body.valid_from or date.today(),
        )
    )
    await session.flush()
    return ProductOut(
        id=product.id, name=product.name, base_unit=product.base_unit, unit_price=body.unit_price
    )


# --------------------------------------------------------------- hareketler

@router.post("/debts", response_model=TxOut, status_code=201)
async def add_debt(body: DebtIn, session: AsyncSession = Depends(get_session)):
    try:
        return await ledger.add_debt(
            session,
            body.person_id,
            [LineInput(li.product_id, Decimal(li.qty), None, li.unit_price) for li in body.lines],
            TxMeta(created_by=_actor(), source=TxSource.WEB, note=body.note,
                   occurred_at=body.occurred_at),
            amount_override=body.amount_override,
        )
    except LedgerError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/payments", response_model=TxOut, status_code=201)
async def add_payment(body: PaymentIn, session: AsyncSession = Depends(get_session)):
    try:
        return await ledger.add_payment(
            session, body.person_id, body.amount,
            TxMeta(created_by=_actor(), note=body.note, occurred_at=body.occurred_at),
        )
    except LedgerError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/transactions/{tx_id}/reverse", response_model=TxOut, status_code=201)
async def reverse_tx(tx_id: int, body: ReverseIn, session: AsyncSession = Depends(get_session)):
    try:
        return await ledger.reverse(session, tx_id, actor=_actor(), reason=body.reason)
    except LedgerError as e:
        raise HTTPException(422, str(e)) from e
