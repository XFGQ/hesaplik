from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TxKind(str, enum.Enum):
    DEBIT = "DEBIT"    # borç: kişinin bize borcu arttı
    CREDIT = "CREDIT"  # tahsilat: kişi ödeme yaptı


class TxStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class TxSource(str, enum.Enum):
    WEB = "WEB"
    TELEGRAM_TEXT = "TELEGRAM_TEXT"
    TELEGRAM_VOICE = "TELEGRAM_VOICE"
    SYSTEM = "SYSTEM"


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)      # il
    district: Mapped[str | None] = mapped_column(Text)  # ilçe
    address: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    aliases: Mapped[list[PersonAlias]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class PersonAlias(Base):
    __tablename__ = "person_aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    person_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)

    person: Mapped[Person] = relationship(back_populates="aliases")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    base_unit: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    aliases: Mapped[list[ProductAlias]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    prices: Mapped[list[PriceHistory]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductAlias(Base):
    __tablename__ = "product_aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)

    product: Mapped[Product] = relationship(back_populates="aliases")


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("product_id", "valid_from", name="uq_price_from"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="prices")


class Transaction(Base):
    """APPEND-ONLY. Asla UPDATE etme. Düzeltme için ters kayıt aç."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_try > 0", name="chk_amount_pos"),
        CheckConstraint("reverses_id IS NULL OR reverses_id <> id", name="chk_no_self_reverse"),
        Index("idx_tx_person_status", "person_id", "status"),
        Index("idx_tx_occurred", "occurred_at"),
        Index(
            "uq_tx_reverses",
            "reverses_id",
            unique=True,
            postgresql_where=lambda: Transaction.reverses_id.isnot(None),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    person_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("persons.id"), nullable=False)
    kind: Mapped[TxKind] = mapped_column(Enum(TxKind, name="tx_kind"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    amount_try: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[TxSource] = mapped_column(
        Enum(TxSource, name="tx_source"), nullable=False, default=TxSource.WEB
    )
    raw_text: Mapped[str | None] = mapped_column(Text)
    llm_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    engine: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TxStatus] = mapped_column(
        Enum(TxStatus, name="tx_status"), nullable=False, default=TxStatus.CONFIRMED
    )
    reverses_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("transactions.id"))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    lines: Mapped[list[TransactionLine]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", lazy="selectin"
    )


class TransactionLine(Base):
    __tablename__ = "transaction_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    transaction: Mapped[Transaction] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship(lazy="joined")


class ArchivedTransaction(Base):
    """Silinen kayıtların gittiği yer. `transactions`'tan gerçekten silinir,
    burada kim/ne zaman/niçin bilgisiyle durur."""

    __tablename__ = "archived_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    person_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("persons.id"), nullable=False)
    kind: Mapped[TxKind] = mapped_column(Enum(TxKind, name="tx_kind"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount_try: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[TxSource] = mapped_column(Enum(TxSource, name="tx_source"), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    llm_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    engine: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TxStatus] = mapped_column(Enum(TxStatus, name="tx_status"), nullable=False)
    reverses_id: Mapped[int | None] = mapped_column(BigInteger)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lines_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    archived_by: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    archive_reason: Mapped[str | None] = mapped_column(Text)


class ArchivedPerson(Base):
    """Silinen kişilerin gittiği yer. `persons`'tan gerçekten silinmez
    (yalnızca is_active=false yapılır); burada kart + o anki bakiye + tüm
    işlemlerinin snapshot'ı kim/ne zaman/niçin bilgisiyle durur."""

    __tablename__ = "archived_persons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    original_person_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("persons.id"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    person_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    balance_try: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    transactions_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    archived_by: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    archive_reason: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RawMessage(Base):
    """Dokunulmaz ham mesaj. `payload`/`received_at` asla değişmez.

    detected_*/parse_*/outcome_* alanları (Faz 7, admin paneli "İşlem Akışı")
    mesaj işlenirken YAN ETKİ olarak doldurulur: müşteri ne yazdı → sistem ne
    algıladı → ne yaptı. Hepsi nullable, yazılamamaları defteri etkilemez
    (bkz. app/services/message_trace.py)."""

    __tablename__ = "raw_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    chat_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transaction_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("transactions.id"))

    # --- izleme (admin paneli "İşlem Akışı")
    detected_kind: Mapped[str | None] = mapped_column(Text)
    detected_person: Mapped[str | None] = mapped_column(Text)
    detected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    detected_product: Mapped[str | None] = mapped_column(Text)
    detected_qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    detected_unit: Mapped[str | None] = mapped_column(Text)
    parse_source: Mapped[str | None] = mapped_column(Text)
    parse_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(Text)
    outcome_detail: Mapped[str | None] = mapped_column(Text)


class PendingRequest(Base):
    """Tek mesajdan çıkan işlemlerin kalıcı kuyruğu. Bellekte değil DB'de
    tutulur ki bot soru sorup beklese, internet kopsa, bot yeniden başlasa
    bile istek kaybolmasın. Aynı mesajdan gelenler aynı batch_id'yi paylaşır,
    sira_no ile sırayla işlenir."""

    __tablename__ = "pending_requests"
    __table_args__ = (
        CheckConstraint(
            "durum IN ('beklemede', 'isleniyor', 'tamamlandi', 'basarisiz', 'iptal')",
            name="chk_pending_durum",
        ),
        Index("idx_pending_chat_durum", "chat_id", "durum"),
        Index("uq_pending_batch_sira", "batch_id", "sira_no", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    batch_id: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    sira_no: Mapped[int] = mapped_column(Integer, nullable=False)
    durum: Mapped[str] = mapped_column(Text, nullable=False, default="beklemede")
    sonuc: Mapped[str | None] = mapped_column(Text)
    hata: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RestoreRequest(Base):
    """Geri yükleme isteği — "Yol A": panel İSTER, host UYGULAR.

    API container'ı veritabanını geri yükleyemez (docker/compose yok, restic
    deposu salt okunur). Panel buraya `bekliyor` bir satır yazar; host'taki
    izleyici (scripts/restore-apply.sh) alır, önce güvenlik yedeği alır
    (`pre_backup_snapshot`), sonra yükler ve `status`u günceller.

    Tek seferde tek aktif restore: kısmi tekil indeks (uq_restore_tek_aktif)
    veritabanı düzeyinde garanti eder."""

    __tablename__ = "restore_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('bekliyor', 'yedekleniyor', 'yukleniyor', 'tamamlandi', 'hata')",
            name="chk_restore_status",
        ),
        Index("idx_restore_requested", "requested_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="bekliyor")
    pre_backup_snapshot: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_detail: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
