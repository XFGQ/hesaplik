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

- "LLM İzleme" (`/llm-monitor`): raw_messages'tan yalnızca `parse_source='llm'`
  düşen satırlar + özet istatistik (bkz. app/services/message_trace.py).
  "LLM Yönetimi"nden (yukarıda) farklı: o bir switch (hangi motor aktif),
  bu bir analitik (motor devreye ne sıklıkla, ne sürede, ne sonuçla giriyor).
- "İstek Kuyruğu" (`/queue`): pending_requests tablosu (bkz.
  app/services/request_queue.py) — bekleyen/yarım/başarısız çoklu istekler.
- "Kişiler & İşlemler" (`/persons`, `/persons/{id}/transactions`,
  `/archived-persons`, `/archived-transactions`): salt okunur veri gezgini,
  canlı defter + arşiv.
- "Loglar" (`/audit-log`): audit_log tablosu — DB denetim kaydı (kim ne
  zaman neyi değiştirdi), container stdout logları DEĞİL.
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
from sqlalchemy.orm import aliased, selectinload

from app.config import settings
from app.db import get_session
from app.models import (
    ArchivedPerson,
    ArchivedTransaction,
    AuditLog,
    PendingRequest,
    Person,
    RawMessage,
    Transaction,
    TransactionLine,
)
from app.services import admin_auth, backup, health, message_trace, queries, request_queue, restore

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


# ---------------------------------------------------------------- LLM izleme

class LlmMonitorRowOut(BaseModel):
    id: int
    received_at: datetime
    text: str | None
    detected_kind: str | None
    detected_person: str | None
    parse_ms: int | None
    outcome: str | None
    outcome_detail: str | None


class LlmMonitorStatsOut(BaseModel):
    total_calls: int             # filtreyle eşleşen toplam LLM çağrısı
    avg_parse_ms: float | None
    success_count: int
    failure_count: int
    success_rate: float | None   # 0..1, total_calls=0 ise None


class LlmMonitorPageOut(BaseModel):
    stats: LlmMonitorStatsOut
    total: int
    limit: int
    offset: int
    items: list[LlmMonitorRowOut]


# ---------------------------------------------------------------- istek kuyruğu

class QueueRowOut(BaseModel):
    id: int
    chat_id: str
    batch_id: str
    raw_text: str
    sira_no: int
    durum: str
    sonuc: str | None
    hata: str | None
    created_at: datetime
    updated_at: datetime


class QueueCountsOut(BaseModel):
    beklemede: int
    isleniyor: int
    tamamlandi: int
    basarisiz: int
    iptal: int


class QueuePageOut(BaseModel):
    counts: QueueCountsOut
    total: int
    limit: int
    offset: int
    items: list[QueueRowOut]


# ---------------------------------------------------------------- kişiler & işlemler

class AdminItemOut(BaseModel):
    product_name: str
    qty: Decimal
    unit: str


class AdminPersonRowOut(BaseModel):
    id: int
    full_name: str
    phone: str | None
    city: str | None
    district: str | None
    balance_try: Decimal
    items: list[AdminItemOut]
    last_activity: datetime | None


class AdminPersonListOut(BaseModel):
    total: int
    items: list[AdminPersonRowOut]


class AdminTxLineOut(BaseModel):
    product_name: str
    qty: Decimal
    unit: str
    unit_price: Decimal
    line_total: Decimal


class AdminTxRowOut(BaseModel):
    id: int
    kind: str
    status: str
    amount_try: Decimal
    occurred_at: datetime
    source: str
    note: str | None
    reverses_id: int | None
    lines: list[AdminTxLineOut]


class AdminPersonTransactionsOut(BaseModel):
    person_id: int
    person_name: str
    total: int
    items: list[AdminTxRowOut]


class ArchivedPersonRowOut(BaseModel):
    id: int
    original_person_id: int
    full_name: str
    phone: str | None
    city: str | None
    district: str | None
    balance_try: Decimal
    archived_by: str
    archived_at: datetime
    archive_reason: str | None


class ArchivedPersonPageOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ArchivedPersonRowOut]


class ArchivedTransactionRowOut(BaseModel):
    id: int
    person_id: int
    kind: str
    amount_try: Decimal
    occurred_at: datetime
    note: str | None
    archived_by: str
    archived_at: datetime
    archive_reason: str | None


class ArchivedTransactionPageOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ArchivedTransactionRowOut]


# ---------------------------------------------------------------- loglar

class AuditLogRowOut(BaseModel):
    id: int
    actor: str
    action: str
    entity: str
    entity_id: str | None
    before: dict | None
    after: dict | None
    trace_id: str | None
    at: datetime


class AuditLogPageOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditLogRowOut]


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


# ---------------------------------------------------------------- LLM izleme

# "Başarı": LLM kullanılabilir bir sonuç üretti (kaydetti, cevapladı ya da
# belirsizlik için soru sordu — üçü de LLM'in işini yaptığı anlamına gelir).
# "Başarısızlık": hata verdi ya da hiçbir şey anlaşılamadı.
_LLM_SUCCESS_OUTCOMES = (
    message_trace.OUTCOME_RECORDED,
    message_trace.OUTCOME_ANSWERED,
    message_trace.OUTCOME_ASKED,
)
_LLM_FAILURE_OUTCOMES = (message_trace.OUTCOME_ERROR, message_trace.OUTCOME_IGNORED)


