from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import (
    Person,
    PriceHistory,
    Product,
    Setting,
    Transaction,
    TransactionLine,
    TxKind,
    TxSource,
    TxStatus,
)
from app.schemas import (
    ArchiveIn,
    BalanceOut,
    DebtIn,
    ItemOut,
    PaymentIn,
    PersonIn,
    PersonOut,
    PersonRowOut,
    ProductIn,
    ProductOut,
    ReverseIn,
    SettingIn,
    SettingOut,
    TxDetailOut,
    TxLineOut,
    TxOut,
    TxWithProductOut,
)
from app.services import catalog, ledger
from app.services.ledger import LedgerError, LineInput, TxMeta

router = APIRouter(prefix="/api")


def _actor() -> str:
    # Faz 3'te JWT'den gelecek.
    return "web"


# --------------------------------------------------------------- kişiler

@router.get("/persons", response_model=list[PersonRowOut])
async def list_persons(q: str | None = None, session: AsyncSession = Depends(get_session)):
    """Tablo tek istekte dolsun: bakiye ve açık kalemler dahil."""
    sign = case((Transaction.kind == TxKind.DEBIT, 1), else_=-1)

    stmt = (
        select(
            Person,
            func.coalesce(func.sum(Transaction.amount_try * sign), 0).label("balance_try"),
            func.max(Transaction.occurred_at).label("last_activity"),
        )
        .outerjoin(
            Transaction,
            (Transaction.person_id == Person.id) & (Transaction.status == TxStatus.CONFIRMED),
        )
        .where(Person.is_active.is_(True))
        .group_by(Person.id)
        .order_by(Person.full_name)
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Person.full_name.ilike(like) | Person.phone.ilike(like))

    rows = (await session.execute(stmt.limit(500))).all()
    if not rows:
        return []

    items_by_person = await _open_items_map(session, [r[0].id for r in rows])

    return [
        PersonRowOut(
            id=p.id,
            full_name=p.full_name,
            phone=p.phone,
            city=p.city,
            district=p.district,
            address=p.address,
            note=p.note,
            balance_try=Decimal(balance),
            last_activity=last,
            items=items_by_person.get(p.id, []),
        )
        for p, balance, last in rows
    ]


async def _open_items_map(session: AsyncSession, person_ids: list[int]) -> dict[int, list[ItemOut]]:
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
    out: dict[int, list[ItemOut]] = {}
    for person_id, name, unit, q in (await session.execute(stmt)).all():
        out.setdefault(person_id, []).append(
            ItemOut(product_name=name, qty=Decimal(q), unit=unit)
        )
    return out


@router.post("/persons", response_model=PersonOut, status_code=201)
async def create_person(body: PersonIn, session: AsyncSession = Depends(get_session)):
    person = Person(
        full_name=" ".join(body.full_name.split()),
        phone=body.phone,
        city=body.city,
        district=body.district,
        address=body.address,
        note=body.note,
    )
    session.add(person)
    await session.flush()
    return person


@router.get("/persons/{person_id}", response_model=PersonOut)
async def get_person(person_id: int, session: AsyncSession = Depends(get_session)):
    person = await session.get(Person, person_id)
    if person is None or not person.is_active:
        raise HTTPException(404, "Kişi bulunamadı")
    return person


@router.put("/persons/{person_id}", response_model=PersonOut)
async def update_person(
    person_id: int, body: PersonIn, session: AsyncSession = Depends(get_session)
):
    """Kişi kartı düzenlenebilir. Defter kayıtları değil, yalnızca iletişim bilgisi."""
    person = await session.get(Person, person_id)
    if person is None or not person.is_active:
        raise HTTPException(404, "Kişi bulunamadı")
    person.full_name = " ".join(body.full_name.split())
    person.phone = body.phone
    person.city = body.city
    person.district = body.district
    person.address = body.address
    person.note = body.note
    await session.flush()
    return person


@router.delete("/persons/{person_id}", status_code=204)
async def delete_person(person_id: int, session: AsyncSession = Depends(get_session)):
    """Kişiyi listeden kaldırır. Hareketleri silinmez, hesabı kapalı olmalı."""
    person = await session.get(Person, person_id)
    if person is None or not person.is_active:
        raise HTTPException(404, "Kişi bulunamadı")

    bal = await ledger.balance_of(session, person_id)
    if bal.balance_try != 0:
        raise HTTPException(
            422, f"Hesabı kapalı değil ({bal.balance_try} TL). Önce hesabı kapatın."
        )
    person.is_active = False
    await session.flush()


# --------------------------------------------------------------- ürünler

