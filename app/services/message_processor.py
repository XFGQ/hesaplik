"""raw_message -> parser -> intent_resolver -> ledger.

Kural motoruyla net ve güvenli bir eşleşme bulunduysa doğrudan CONFIRMED
kaydedilir (LLM yok, güven skoru yok — kural eşleşmesi zaten güvenli
sayılır). Belirsizse hiçbir şey kaydedilmez; sonucun outcome'u botun ne
soracağını belirler.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawMessage, Transaction, TxSource
from app.services import parser
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent, resolve
from app.services.ledger import Balance, LineInput, TxMeta, add_debt, add_payment, balance_of

TELEGRAM_ACTOR = "telegram-bot"


class ProcessOutcome(str, enum.Enum):
    RECORDED = "recorded"
    BALANCE = "balance"
    NEEDS_CONFIRMATION = "needs_confirmation"
    PERSON_NOT_FOUND = "person_not_found"
    UNRECOGNIZED = "unrecognized"


@dataclass(slots=True)
class ProcessResult:
    outcome: ProcessOutcome
    resolved: ResolvedIntent
    transaction_id: int | None = None
    balance: Balance | None = None


async def process_raw_message(session: AsyncSession, raw: RawMessage, text: str) -> ProcessResult:
    intent = parser.parse(text)
    resolved = await resolve(session, intent)
    return await handle_resolved(session, raw, resolved, text)


async def handle_resolved(
    session: AsyncSession, raw: RawMessage, resolved: ResolvedIntent, text: str
) -> ProcessResult:
    """Zaten çözülmüş bir niyeti işler. Bot'un onay callback'leri (kişi
    oluşturuldu / aday seçildi) de bu yolu tekrar kullanır."""
    if resolved.status == ResolutionStatus.UNRECOGNIZED:
        return ProcessResult(outcome=ProcessOutcome.UNRECOGNIZED, resolved=resolved)
    if resolved.status == ResolutionStatus.NEEDS_CONFIRMATION:
        return ProcessResult(outcome=ProcessOutcome.NEEDS_CONFIRMATION, resolved=resolved)
    if resolved.status == ResolutionStatus.PERSON_NOT_FOUND:
        return ProcessResult(outcome=ProcessOutcome.PERSON_NOT_FOUND, resolved=resolved)

    assert resolved.person is not None

    if resolved.kind == "balance_query":
        bal = await balance_of(session, resolved.person.id)
        return ProcessResult(outcome=ProcessOutcome.BALANCE, resolved=resolved, balance=bal)

    tx = await record_resolved(session, raw, resolved, text)
    bal = await balance_of(session, resolved.person.id)
    return ProcessResult(
        outcome=ProcessOutcome.RECORDED, resolved=resolved, transaction_id=tx.id, balance=bal
    )


async def record_resolved(
    session: AsyncSession, raw: RawMessage, resolved: ResolvedIntent, text: str
) -> Transaction:
    """READY durumundaki bir borç/tahsilat niyetini deftere yazar."""
    lines: list[LineInput] = []
    if resolved.product is not None and resolved.qty is not None:
        # Kullanıcının yazdığı tutar esas: line_total veriliyor, birim fiyat
        # ondan türetilir (price_history'ye bakılmaz).
        lines = [
            LineInput(
                product_id=resolved.product.id,
                qty=resolved.qty,
                unit=resolved.unit,
                line_total=resolved.amount,
            )
        ]

    meta = TxMeta(
        created_by=TELEGRAM_ACTOR,
        source=TxSource.TELEGRAM_TEXT,
        raw_text=text,
        trace_id=str(raw.id) if raw.id is not None else None,
    )

    if resolved.kind == "debt":
        amount_override = resolved.amount if not lines else None
        tx = await add_debt(session, resolved.person.id, lines, meta, amount_override=amount_override)
    else:
        tx = await add_payment(session, resolved.person.id, resolved.amount, meta, lines=lines or None)

    raw.processed_at = datetime.now(timezone.utc)
    raw.transaction_id = tx.id
    await session.flush()
    return tx