@router.get(
    "/llm-monitor", response_model=LlmMonitorPageOut, dependencies=[admin_auth.AdminRequired]
)
async def llm_monitor(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    outcome: str | None = Query(None),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    session: AsyncSession = Depends(get_session),
):
    """Yalnızca LLM'e düşen (`parse_source='llm'`) mesajlar: süre, sonuç,
    cümle. İstatistik FİLTRELENMİŞ kümenin tamamı üzerinden hesaplanır,
    yalnız görünen sayfa üzerinden değil — aksi halde "başarı oranı" sayfa
    değiştikçe anlamsız zıplardı."""
    if outcome is not None and outcome not in (*_LLM_SUCCESS_OUTCOMES, *_LLM_FAILURE_OUTCOMES):
        raise HTTPException(422, f"Geçersiz sonuç: {outcome}")

    filters = [RawMessage.parse_source == message_trace.SOURCE_LLM]
    if outcome is not None:
        filters.append(RawMessage.outcome == outcome)
    if date_from is not None:
        filters.append(func.date(RawMessage.received_at) >= date_from)
    if date_to is not None:
        filters.append(func.date(RawMessage.received_at) <= date_to)

    total_calls, avg_ms, success_count, failure_count = (
        await session.execute(
            select(
                func.count(),
                func.avg(RawMessage.parse_ms),
                func.count().filter(RawMessage.outcome.in_(_LLM_SUCCESS_OUTCOMES)),
                func.count().filter(RawMessage.outcome.in_(_LLM_FAILURE_OUTCOMES)),
            ).where(*filters)
        )
    ).one()

    stmt = (
        select(RawMessage)
        .where(*filters)
        .order_by(RawMessage.received_at.desc(), RawMessage.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return LlmMonitorPageOut(
        stats=LlmMonitorStatsOut(
            total_calls=total_calls,
            avg_parse_ms=float(avg_ms) if avg_ms is not None else None,
            success_count=success_count,
            failure_count=failure_count,
            success_rate=(success_count / total_calls) if total_calls else None,
        ),
        total=total_calls,
        limit=limit,
        offset=offset,
        items=[
            LlmMonitorRowOut(
                id=r.id,
                received_at=r.received_at,
                text=message_trace.payload_text(r.payload),
                detected_kind=r.detected_kind,
                detected_person=r.detected_person,
                parse_ms=r.parse_ms,
                outcome=r.outcome,
                outcome_detail=r.outcome_detail,
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------- istek kuyruğu

@router.get("/queue", response_model=QueuePageOut, dependencies=[admin_auth.AdminRequired])
async def queue(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    durum: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Kalıcı çoklu-istek kuyruğu (bkz. app/services/request_queue.py):
    bekleyen, yarım kalmış ve başarısız olmuş istekler. Durum sayıları
    filtreden BAĞIMSIZ, kuyruğun tamamı üzerinden (üstte özet şerit)."""
    if durum is not None and durum not in request_queue.DURUMLAR:
        raise HTTPException(422, f"Geçersiz durum: {durum}")

    count_rows = (
        await session.execute(select(PendingRequest.durum, func.count()).group_by(PendingRequest.durum))
    ).all()
    counts = {d: 0 for d in request_queue.DURUMLAR}
    for d, c in count_rows:
        counts[d] = c

    filters = [PendingRequest.durum == durum] if durum is not None else []
    total = (
        await session.execute(select(func.count()).select_from(PendingRequest).where(*filters))
    ).scalar_one()

    stmt = (
        select(PendingRequest)
        .where(*filters)
        .order_by(PendingRequest.created_at.desc(), PendingRequest.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return QueuePageOut(
        counts=QueueCountsOut(
            beklemede=counts[request_queue.BEKLEMEDE],
            isleniyor=counts[request_queue.ISLENIYOR],
            tamamlandi=counts[request_queue.TAMAMLANDI],
            basarisiz=counts[request_queue.BASARISIZ],
            iptal=counts[request_queue.IPTAL],
        ),
        total=total,
        limit=limit,
        offset=offset,
        items=[
            QueueRowOut(
                id=r.id,
                chat_id=r.chat_id,
                batch_id=r.batch_id,
                raw_text=r.raw_text,
                sira_no=r.sira_no,
                durum=r.durum,
                sonuc=r.sonuc,
                hata=r.hata,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------- kişiler & işlemler

@router.get("/persons", response_model=AdminPersonListOut, dependencies=[admin_auth.AdminRequired])
async def admin_persons(
    q: str | None = None,
    filter: str = "all",
    district: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Canlı kişi listesi + bakiye + açık kalemler. Salt okunur — public
    `/api/persons` ile aynı sorgu fonksiyonunu kullanır, farkı admin şifresi
    arkasında olması."""
    try:
        rows = await queries.list_persons_with_balance(
            session, scope=filter, district=district, q=q, order="name"
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    items = [
        AdminPersonRowOut(
            id=row.person.id,
            full_name=row.person.full_name,
            phone=row.person.phone,
            city=row.person.city,
            district=row.person.district,
            balance_try=row.balance_try,
            items=[AdminItemOut(product_name=n, qty=qv, unit=u) for n, qv, u in row.items],
            last_activity=row.last_activity,
        )
        for row in rows
    ]
    return AdminPersonListOut(total=len(items), items=items)


@router.get(
    "/persons/{person_id}/transactions",
    response_model=AdminPersonTransactionsOut,
    dependencies=[admin_auth.AdminRequired],
)
async def admin_person_transactions(
    person_id: int,
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
):
    """Bir kişinin TÜM hareketleri (durumu ne olursa olsun — reddedilenler
    dahil, panel denetim amaçlı her şeyi görür). Salt okunur."""
    person = await session.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "Kişi bulunamadı")

    total = (
        await session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.person_id == person_id)
        )
    ).scalar_one()

    stmt = (
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.product))
        .where(Transaction.person_id == person_id)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(limit)
    )
    txs = list((await session.execute(stmt)).scalars())

    return AdminPersonTransactionsOut(
        person_id=person.id,
        person_name=person.full_name,
        total=total,
        items=[
            AdminTxRowOut(
                id=t.id,
                kind=t.kind.value,
                status=t.status.value,
                amount_try=t.amount_try,
                occurred_at=t.occurred_at,
                source=t.source.value,
                note=t.note,
                reverses_id=t.reverses_id,
                lines=[
                    AdminTxLineOut(
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
        ],
    )


@router.get(
    "/archived-persons", response_model=ArchivedPersonPageOut, dependencies=[admin_auth.AdminRequired]
)
async def archived_persons(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Silinen (arşivlenen) kişiler. "Yazarak onay her silmede" kararınca
    hiçbir şey gerçekten yok olmaz — burası o kayıtların denetim görünümü."""
    filters = [ArchivedPerson.full_name.ilike(f"%{q}%")] if q else []

    total = (
        await session.execute(select(func.count()).select_from(ArchivedPerson).where(*filters))
    ).scalar_one()

    stmt = (
        select(ArchivedPerson)
        .where(*filters)
        .order_by(ArchivedPerson.archived_at.desc(), ArchivedPerson.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return ArchivedPersonPageOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            ArchivedPersonRowOut(
                id=r.id,
                original_person_id=r.original_person_id,
                full_name=r.full_name,
                phone=r.phone,
                city=r.city,
                district=r.district,
                balance_try=r.balance_try,
                archived_by=r.archived_by,
                archived_at=r.archived_at,
                archive_reason=r.archive_reason,
            )
            for r in rows
        ],
    )


@router.get(
    "/archived-transactions",
    response_model=ArchivedTransactionPageOut,
    dependencies=[admin_auth.AdminRequired],
)
async def archived_transactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    person_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Silinen (arşivlenen) hareketler, istenirse tek kişiye daraltılır."""
    filters = [ArchivedTransaction.person_id == person_id] if person_id is not None else []

    total = (
        await session.execute(select(func.count()).select_from(ArchivedTransaction).where(*filters))
    ).scalar_one()

    stmt = (
        select(ArchivedTransaction)
        .where(*filters)
        .order_by(ArchivedTransaction.archived_at.desc(), ArchivedTransaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return ArchivedTransactionPageOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            ArchivedTransactionRowOut(
                id=r.id,
                person_id=r.person_id,
                kind=r.kind.value,
                amount_try=r.amount_try,
                occurred_at=r.occurred_at,
                note=r.note,
                archived_by=r.archived_by,
                archived_at=r.archived_at,
                archive_reason=r.archive_reason,
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------- loglar

@router.get("/audit-log", response_model=AuditLogPageOut, dependencies=[admin_auth.AdminRequired])
async def audit_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: str | None = Query(None),
    action: str | None = Query(None),
    entity: str | None = Query(None),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    session: AsyncSession = Depends(get_session),
):
    """Sistem denetim kayıtları: kim, ne zaman, neyi değiştirdi (bkz.
    AuditLog — restore, kişi düzenleme/arşivleme, ters kayıt vb. hepsi
    buraya yazar). Container stdout logları DEĞİL, yalnızca DB denetimi."""
    filters = []
    if actor:
        filters.append(AuditLog.actor == actor)
    if action:
        filters.append(AuditLog.action == action)
    if entity:
        filters.append(AuditLog.entity == entity)
    if date_from is not None:
        filters.append(func.date(AuditLog.at) >= date_from)
    if date_to is not None:
        filters.append(func.date(AuditLog.at) <= date_to)

    total = (
        await session.execute(select(func.count()).select_from(AuditLog).where(*filters))
    ).scalar_one()

    stmt = (
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return AuditLogPageOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            AuditLogRowOut(
                id=r.id,
                actor=r.actor,
                action=r.action,
                entity=r.entity,
                entity_id=r.entity_id,
                before=r.before,
                after=r.after,
                trace_id=r.trace_id,
                at=r.at,
            )
            for r in rows
        ],
    )
