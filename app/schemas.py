from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    full_name: str
    phone: str | None = None


class PersonIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str | None = None
    note: str | None = None


class LineIn(BaseModel):
    product_id: int
    qty: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0)


class DebtIn(BaseModel):
    person_id: int
    lines: list[LineIn] = []
    amount_override: Decimal | None = Field(default=None, gt=0)
    note: str | None = None
    occurred_at: datetime | None = None


class PaymentIn(BaseModel):
    person_id: int
    amount: Decimal = Field(gt=0)
    note: str | None = None
    occurred_at: datetime | None = None


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class TxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    person_id: int
    kind: str
    amount_try: Decimal
    occurred_at: datetime
    status: str
    reverses_id: int | None = None


class ItemOut(BaseModel):
    product_name: str
    qty: Decimal
    unit: str


class BalanceOut(BaseModel):
    person_id: int
    balance_try: Decimal
    is_receivable: bool
    items: list[ItemOut]
