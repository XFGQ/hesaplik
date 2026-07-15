from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Person, TxSource
from app.schemas import (
    BalanceOut,
    DebtIn,
    ItemOut,
    PaymentIn,
    PersonIn,
    PersonOut,
    ReverseIn,
    TxOut,
)
from app.services import ledger
from app.services.ledger import LedgerError, LineInput, TxMeta

router = APIRouter(prefix="/api")


def _actor() -> str:
    # Faz 3'te JWT'den gelecek.
    return "web"


@router.post("/persons", response_model=PersonOut, status_code=201)
async def create_person(body: PersonIn, session: AsyncSession = Depends(get_session)):
    person = Person(full_name=body.full_name, phone=body.phone, note=body.note)
    session.add(person)
    await session.flush()
    return person


@router.get("/persons", response_model=list[PersonOut])
async def list_persons(q: str | None = None, session: AsyncSession = Depends(get_session)):
    stmt = select(Person).where(Person.is_active.is_(True)).order_by(Person.full_name)
    if q:
        stmt = stmt.where(Person.full_name.ilike(f"%{q}%"))
    return list((await session.execute(stmt.limit(50))).scalars())


@router.post("/debts", response_model=TxOut, status_code=201)
async def add_debt(body: DebtIn, session: AsyncSession = Depends(get_session)):
    try:
        tx = await ledger.add_debt(
            session,
            body.person_id,
            [LineInput(l.product_id, Decimal(l.qty), None, l.unit_price) for l in body.lines],
            TxMeta(created_by=_actor(), source=TxSource.WEB, note=body.note,
                   occurred_at=body.occurred_at),
            amount_override=body.amount_override,
        )
    except LedgerError as e:
        raise HTTPException(422, str(e)) from e
    return tx


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


@router.get("/persons/{person_id}/balance", response_model=BalanceOut)
async def balance(person_id: int, session: AsyncSession = Depends(get_session)):
    bal = await ledger.balance_of(session, person_id)
    return BalanceOut(
        person_id=bal.person_id,
        balance_try=bal.balance_try,
        is_receivable=bal.is_receivable,
        items=[ItemOut(product_name=n, qty=q, unit=u) for n, q, u in bal.items],
    )
