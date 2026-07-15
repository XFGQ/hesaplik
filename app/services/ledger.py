"""Defter servisi.

Kurallar:
  1. Para aritmetiği yalnızca burada ve Decimal ile yapılır. float yasak.
  2. Kayıt değiştirilmez. Düzeltme = ters kayıt (reverse).
  3. Bakiye kolonda tutulmaz, her zaman hesaplanır.
  4. LLM bu modülü çağırır; bu modül LLM'i bilmez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditLog,
    PriceHistory,
    Product,
    Transaction,
    TransactionLine,
    TxKind,
    TxSource,
    TxStatus,
)

KURUS = Decimal("0.01")


class LedgerError(Exception):
    """İş kuralı ihlali."""


def money(value: Decimal | int | str) -> Decimal:
    """Her tutar kuruşa yuvarlanır. Bu fonksiyondan geçmeyen tutar defterе girmez."""
    return Decimal(value).quantize(KURUS, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class LineInput:
    product_id: int
    qty: Decimal
    unit: str | None = None
    unit_price: Decimal | None = None  # None ise price_history'den çekilir


@dataclass(slots=True)
class TxMeta:
    created_by: str
    source: TxSource = TxSource.WEB
    raw_text: str | None = None
    llm_confidence: Decimal | None = None
    engine: str | None = None
    trace_id: str | None = None
    note: str | None = None
    occurred_at: datetime | None = None


@dataclass(slots=True)
class Balance:
    person_id: int
    balance_try: Decimal
    items: list[tuple[str, Decimal, str]] = field(default_factory=list)

    @property
    def is_receivable(self) -> bool:
        """True ise kişi bize borçlu."""
        return self.balance_try > 0


async def resolve_unit_price(
    session: AsyncSession, product_id: int, on: date
) -> tuple[Decimal, str]:
    """Verilen tarihte geçerli birim fiyatı ve ürünün temel birimini döndürür."""
    product = await session.get(Product, product_id)
    if product is None:
        raise LedgerError(f"Ürün bulunamadı: {product_id}")

    stmt = (
        select(PriceHistory.unit_price)
        .where(PriceHistory.product_id == product_id, PriceHistory.valid_from <= on)
        .order_by(PriceHistory.valid_from.desc())
        .limit(1)
    )
    price = (await session.execute(stmt)).scalar_one_or_none()
    if price is None:
        raise LedgerError(f"{product.name} için {on} tarihinde geçerli fiyat yok")
    return money(price), product.base_unit


async def _build_lines(
    session: AsyncSession, lines: list[LineInput], on: date
) -> tuple[list[TransactionLine], Decimal]:
    built: list[TransactionLine] = []
    total = Decimal("0.00")
    for li in lines:
        if li.qty <= 0:
            raise LedgerError("Adet sıfır veya negatif olamaz")
        unit_price = li.unit_price
        unit = li.unit
        if unit_price is None or unit is None:
            resolved_price, base_unit = await resolve_unit_price(session, li.product_id, on)
            unit_price = unit_price if unit_price is not None else resolved_price
            unit = unit or base_unit
        unit_price = money(unit_price)
        line_total = money(Decimal(li.qty) * unit_price)
        total += line_total
        built.append(
            TransactionLine(
                product_id=li.product_id,
                qty=Decimal(li.qty),
                unit=unit,
                unit_price=unit_price,
                line_total=line_total,
            )
        )
    return built, money(total)


async def add_debt(
    session: AsyncSession,
    person_id: int,
    lines: list[LineInput],
    meta: TxMeta,
    amount_override: Decimal | None = None,
    status: TxStatus = TxStatus.CONFIRMED,
) -> Transaction:
    """Borç kaydı. Tutar satırlardan hesaplanır; amount_override yalnızca
    kalem olmayan serbest borç (örn. nakit ödünç) için kullanılır."""
    if not lines and amount_override is None:
        raise LedgerError("Borç için ya kalem ya da tutar gerekir")

    occurred = meta.occurred_at or datetime.now(timezone.utc)
    built, total = await _build_lines(session, lines, occurred.date())
    amount = money(amount_override) if amount_override is not None else total
    if amount <= 0:
        raise LedgerError("Tutar sıfırdan büyük olmalı")

    tx = Transaction(
        person_id=person_id,
        kind=TxKind.DEBIT,
        occurred_at=occurred,
        amount_try=amount,
        note=meta.note,
        source=meta.source,
        raw_text=meta.raw_text,
        llm_confidence=meta.llm_confidence,
        engine=meta.engine,
        trace_id=meta.trace_id,
        status=status,
        created_by=meta.created_by,
        lines=built,
    )
    session.add(tx)
    await session.flush()
    await _audit(session, meta.created_by, "add_debt", tx, meta.trace_id)
    return tx


async def add_payment(
    session: AsyncSession,
    person_id: int,
    amount: Decimal,
    meta: TxMeta,
    status: TxStatus = TxStatus.CONFIRMED,
) -> Transaction:
    """Tahsilat kaydı."""
    amount = money(amount)
    if amount <= 0:
        raise LedgerError("Tahsilat sıfırdan büyük olmalı")

    tx = Transaction(
        person_id=person_id,
        kind=TxKind.CREDIT,
        occurred_at=meta.occurred_at or datetime.now(timezone.utc),
        amount_try=amount,
        note=meta.note,
        source=meta.source,
        raw_text=meta.raw_text,
        llm_confidence=meta.llm_confidence,
        engine=meta.engine,
        trace_id=meta.trace_id,
        status=status,
        created_by=meta.created_by,
    )
    session.add(tx)
    await session.flush()
    await _audit(session, meta.created_by, "add_payment", tx, meta.trace_id)
    return tx


async def reverse(
    session: AsyncSession, transaction_id: int, actor: str, reason: str,
    trace_id: str | None = None,
) -> Transaction:
    """Kaydı iptal eder. UPDATE yok: karşıt kind ile ters kayıt açılır.
    Bakiye toplamda kendiliğinden sıfırlanır, iz kalır."""
    orig = await session.get(Transaction, transaction_id)
    if orig is None:
        raise LedgerError(f"Kayıt bulunamadı: {transaction_id}")
    if orig.status is not TxStatus.CONFIRMED:
        raise LedgerError("Yalnızca onaylı kayıt ters kaydedilebilir")

    existing = (
        await session.execute(
            select(Transaction.id).where(Transaction.reverses_id == transaction_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise LedgerError(f"Kayıt zaten iptal edilmiş (ters kayıt: {existing})")

    opposite = TxKind.CREDIT if orig.kind is TxKind.DEBIT else TxKind.DEBIT
    contra = Transaction(
        person_id=orig.person_id,
        kind=opposite,
        occurred_at=datetime.now(timezone.utc),
        amount_try=orig.amount_try,
        note=f"İptal (#{orig.id}): {reason}",
        source=TxSource.SYSTEM,
        status=TxStatus.CONFIRMED,
        reverses_id=orig.id,
        trace_id=trace_id,
        created_by=actor,
        lines=[
            TransactionLine(
                product_id=li.product_id,
                qty=li.qty,
                unit=li.unit,
                unit_price=li.unit_price,
                line_total=li.line_total,
            )
            for li in orig.lines
        ],
    )
    session.add(contra)
    await session.flush()
    await _audit(session, actor, "reverse", contra, trace_id, extra={"reverses": orig.id})
    return contra


async def confirm(session: AsyncSession, transaction_id: int, actor: str) -> Transaction:
    """PENDING -> CONFIRMED. Tetikleyicinin izin verdiği tek geçiş."""
    tx = await session.get(Transaction, transaction_id)
    if tx is None:
        raise LedgerError(f"Kayıt bulunamadı: {transaction_id}")
    if tx.status is not TxStatus.PENDING:
        raise LedgerError("Yalnızca bekleyen kayıt onaylanabilir")
    tx.status = TxStatus.CONFIRMED
    await session.flush()
    await _audit(session, actor, "confirm", tx, tx.trace_id)
    return tx


async def reject(session: AsyncSession, transaction_id: int, actor: str) -> Transaction:
    tx = await session.get(Transaction, transaction_id)
    if tx is None:
        raise LedgerError(f"Kayıt bulunamadı: {transaction_id}")
    if tx.status is not TxStatus.PENDING:
        raise LedgerError("Yalnızca bekleyen kayıt reddedilebilir")
    tx.status = TxStatus.REJECTED
    await session.flush()
    await _audit(session, actor, "reject", tx, tx.trace_id)
    return tx


async def balance_of(session: AsyncSession, person_id: int) -> Balance:
    """Bakiye + açık kalemler. Tek kaynak: onaylı hareketlerin toplamı."""
    sign = case((Transaction.kind == TxKind.DEBIT, 1), else_=-1)

    total = (
        await session.execute(
            select(func.coalesce(func.sum(Transaction.amount_try * sign), 0)).where(
                Transaction.person_id == person_id,
                Transaction.status == TxStatus.CONFIRMED,
            )
        )
    ).scalar_one()

    items_stmt = (
        select(
            Product.name,
            func.sum(TransactionLine.qty * sign).label("qty"),
            TransactionLine.unit,
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .join(Product, Product.id == TransactionLine.product_id)
        .where(
            Transaction.person_id == person_id,
            Transaction.status == TxStatus.CONFIRMED,
        )
        .group_by(Product.name, TransactionLine.unit)
        .having(func.sum(TransactionLine.qty * sign) != 0)
    )
    rows = (await session.execute(items_stmt)).all()
    items = [(name, Decimal(qty), unit) for name, qty, unit in rows]

    return Balance(person_id=person_id, balance_try=money(total), items=items)


async def _audit(
    session: AsyncSession,
    actor: str,
    action: str,
    tx: Transaction,
    trace_id: str | None,
    extra: dict | None = None,
) -> None:
    after = {
        "id": tx.id,
        "person_id": tx.person_id,
        "kind": tx.kind.value,
        "amount_try": str(tx.amount_try),
        "status": tx.status.value,
        "source": tx.source.value,
    }
    if extra:
        after.update(extra)
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            entity="transactions",
            entity_id=str(tx.id),
            after=after,
            trace_id=trace_id,
        )
    )
