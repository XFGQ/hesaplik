import secrets
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_session
from app.models import (
    AuditLog,
    Person,
    PriceHistory,
    Product,
    Setting,
    Transaction,
    TransactionLine,
    TxSource,
    TxStatus,
)
from app.schemas import (
    AdminLLMPreferenceIn,
    AdminLLMSourceOut,
    AdminLLMStatusOut,
    AdminVllmControlIn,
    AdminVllmControlOut,
    ArchiveIn,
    BackupRunOut,
    BackupSnapshotOut,
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
    VllmDesiredOut,
)
from app.services import backup, catalog, ledger, llm_provider, queries, report, vllm_control
from app.services.ledger import LedgerError, LineInput, TxMeta

router = APIRouter(prefix="/api")


def _actor() -> str:
    # Faz 3'te JWT'den gelecek.
    return "web"


# --------------------------------------------------------------- kişiler

@router.get("/persons", response_model=list[PersonRowOut])
async def list_persons(
    q: str | None = None,
    filter: str = "all",
    district: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Tablo tek istekte dolsun: bakiye ve açık kalemler dahil.

    filter: "all" | "debtors" | "creditors". district verilirse yalnızca o
    ilçedeki kişiler. Bot da (Telegram sorgu komutları) aynı sorgu
    fonksiyonunu kullanır (app/services/queries.py).
    """
    try:
        rows = await queries.list_persons_with_balance(
            session, scope=filter, district=district, q=q, order="name"
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    return [
        PersonRowOut(
            id=row.person.id,
            full_name=row.person.full_name,
            phone=row.person.phone,
            city=row.person.city,
            district=row.person.district,
            address=row.person.address,
            note=row.person.note,
            balance_try=row.balance_try,
            last_activity=row.last_activity,
            items=[ItemOut(product_name=n, qty=qty, unit=u) for n, qty, u in row.items],
        )
        for row in rows
    ]


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
    """Kişiyi listeden kaldırır (soft delete). Hareketleri silinmez; kullanıcı
    arayüzde yazarak onayladıktan sonra bakiye sıfır olmasa da silinebilir."""
    person = await session.get(Person, person_id)
    if person is None or not person.is_active:
        raise HTTPException(404, "Kişi bulunamadı")

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


# --------------------------------------------------------------- raporlar

def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/daily")
async def report_daily(
    gun: date | None = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_session),
):
    gun = gun or report.today_tr()
    isletme = await report.isletme_adi(session)
    pdf = await report.rapor_gunluk(session, isletme, gun=gun)
    return _pdf_response(pdf, f"rapor_gunluk_{gun.isoformat()}.pdf")


@router.get("/reports/general")
async def report_general(session: AsyncSession = Depends(get_session)):
    isletme = await report.isletme_adi(session)
    pdf = await report.rapor_genel(session, isletme)
    return _pdf_response(pdf, f"rapor_genel_{report.today_tr().isoformat()}.pdf")


@router.get("/reports/person/{person_id}")
async def report_person(person_id: int, session: AsyncSession = Depends(get_session)):
    person = await session.get(Person, person_id)
    if person is None or not person.is_active:
        raise HTTPException(404, "Kişi bulunamadı")
    isletme = await report.isletme_adi(session)
    pdf = await report.rapor_kisi(session, isletme, person_id)
    return _pdf_response(pdf, f"rapor_ekstre_{report.slugify(person.full_name)}.pdf")


# --------------------------------------------------------------- yedekleme

@router.get("/backups", response_model=list[BackupSnapshotOut])
async def list_backups():
    """Şimdilik kimliği doğrulanmış herkes görebilir; Faz 3'te yetki eklenecek."""
    try:
        snapshots = await backup.list_snapshots()
    except backup.BackupUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return [
        BackupSnapshotOut(
            id=s["short_id"],
            time=s["time"],
            size_bytes=s.get("summary", {}).get("total_bytes_processed", 0),
        )
        for s in snapshots
    ]


@router.post("/backups/run", response_model=BackupRunOut)
async def run_backup_now():
    """Kullanıcıdan gelen hiçbir parametre kabul etmez, sabit script çalıştırır."""
    try:
        ok, message, duration = await backup.run_backup()
    except backup.BackupUnavailable as e:
        raise HTTPException(503, str(e)) from e
    if not ok:
        raise HTTPException(500, message)
    return BackupRunOut(ok=ok, message=message, duration_seconds=duration)


# --------------------------------------------------------------- admin (LLM yönetimi)


def _check_admin_password(password: str | None) -> None:
    """settings.admin_password boşsa panel tamamen kapalıdır (503) —
    yanlışlıkla açık admin uç noktası kalmasın diye. Karşılaştırma sabit
    zamanlıdır (timing attack'e karşı)."""
    if not settings.admin_password:
        raise HTTPException(503, "Admin paneli yapılandırılmamış")
    if not password or not secrets.compare_digest(password, settings.admin_password):
        raise HTTPException(401, "Yetkisiz")


async def require_admin(x_admin_password: str | None = Header(default=None)) -> None:
    _check_admin_password(x_admin_password)


def _llm_status_out(status: llm_provider.LLMStatus) -> AdminLLMStatusOut:
    return AdminLLMStatusOut(
        primary=status.primary,
        active=status.active,
        nvidia=AdminLLMSourceOut(ok=status.nvidia.ok, url=status.nvidia.url, model=status.nvidia.model),
        vllm=AdminLLMSourceOut(ok=status.vllm.ok, url=status.vllm.url, model=status.vllm.model),
        ollama=AdminLLMSourceOut(ok=status.ollama.ok, url=status.ollama.url, model=status.ollama.model),
    )


@router.get("/admin/llm", response_model=AdminLLMStatusOut, dependencies=[Depends(require_admin)])
async def admin_llm_status(session: AsyncSession = Depends(get_session)):
    return _llm_status_out(await llm_provider.get_status(session))


@router.post("/admin/llm", response_model=AdminLLMStatusOut, dependencies=[Depends(require_admin)])
async def admin_llm_update(body: AdminLLMPreferenceIn, session: AsyncSession = Depends(get_session)):
    if body.llm_primary not in llm_provider.LLM_PRIMARY_VALUES:
        raise HTTPException(422, "Geçersiz tercih")
    await llm_provider.set_llm_primary(session, body.llm_primary)
    return _llm_status_out(await llm_provider.get_status(session))


# ----------------------------------------------------- vLLM cihaz aç/kapat ("Yol B")
#
# Bkz. app/services/vllm_control.py > modül docstring'i. Panel burada
# yalnızca bir TERCİH yazar (settings.vllm_desired); Bosna'daki host
# script'i bunu ayrı, token korumalı bir uçtan (aşağıdaki /vllm-desired)
# kendisi çeker ve uygular. Panel Bosna'ya hiçbir zaman doğrudan komut
# göndermez.


async def _vllm_control_out(desired: str) -> AdminVllmControlOut:
    reachable = await llm_provider.vllm_reachable_cached()
    return AdminVllmControlOut(
        desired=desired, reachable=reachable, pending=vllm_control.is_pending(desired, reachable)
    )


@router.get(
    "/admin/vllm-control", response_model=AdminVllmControlOut, dependencies=[Depends(require_admin)]
)
async def admin_vllm_control_status(session: AsyncSession = Depends(get_session)):
    return await _vllm_control_out(await vllm_control.get_vllm_desired(session))


@router.post(
    "/admin/vllm-control", response_model=AdminVllmControlOut, dependencies=[Depends(require_admin)]
)
async def admin_vllm_control_update(
    body: AdminVllmControlIn, request: Request, session: AsyncSession = Depends(get_session)
):
    if body.desired not in vllm_control.VLLM_DESIRED_VALUES:
        raise HTTPException(422, "Geçersiz tercih")

    before = await vllm_control.get_vllm_desired(session)
    await vllm_control.set_vllm_desired(session, body.desired)
    session.add(
        AuditLog(
            actor=f"admin-panel@{request.client.host if request.client else 'bilinmeyen'}",
            action="set_vllm_desired",
            entity="settings",
            entity_id=vllm_control.VLLM_DESIRED_KEY,
            before={"value": before},
            after={"value": body.desired},
        )
    )
    await session.flush()

    return await _vllm_control_out(body.desired)


async def require_vllm_control_token(x_vllm_control_token: str | None = Header(default=None)) -> None:
    """Bosna'nın çektiği /vllm-desired ucunu korur. Admin şifresinden AYRI
    ve daha dar yetkili bir token — .env VLLM_CONTROL_TOKEN. Token
    yapılandırılmamışsa (boş) uç HER ZAMAN 401 döner (fail closed): kazara
    açık bir kontrol ucu kalmasın."""
    if (
        not settings.vllm_control_token
        or not x_vllm_control_token
        or not secrets.compare_digest(x_vllm_control_token, settings.vllm_control_token)
    ):
        raise HTTPException(401, "Yetkisiz")


@router.get(
    "/vllm-desired", response_model=VllmDesiredOut, dependencies=[Depends(require_vllm_control_token)]
)
async def vllm_desired(session: AsyncSession = Depends(get_session)):
    """Bosna'nın (scripts/vllm-control.sh, ~30 sn'de bir) çektiği uç. Admin
    şifresi İSTEMEZ — yalnızca yukarıdaki token yeterli, çünkü bu script
    tarayıcı oturumu değil bir sunucu-sunucu çağrısıdır."""
    return VllmDesiredOut(desired=await vllm_control.get_vllm_desired(session))
