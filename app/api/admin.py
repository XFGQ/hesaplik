"""Admin paneli uçları (/api/admin/*) — Faz 7.

Tümü `admin_auth.require_admin` arkasında: token yoksa/geçersizse 401, hiçbir
veri dönmez. Tek istisna `POST /login` (token'ı o üretir) ve `GET /me`
(oturumun geçerli olup olmadığını söyler, veri sızdırmaz).

Dolu bölümler:

- "İşlem Akışı" (`/flow`): raw_messages'a mesaj işlenirken yazılan izleme
  verisi (bkz. app/services/message_trace.py) zaman sıralı, filtrelenebilir
  ve sayfalanabilir biçimde dönülür.
- "Sistem Sağlığı" (`/health`): veritabanı, LLM, bot, yedek ve API durumu
  (bkz. app/services/health.py).
- "Yedekleme" (`/backups`, `/backups/restore`): yedek listesi ve "ana veri
  yap" akışı. Geri yüklemenin SON adımı (pg_restore) henüz bağlı değil,
  bkz. app/services/restore.py.

Kalan bölümler (LLM izleme, kuyruk, loglar...) arayüzde yer tutuyor, uçları
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

from app.config import settings
from app.db import get_session
from app.models import Person, RawMessage, Transaction
from app.services import admin_auth, backup, health, message_trace, restore

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


class HealthDetailOut(BaseModel):
    label: str
    value: str


class HealthComponentOut(BaseModel):
    id: str
    label: str
    status: str                # ok | uyari | hata | bilgi
    summary: str
    details: list[HealthDetailOut]
    measured: bool             # False: doğrudan ölçülmedi, dolaylı iz
    note: str | None


class HealthOut(BaseModel):
    checked_at: datetime
    overall: str
    overall_text: str
    components: list[HealthComponentOut]


# Adı app/schemas.py'deki BackupSnapshotOut'tan kasten farklı: orası defterin
# açık ucundaki sade liste (id/time/size), burası panelin ayrıntılı hâli.
class BackupItemOut(BaseModel):
    id: str                    # restic short_id — `restic dump <id>` ile aynı
    full_id: str
    time: datetime
    size_bytes: int | None     # restic vermezse null; arayüz "—" gösterir
    hostname: str | None
    tags: list[str]
    paths: list[str]


class BackupListOut(BaseModel):
    repository: str
    total: int
    last_time: datetime | None
    auto_interval_minutes: int
    next_auto_estimate: datetime | None    # TAHMİN: timer host'ta, ölçülemez
    restore_available: bool                # gerçek geri yükleme açık mı
    items: list[BackupItemOut]


class RestoreIn(BaseModel):
    snapshot_id: str
    password: str


class RestoreRequestOut(BaseModel):
    id: int
    snapshot_id: str
    status: str                       # bekliyor/yedekleniyor/yukleniyor/tamamlandi/hata
    requested_at: datetime
    requested_by: str
    pre_backup_snapshot: str | None   # restore öncesi güvenlik yedeği (host doldurur)
    started_at: datetime | None
    finished_at: datetime | None
    error_detail: str | None
    stale: bool                       # uzun süredir bekliyor: izleyici çalışmıyor olabilir


class RestoreStartOut(BaseModel):
    ok: bool
    message: str
    request: RestoreRequestOut


class RestoreStatusOut(BaseModel):
    active: bool                          # şu an süren bir geri yükleme var mı
    request: RestoreRequestOut | None     # aktif yoksa EN SON istek


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


# ---------------------------------------------------------------- sistem sağlığı

@router.get("/health", response_model=HealthOut, dependencies=[admin_auth.AdminRequired])
async def system_health(session: AsyncSession = Depends(get_session)):
    """Bileşen bileşen "ne ayakta, ne değil". Veritabanı çökmüş olsa bile bu
    uç 200 döner: her kontrol kendi hatasını yakalayıp durum olarak bildirir,
    yoksa "sistem sağlığı" sayfası tam da sorun varken açılmazdı.

    Herkese açık `/api/health`ten farkı: orası tek kelimelik bir canlılık
    sinyali, burası kurulum ayrıntısı (model adı, depo yolu, sayılar) —
    o yüzden admin şifresi arkasında."""
    report = await health.collect(session)
    return HealthOut(
        checked_at=report.checked_at,
        overall=report.overall,
        overall_text=report.overall_text,
        components=[
            HealthComponentOut(
                id=c.id,
                label=c.label,
                status=c.status,
                summary=c.summary,
                details=[HealthDetailOut(label=k, value=v) for k, v in c.details],
                measured=c.measured,
                note=c.note,
            )
            for c in report.components
        ],
    )


# ---------------------------------------------------------------- yedekleme

@router.get("/backups", response_model=BackupListOut, dependencies=[admin_auth.AdminRequired])
async def backups():
    """Yedek listesi, en yeni üstte. Depoya erişilemezse 503 + sebep: panel
    "yedek yok" ile "depo okunamıyor"u karıştırmasın, ikisi çok farklı."""
    try:
        items = await backup.snapshots()
    except backup.BackupUnavailable as e:
        raise HTTPException(503, str(e)) from e

    last_time = items[0].time if items else None
    return BackupListOut(
        repository=settings.restic_repository,
        total=len(items),
        last_time=last_time,
        auto_interval_minutes=backup.AUTO_INTERVAL_MINUTES,
        next_auto_estimate=backup.next_auto_estimate(last_time),
        restore_available=restore.is_available(),
        items=[
            BackupItemOut(
                id=s.id,
                full_id=s.full_id,
                time=s.time,
                size_bytes=s.size_bytes,
                hostname=s.hostname,
                tags=s.tags,
                paths=s.paths,
            )
            for s in items
        ],
    )


def _restore_out(req) -> RestoreRequestOut:
    return RestoreRequestOut(
        id=req.id,
        snapshot_id=req.snapshot_id,
        status=req.status,
        requested_at=req.requested_at,
        requested_by=req.requested_by,
        pre_backup_snapshot=req.pre_backup_snapshot,
        started_at=req.started_at,
        finished_at=req.finished_at,
        error_detail=req.error_detail,
        stale=restore.is_stale(req),
    )


@router.post(
    "/backups/restore", response_model=RestoreStartOut, dependencies=[admin_auth.AdminRequired]
)
async def restore_backup(
    body: RestoreIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """"Ana veri yap": seçilen yedeği canlı veritabanı yapma İSTEĞİ.

    İki kat koruma: oturum çerezi (AdminRequired) YETMEZ, şifre ISTEK
    GÖVDESİNDE tekrar sorulur. Açık kalmış bir panel sekmesi tek tıkla
    defterin üstüne yazamasın diye — bu, sistemdeki en yıkıcı işlem.
    Yanlış şifre giriş ekranıyla aynı kilide takılır.

    Yanlış şifre 401 DEĞİL 403 döner: 401 istemcide "oturum düştü" demektir
    ve paneli şifre ekranına atardı. Burada oturum geçerli, izin verilmeyen
    şey bu tek işlem — kullanıcı modalda "şifre hatalı" görüp tekrar dener.

    Geri yüklemeyi API YAPMAZ ("Yol A"): istek `restore_requests`e yazılır,
    host'taki izleyici (scripts/restore-apply.sh) önce güvenlik yedeği alıp
    sonra yükler. Zaten süren bir geri yükleme varsa 409."""
    key = _client_key(request)
    now = time.time()
    if _locked_out(key, now):
        raise HTTPException(429, "Çok fazla deneme, biraz sonra tekrar deneyin")

    if not admin_auth.check_password(body.password):
        _failures[key].append(now)
        raise HTTPException(403, "Şifre hatalı")
    _failures.pop(key, None)

    try:
        created = await restore.request_restore(
            session, snapshot_id=body.snapshot_id, actor=f"admin-panel@{key}"
        )
    except restore.UnknownSnapshot as e:
        raise HTTPException(404, "Bu kimlikte yedek bulunamadı") from e
    except restore.RestoreInProgress as e:
        raise HTTPException(409, "Zaten bir geri yükleme sürüyor") from e
    except backup.BackupUnavailable as e:
        raise HTTPException(503, str(e)) from e

    return RestoreStartOut(
        ok=True, message=restore.ACCEPTED_MESSAGE, request=_restore_out(created)
    )


@router.get(
    "/backups/restore/status",
    response_model=RestoreStatusOut,
    dependencies=[admin_auth.AdminRequired],
)
async def restore_status(session: AsyncSession = Depends(get_session)):
    """Süren geri yüklemenin durumu; yoksa EN SON isteğin sonucu. Panel bunu
    birkaç saniyede bir sorup ilerlemeyi gösterir — ilerlemeyi yazan taraf
    host izleyicidir, API yalnızca okur."""
    running = await restore.active_request(session)
    latest = running or await restore.latest_request(session)
    return RestoreStatusOut(
        active=running is not None,
        request=_restore_out(latest) if latest is not None else None,
    )


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
