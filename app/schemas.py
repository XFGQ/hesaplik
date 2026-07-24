from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PersonIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=60)
    district: str | None = Field(default=None, max_length=60)
    address: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=1000)


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    full_name: str
    phone: str | None = None
    city: str | None = None
    district: str | None = None
    address: str | None = None
    note: str | None = None


class ItemOut(BaseModel):
    product_name: str
    qty: Decimal
    unit: str


class PersonRowOut(PersonOut):
    balance_try: Decimal
    items: list[ItemOut] = []
    last_activity: datetime | None = None


class ProductOut(BaseModel):
    id: int
    name: str
    base_unit: str
    unit_price: Decimal | None = None


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_unit: str = Field(default="adet", min_length=1, max_length=20)
    unit_price: Decimal | None = Field(default=None, ge=0)
    valid_from: date | None = None


class DebtIn(BaseModel):
    person_id: int
    product_name: str = Field(min_length=1, max_length=80)
    qty: Decimal = Field(gt=0)
    unit: str | None = Field(default=None, max_length=20)
    amount: Decimal = Field(gt=0)
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class PaymentIn(BaseModel):
    person_id: int
    amount: Decimal = Field(gt=0)
    product_name: str | None = Field(default=None, max_length=80)
    qty: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=20)
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class ArchiveIn(BaseModel):
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


class TxWithProductOut(TxOut):
    product_name: str
    product_created: bool = False


class TxLineOut(BaseModel):
    product_name: str
    qty: Decimal
    unit: str
    unit_price: Decimal
    line_total: Decimal


class TxDetailOut(BaseModel):
    id: int
    person_id: int
    kind: str
    amount_try: Decimal
    occurred_at: datetime
    status: str
    source: str
    note: str | None = None
    reverses_id: int | None = None
    is_reversed: bool = False
    lines: list[TxLineOut] = []


class BalanceOut(BaseModel):
    person_id: int
    balance_try: Decimal
    is_receivable: bool
    items: list[ItemOut]


class SettingIn(BaseModel):
    value: str = Field(min_length=1, max_length=500)


class SettingOut(BaseModel):
    key: str
    value: str


class BackupSnapshotOut(BaseModel):
    id: str
    time: datetime
    size_bytes: int


class BackupRunOut(BaseModel):
    ok: bool
    message: str
    duration_seconds: float