@router.get("/products", response_model=list[ProductOut])
async def list_products(session: AsyncSession = Depends(get_session)):
    """Yazarken öneri listesi için. Fiyat varsa gelir, zorunlu değil."""
    latest = (
        select(PriceHistory.product_id, func.max(PriceHistory.valid_from).label("vf"))
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
    existing = await catalog.find_product(session, body.name)
    if existing is not None:
        raise HTTPException(409, f"'{existing.name}' zaten kayıtlı")
    try:
        product, _ = await catalog.resolve_or_create(session, body.name, body.base_unit)
    except IntegrityError as e:
        raise HTTPException(409, "Bu ürün zaten kayıtlı") from e
    if body.unit_price is not None:
        session.add(
            PriceHistory(
                product_id=product.id,
                unit_price=body.unit_price,
                valid_from=body.valid_from or date.today(),
            )
        )
        await session.flush()
    return ProductOut(
        id=product.id,
        name=product.name,
        base_unit=product.base_unit,
        unit_price=body.unit_price,
    )


# --------------------------------------------------------------- hareketler

@router.post("/debts", response_model=TxWithProductOut, status_code=201)
async def add_debt(body: DebtIn, session: AsyncSession = Depends(get_session)):
    """Ürün adı serbest yazılır. Tutar kullanıcının yazdığıdır; fiyat listesi bağlamaz."""
    if await session.get(Person, body.person_id) is None:
        raise HTTPException(404, "Kişi bulunamadı")
    try:
        product, created = await catalog.resolve_or_create(session, body.product_name, body.unit)
        tx = await ledger.add_debt(
            session,
            body.person_id,
            [
                LineInput(
                    product_id=product.id,
                    qty=body.qty,
                    unit=body.unit or product.base_unit,
                    line_total=body.amount,
                )
            ],
            TxMeta(
                created_by=_actor(),
                source=TxSource.WEB,
                note=body.note,
                occurred_at=body.occurred_at,
            ),
        )
    except (LedgerError, ValueError) as e:
        raise HTTPException(422, str(e)) from e

    return TxWithProductOut(
        id=tx.id,
        person_id=tx.person_id,
        kind=tx.kind.value,
        amount_try=tx.amount_try,
        occurred_at=tx.occurred_at,
        status=tx.status.value,
        reverses_id=tx.reverses_id,
        product_name=product.name,
        product_created=created,
    )


@router.post("/payments", response_model=TxOut, status_code=201)
async def add_payment(body: PaymentIn, session: AsyncSession = Depends(get_session)):
    if await session.get(Person, body.person_id) is None:
        raise HTTPException(404, "Kişi bulunamadı")

    lines = None
    if body.product_name and body.qty:
        product, _ = await catalog.resolve_or_create(session, body.product_name, body.unit)
        lines = [
            LineInput(
                product_id=product.id,
                qty=body.qty,
                unit=body.unit or product.base_unit,
                line_total=body.amount,
            )
        ]
    try:
        return await ledger.add_payment(
            session,
            body.person_id,
            body.amount,
            TxMeta(created_by=_actor(), note=body.note, occurred_at=body.occurred_at),
            lines=lines,
        )
    except (LedgerError, ValueError) as e:
        raise HTTPException(422, str(e)) from e


@router.post("/transactions/{tx_id}/reverse", response_model=TxOut, status_code=201)
async def reverse_tx(tx_id: int, body: ReverseIn, session: AsyncSession = Depends(get_session)):
    try:
        return await ledger.reverse(session, tx_id, actor=_actor(), reason=body.reason)
    except LedgerError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/transactions/{tx_id}", status_code=204)
async def delete_transaction(
    tx_id: int, body: ArchiveIn, session: AsyncSession = Depends(get_session)
):
    """Sil = arşive taşı. Kayıt yok edilmez, archived_transactions'a kopyalanıp
    canlı defterden çıkarılır."""
    try:
        await ledger.archive_transaction(session, tx_id, actor=_actor(), reason=body.reason)
    except LedgerError as e:
        raise HTTPException(422, str(e)) from e


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
    person_id: int, limit: int = 200, session: AsyncSession = Depends(get_session)
):
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


# --------------------------------------------------------------- ayarlar

@router.get("/settings", response_model=dict[str, str])
async def list_settings(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Setting))).scalars().all()
    return {s.key: s.value for s in rows}


@router.put("/settings/{key}", response_model=SettingOut)
async def update_setting(
    key: str, body: SettingIn, session: AsyncSession = Depends(get_session)
):
    """Yoksa oluşturur, varsa günceller."""
    setting = await session.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=body.value)
        session.add(setting)
    else:
        setting.value = body.value
        setting.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return SettingOut(key=setting.key, value=setting.value)
