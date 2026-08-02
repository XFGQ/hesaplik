"""Admin paneli uçları (/api/admin/*) — Faz 7.

Tümü `admin_auth.require_admin` arkasında: token yoksa/geçersizse 401, hiçbir
veri dönmez. Tek istisna `POST /login` (token'ı o üretir) ve `GET /me`
(oturumun geçerli olup olmadığını söyler, veri sızdırmaz).

Şimdilik tek gerçek bölüm "İşlem Akışı": raw_messages'a mesaj işlenirken
yazılan izleme verisi (bkz. app/services/message_trace.py) zaman sıralı,
filtrelenebilir ve sayfalanabilir biçimde dönülür. Diğer bölümler (sistem
sağlığı, LLM izleme, kuyruk, loglar...) arayüzde yer tutuyor, uçları
sonraki adımda eklenecek.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import get_session
from app.models import Person, RawMessage, Transaction
from app.services import admin_auth, message_trace

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Kaba kuvvet freni: aynı IP'den kısa sürede çok sayıda yanlış şifre
# denemesi kilitlenir. Tek kullanıcılı panel için bellekte tutmak yeterli —
# süreç yeniden başlarsa sayaç sıfırlanır, kalıcı bir tablo gerekmez.
_MAX_FAILURES = 10
_LOCKOUT_SECONDS = 300
_failures: dict[str, list[float]] = defaultdict(list)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "bilinmeyen"


def _locked_out(key: str, now: float) -> bool:
    recent = [t for t in _failures[key] if now - t < _LOCKOUT_SECONDS]
    _failures[key] = recent
    return len(recent) >= _MAX_FAILURES


# ---------------------------------------------------------------- şemalar

class LoginIn(BaseModel):
    password: str


class LoginOut(BaseModel):
    ok: bool
    token: str
    expires_in: int


class MeOut(BaseModel):
    ok: bool


class FlowTxOut(BaseModel):
    """İzlenen mesajdan doğan defter kaydı (varsa)."""

    id: int
    kind: str
    amount_try: Decimal
    person_id: int
    person_name: str
    occurred_at: datetime


class FlowRowOut(BaseModel):
    id: int
    received_at: datetime
    processed_at: datetime | None
    channel: str
    chat_id: str | None
    text: str | None                  # müşteri ne yazdı (payload'dan)
    detected_kind: str | None         # sistem ne algıladı
    detected_person: str | None
    detected_amount: Decimal | None
    detected_product: str | None
    detected_qty: Decimal | None
    detected_unit: str | None
    parse_source: str | None          # regex | llm | none
    parse_ms: int | None
    outcome: str | None               # ne yaptı
    outcome_detail: str | None
    transaction: FlowTxOut | None


class FlowPageOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[FlowRowOut]


# ---------------------------------------------------------------- oturum

@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request, response: Response):
    """Doğru şifre → httpOnly çerez + token. Yanlış şifre → 401, hiçbir
    ipucu yok. ADMIN_PASSWORD tanımsızsa panel kapalıdır (503)."""
    if not admin_auth.is_enabled():
        raise HTTPException(503, "Admin paneli yapılandırılmamış")

    key = _client_key(request)
    now = time.time()
    if _locked_out(key, now):
        raise HTTPException(429, "Çok fazla deneme, biraz sonra tekrar deneyin")

    if not admin_auth.check_password(body.password):
        _failures[key].append(now)
        raise HTTPException(401, "Şifre hatalı")

    _failures.pop(key, None)
    token = admin_auth.create_token()
    max_age = admin_auth.token_max_age()
    response.set_cookie(
        admin_auth.COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,     # JS okuyamaz
        samesite="lax",
        path="/",
    )
    return LoginOut(ok=True, token=token, expires_in=max_age)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(admin_auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeOut, dependencies=[admin_auth.AdminRequired])
async def me():
    """Panel açılışında "oturumum geçerli mi?" sorusu. Geçersizse 401."""
    return MeOut(ok=True)


# ---------------------------------------------------------------- işlem akışı

@router.get("/flow", response_model=FlowPageOut, dependencies=[admin_auth.AdminRequired])
async def flow(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    kind: str | None = Query(None, description="detected_kind: debt/payment/query/edit/archive/none"),
    person: str | None = Query(None, description="algılanan kişi adında geçen metin"),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    session: AsyncSession = Depends(get_session),
):
    """Zaman sıralı (en yeni üstte) mesaj akışı: ham metin → algılanan →
    sonuç. Filtreler birleşiktir (hepsi AND)."""
    if kind is not None and kind not in message_trace.DETECTED_KINDS:
        raise HTTPException(422, f"Geçersiz tür: {kind}")

    tx = aliased(Transaction)
    person_tbl = aliased(Person)

    filters = []
    if kind is not None:
        filters.append(RawMessage.detected_kind == kind)
    if person:
        filters.append(RawMessage.detected_person.ilike(f"%{person}%"))
    if date_from is not None:
        filters.append(func.date(RawMessage.received_at) >= date_from)
    if date_to is not None:
        filters.append(func.date(RawMessage.received_at) <= date_to)

    total = (
        await session.execute(select(func.count()).select_from(RawMessage).where(*filters))
    ).scalar_one()

    stmt = (
        select(RawMessage, tx, person_tbl.full_name)
        .outerjoin(tx, tx.id == RawMessage.transaction_id)
        .outerjoin(person_tbl, person_tbl.id == tx.person_id)
        .where(*filters)
        .order_by(RawMessage.received_at.desc(), RawMessage.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()

    return FlowPageOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[_row_out(raw, tx_row, person_name) for raw, tx_row, person_name in rows],
    )


def _row_out(raw: RawMessage, tx_row: Transaction | None, person_name: str | None) -> FlowRowOut:
    return FlowRowOut(
        id=raw.id,
        received_at=raw.received_at,
        processed_at=raw.processed_at,
        channel=raw.channel,
        chat_id=raw.chat_id,
        text=message_trace.payload_text(raw.payload),
        detected_kind=raw.detected_kind,
        detected_person=raw.detected_person,
        detected_amount=raw.detected_amount,
        detected_product=raw.detected_product,
        detected_qty=raw.detected_qty,
        detected_unit=raw.detected_unit,
        parse_source=raw.parse_source,
        parse_ms=raw.parse_ms,
        outcome=raw.outcome,
        outcome_detail=raw.outcome_detail,
        transaction=(
            FlowTxOut(
                id=tx_row.id,
                kind=tx_row.kind.value,
                amount_try=tx_row.amount_try,
                person_id=tx_row.person_id,
                person_name=person_name or "—",
                occurred_at=tx_row.occurred_at,
            )
            if tx_row is not None
            else None
        ),
    )
