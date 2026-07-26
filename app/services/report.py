"""PDF raporlar (Faz 4b, CLAUDE.md > "Faz 4b — PDF Raporlar").

Şablon (ReportDoc, ozet_kutulari, tablo, money, para) rapor_taslak.py'den
AYNEN alınmıştır — görünüm onaylandı, değiştirilmedi. Bu modül yalnızca
taslaktaki örnek veriyi gerçek DB sorgularıyla değiştirir. Bakiye/açık
kalem hesabı mevcut ledger/queries mantığıyla aynı (yeniden yazılmadı):
`ledger.balance_of` ve `queries.list_persons_with_balance` kullanılır.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Person, Setting, Transaction, TransactionLine, TxKind, TxStatus
from app.services import queries
from app.services.ledger import balance_of

_KURUS = Decimal("0.01")


def _round(v: Decimal) -> Decimal:
    """Kuruşa yuvarla. `money()`'den farklı: string değil Decimal döner,
    stats nesnelerinde toplama/çıkarma (net hesabı) için kullanılır."""
    return v.quantize(_KURUS)

TR_TZ = ZoneInfo("Europe/Istanbul")


class ReportError(Exception):
    """Rapor üretilemedi (örn. kişi bulunamadı)."""


# ---------------------------------------------------------------- font
# Dağıtıma göre DejaVu Sans farklı yerde olabilir (Debian/Ubuntu vs Fedora).
_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",    # Debian/Ubuntu
    "/usr/share/fonts/dejavu-sans-fonts",  # Fedora/RHEL
    "/usr/share/fonts/dejavu",             # bazı dağıtımlar
]


def _register_fonts() -> None:
    for d in _FONT_DIRS:
        regular = os.path.join(d, "DejaVuSans.ttf")
        bold = os.path.join(d, "DejaVuSans-Bold.ttf")
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("Hesap", regular))
            pdfmetrics.registerFont(TTFont("Hesap-Bold", bold))
            return
    raise RuntimeError(
        "DejaVu Sans fontu bulunamadı (PDF rapor için gerekli). "
        f"Denenen yollar: {', '.join(_FONT_DIRS)}"
    )


_register_fonts()

# ---------------------------------------------------------------- renkler
# rapor_taslak.py'den aynen: az ama anlamlı — lacivert kurumsal, kırmızı
# borç, yeşil alacak, gri çizgi.
NAVY = colors.HexColor("#1e3a5f")
BORC = colors.HexColor("#c0261d")      # borç kırmızı
ALACAK = colors.HexColor("#1d6b3c")    # alacak/tahsilat yeşil
INK = colors.HexColor("#1a1c20")
SOFT = colors.HexColor("#5c6470")
RULE = colors.HexColor("#d8dae0")
ZEBRA = colors.HexColor("#f4f6f9")     # tek/çift satır ayrımı
HEAD_BG = colors.HexColor("#1e3a5f")
CHIP_BG = colors.HexColor("#eef2f7")

TL = "₺"  # ₺


def para(text, size=9, color=INK, bold=False, align=0):
    return Paragraph(
        text,
        ParagraphStyle(
            "p", fontName="Hesap-Bold" if bold else "Hesap",
            fontSize=size, textColor=color, leading=size * 1.35, alignment=align,
        ),
    )


def money(v):
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {TL}"


class ReportDoc(BaseDocTemplate):
    """Ortak şablon: her sayfada aynı header ve footer."""

    def __init__(self, path, isletme, baslik, alt_baslik, vurgu="ust", **kw):
        super().__init__(path, pagesize=A4,
                         topMargin=38 * mm, bottomMargin=20 * mm,
                         leftMargin=16 * mm, rightMargin=16 * mm, **kw)
        self.isletme = isletme
        self.baslik = baslik
        self.alt_baslik = alt_baslik
        self.vurgu = vurgu
        frame = Frame(self.leftMargin, self.bottomMargin,
                      self.width, self.height, id="main")
        self.addPageTemplates([PageTemplate(id="tpl", frames=[frame],
                                            onPage=self._decor)])

    def _decor(self, canvas, doc):
        w, h = A4
        # ---- üst şerit (kurumsal bant)
        canvas.setFillColor(NAVY)
        canvas.rect(0, h - 30 * mm, w, 30 * mm, fill=1, stroke=0)
        # logo yeri: sol üstte "kaşe" kutusu
        canvas.setFillColor(colors.white)
        canvas.setFont("Hesap-Bold", 15)
        canvas.drawString(16 * mm, h - 15 * mm, self.isletme)
        canvas.setFont("Hesap", 8.5)
        canvas.setFillColor(colors.HexColor("#c3d0e0"))
        canvas.drawString(16 * mm, h - 20 * mm, "Cari Hesap Raporu")
        # sağ üst: rapor başlığı + alt başlık. vurgu='alt' ise alt satır
        # (kişi adı gibi) büyük ve beyaz; değilse üst başlık büyük.
        if getattr(self, "vurgu", "ust") == "alt":
            canvas.setFont("Hesap", 9)
            canvas.setFillColor(colors.HexColor("#c3d0e0"))
            canvas.drawRightString(w - 16 * mm, h - 13 * mm, self.baslik)
            canvas.setFont("Hesap-Bold", 15)
            canvas.setFillColor(colors.white)
            canvas.drawRightString(w - 16 * mm, h - 20 * mm, self.alt_baslik)
        else:
            canvas.setFont("Hesap-Bold", 12)
            canvas.setFillColor(colors.white)
            canvas.drawRightString(w - 16 * mm, h - 14 * mm, self.baslik)
            canvas.setFont("Hesap", 8.5)
            canvas.setFillColor(colors.HexColor("#c3d0e0"))
            canvas.drawRightString(w - 16 * mm, h - 19.5 * mm, self.alt_baslik)
        # ---- alt bilgi
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(16 * mm, 15 * mm, w - 16 * mm, 15 * mm)
        canvas.setFont("Hesap", 7.5)
        canvas.setFillColor(SOFT)
        uretim = datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M")
        canvas.drawString(16 * mm, 11 * mm, f"Oluşturulma: {uretim}")
        canvas.drawCentredString(w / 2, 11 * mm, "Hesaplık")
        canvas.drawRightString(w - 16 * mm, 11 * mm, f"Sayfa {doc.page}")


def ozet_kutulari(veriler):
    """Üstte yan yana renkli özet kutuları: (etiket, değer, renk)."""
    cells = []
    for etiket, deger, renk in veriler:
        inner = Table(
            [[para(etiket, 7.5, SOFT)], [para(deger, 13, renk, bold=True)]],
            colWidths=[(178 / len(veriler)) * mm],
        )
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CHIP_BG),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, 0), 7),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
            ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ]))
        cells.append(inner)
    t = Table([cells], colWidths=[(182 / len(veriler)) * mm] * len(veriler))
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def tablo(headers, rows, col_widths, renk_sutun=None):
    """Zebra desenli, başlığı lacivert tablo. renk_sutun: bakiye sütununu
    değerine göre kırmızı/yeşil boyamak için (sütun index, değer listesi)."""
    data = [[para(h, 8, colors.white, bold=True,
                  align=(2 if i >= len(headers) - 1 else 0))
             for i, h in enumerate(headers)]]
    for r in rows:
        data.append(r)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


# ---------------------------------------------------------------- yardımcılar
_AY_KISA = {
    1: "Oca", 2: "Şub", 3: "Mar", 4: "Nis", 5: "May", 6: "Haz",
    7: "Tem", 8: "Ağu", 9: "Eyl", 10: "Eki", 11: "Kas", 12: "Ara",
}
_AY_TAM = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}

_SLUG_MAP = str.maketrans({
    "ç": "c", "Ç": "C", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I",
    "ö": "o", "Ö": "O", "ş": "s", "Ş": "S", "ü": "u", "Ü": "U",
})


def today_tr() -> date:
    """İşletme İzmir'de; "bugün" Türkiye takvim günü demektir, UTC değil."""
    return datetime.now(TR_TZ).date()


def slugify(text: str) -> str:
    """Dosya adı için ASCII-güvenli kısaltma: "Ahmet Yılmaz" -> "ahmet_yilmaz"."""
    ascii_text = text.translate(_SLUG_MAP)
    ascii_text = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")
    return ascii_text.lower() or "kisi"


def _tarih_uzun(d: date) -> str:
    return f"{d.day} {_AY_TAM[d.month]} {d.year}"


def _tarih_kisa(dt: datetime) -> str:
    d = dt.astimezone(TR_TZ)
    return f"{d.day} {_AY_KISA[d.month]} {d.year}"


def _saat_str(dt: datetime) -> str:
    return dt.astimezone(TR_TZ).strftime("%H:%M")


def _qty_fmt(value: Decimal) -> str:
    raw = f"{value:,.3f}"
    if "." in raw:
        int_part, dec_part = raw.split(".")
        dec_part = dec_part.rstrip("0")
        raw = int_part if not dec_part else f"{int_part}.{dec_part}"
    return raw.replace(",", "X").replace(".", ",").replace("X", ".")


def _item_label(name: str, q: Decimal, unit: str) -> str:
    durum = "borçlu" if q > 0 else "alacaklı"
    return f"{_qty_fmt(abs(q))} {unit} {name.lower()} {durum}"


async def isletme_adi(session: AsyncSession) -> str:
    setting = await session.get(Setting, "business_name")
    return setting.value if setting else "Hesaplık"


# ================================================================ RAPOR 1: günlük

@dataclass(slots=True)
class DailyStats:
    gun: date
    count: int
    toplam_borc: Decimal
    toplam_tahsilat: Decimal

    @property
    def net(self) -> Decimal:
        return self.toplam_borc - self.toplam_tahsilat


async def _gunluk_hareketler(session: AsyncSession, gun: date) -> list[tuple[Transaction, Person]]:
    start = datetime.combine(gun, time.min, tzinfo=TR_TZ)
    end = start + timedelta(days=1)
    stmt = (
        select(Transaction, Person)
        .join(Person, Person.id == Transaction.person_id)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.product))
        .where(
            Transaction.status == TxStatus.CONFIRMED,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
        )
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
    )
    return list((await session.execute(stmt)).all())


def _daily_stats(gun: date, rows: list[tuple[Transaction, Person]]) -> DailyStats:
    toplam_borc = _round(sum((tx.amount_try for tx, _ in rows if tx.kind == TxKind.DEBIT), Decimal("0.00")))
    toplam_tahsilat = _round(
        sum((tx.amount_try for tx, _ in rows if tx.kind == TxKind.CREDIT), Decimal("0.00"))
    )
    return DailyStats(gun=gun, count=len(rows), toplam_borc=toplam_borc, toplam_tahsilat=toplam_tahsilat)


async def gunluk_ozet(session: AsyncSession, gun: date | None = None) -> DailyStats:
    """Telegram kısa özeti gibi PDF'siz kullanım için (aynı sorgu, tek kaynak)."""
    gun = gun or today_tr()
    rows = await _gunluk_hareketler(session, gun)
    return _daily_stats(gun, rows)


def _gunluk_urun_miktar(lines: list[TransactionLine]) -> tuple[str, str]:
    if not lines:
        return "—", "—"
    urun = " · ".join(li.product.name for li in lines)
    miktar = " · ".join(f"{_qty_fmt(li.qty)} {li.unit}" for li in lines)
    return urun, miktar


async def rapor_gunluk(session: AsyncSession, isletme: str, gun: date | None = None) -> bytes:
    """Bugün (veya verilen gün) kaydedilen hareketler."""
    gun = gun or today_tr()
    rows = await _gunluk_hareketler(session, gun)
    stats = _daily_stats(gun, rows)

    buf = io.BytesIO()
    doc = ReportDoc(buf, isletme, "Günlük Rapor", _tarih_uzun(gun))
    story = [Spacer(1, 4)]

    net = stats.net
    story.append(ozet_kutulari([
        ("KAYIT SAYISI", str(stats.count), NAVY),
        ("TOPLAM BORÇ", money(stats.toplam_borc), BORC),
        ("TOPLAM TAHSİLAT", money(stats.toplam_tahsilat), ALACAK),
        ("NET", money(net), BORC if net >= 0 else ALACAK),
    ]))
    story.append(Spacer(1, 14))

    if rows:
        table_rows = []
        for tx, person in rows:
            renk = BORC if tx.kind == TxKind.DEBIT else ALACAK
            urun, miktar = _gunluk_urun_miktar(tx.lines)
            table_rows.append([
                para(_saat_str(tx.occurred_at), 8.5, SOFT),
                para(person.full_name, 8.5, INK, bold=True),
                para(urun, 8.5),
                para(miktar, 8.5),
                para("Borç" if tx.kind == TxKind.DEBIT else "Tahsilat", 8.5, renk, bold=True),
                para(("+" if renk == BORC else "−") + money(tx.amount_try), 8.5, renk,
                     bold=True, align=2),
            ])
        story.append(tablo(
            ["Saat", "Kişi", "Ürün", "Miktar", "Tür", "Tutar"],
            table_rows, [16 * mm, 42 * mm, 30 * mm, 28 * mm, 26 * mm, 40 * mm]))
    else:
        story.append(para("Bugün için kayıt yok.", 9, SOFT))

    doc.build(story)
    return buf.getvalue()


# ================================================================ RAPOR 2: kişi ekstresi

async def _kisi_hareketler(session: AsyncSession, person_id: int) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.product))
        .where(Transaction.person_id == person_id, Transaction.status == TxStatus.CONFIRMED)
        .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
    )
    return list((await session.execute(stmt)).scalars())


def _lines_aciklama(lines: list[TransactionLine]) -> tuple[str, str]:
    aciklama = " · ".join(f"{li.product.name} {_qty_fmt(li.qty)} {li.unit}" for li in lines)
    birim = money(lines[0].unit_price) if len(lines) == 1 else "—"
    return aciklama, birim


async def rapor_kisi(session: AsyncSession, isletme: str, person_id: int) -> bytes:
    """Bir kişinin tüm hareketleri + yürüyen bakiye."""
    person = await session.get(Person, person_id)
    if person is None:
        raise ReportError(f"Kişi bulunamadı: {person_id}")

    bal = await balance_of(session, person_id)
    txs = await _kisi_hareketler(session, person_id)

    buf = io.BytesIO()
    doc = ReportDoc(buf, isletme, "Kişi Ekstresi", person.full_name, vurgu="alt")
    story = [Spacer(1, 4)]

    iletisim = " · ".join(
        filter(None, [person.phone, " / ".join(filter(None, [person.district, person.city]))])
    )
    baslik_satiri = f"<b>{person.full_name}</b>" + (f"  ·  {iletisim}" if iletisim else "")

    if bal.balance_try > 0:
        bakiye_renk, bakiye_isaret = BORC, "+"
    elif bal.balance_try < 0:
        bakiye_renk, bakiye_isaret = ALACAK, "−"
    else:
        bakiye_renk, bakiye_isaret = INK, ""

    bilgi = Table([[
        para(baslik_satiri, 9, INK),
        para("Güncel Bakiye", 7.5, SOFT, align=2),
    ], [
        para("", 1),
        para(bakiye_isaret + money(bal.balance_try), 15, bakiye_renk, bold=True, align=2),
    ]], colWidths=[120 * mm, 62 * mm])
    bilgi.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CHIP_BG),
        ("SPAN", (0, 0), (0, 1)),
        ("VALIGN", (0, 0), (0, 1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(bilgi)
    story.append(Spacer(1, 6))

    acik = " · ".join(_item_label(n, q, u) for n, q, u in bal.items) if bal.items else "yok"
    story.append(para(f"Açık Kalemler:  {acik}", 8.5, NAVY, bold=True))
    story.append(Spacer(1, 12))

    if txs:
        rows = []
        running = Decimal("0.00")
        for t in txs:
            running += t.amount_try if t.kind == TxKind.DEBIT else -t.amount_try
            renk = BORC if t.kind == TxKind.DEBIT else ALACAK
            if t.lines:
                aciklama, birim = _lines_aciklama(t.lines)
            else:
                aciklama = t.note or ("Borç" if t.kind == TxKind.DEBIT else "Tahsilat")
                birim = "—"
            bakiye_renk_row = BORC if running > 0 else ALACAK if running < 0 else INK
            bakiye_isaret_row = "+" if running > 0 else "−" if running < 0 else ""
            rows.append([
                para(_tarih_kisa(t.occurred_at), 8.5, SOFT),
                para(aciklama, 8.5, INK),
                para(birim, 8.5, SOFT, align=2),
                para("Borç" if t.kind == TxKind.DEBIT else "Tahsilat", 8.5, renk, bold=True),
                para(("+" if renk == BORC else "−") + money(t.amount_try), 8.5, renk, align=2),
                para(bakiye_isaret_row + money(running), 8.5, bakiye_renk_row, bold=True, align=2),
            ])
        story.append(tablo(
            ["Tarih", "Açıklama", "Birim Fiyat", "Tür", "Tutar", "Bakiye"],
            rows, [26 * mm, 50 * mm, 24 * mm, 22 * mm, 30 * mm, 30 * mm]))
    else:
        story.append(para("Hareket yok.", 9, SOFT))

    doc.build(story)
    return buf.getvalue()


# ================================================================ RAPOR 3: genel durum

@dataclass(slots=True)
class GeneralStats:
    kisi_sayisi: int
    toplam_alacak: Decimal
    toplam_borc: Decimal

    @property
    def net_alacak(self) -> Decimal:
        return self.toplam_alacak - self.toplam_borc


def _general_stats(rows: list[queries.PersonBalanceRow]) -> GeneralStats:
    toplam_alacak = _round(sum((r.balance_try for r in rows if r.balance_try > 0), Decimal("0.00")))
    toplam_borc = _round(sum((abs(r.balance_try) for r in rows if r.balance_try < 0), Decimal("0.00")))
    return GeneralStats(kisi_sayisi=len(rows), toplam_alacak=toplam_alacak, toplam_borc=toplam_borc)


async def genel_ozet(session: AsyncSession) -> GeneralStats:
    rows = await queries.list_persons_with_balance(session, scope="all")
    return _general_stats(rows)


async def rapor_genel(session: AsyncSession, isletme: str) -> bytes:
    """Tüm aktif kişiler, borçlu çoktan aza sıralı."""
    rows = await queries.list_persons_with_balance(session, scope="all")
    stats = _general_stats(rows)

    buf = io.BytesIO()
    doc = ReportDoc(buf, isletme, "Genel Durum", _tarih_uzun(today_tr()))
    story = [Spacer(1, 4)]

    net = stats.net_alacak
    story.append(ozet_kutulari([
        ("KİŞİ SAYISI", str(stats.kisi_sayisi), NAVY),
        ("TOPLAM ALACAK", money(stats.toplam_alacak), BORC),
        ("TOPLAM BORÇ", money(stats.toplam_borc), ALACAK),
        ("NET ALACAK", money(net), BORC if net >= 0 else ALACAK),
    ]))
    story.append(Spacer(1, 14))

    if rows:
        table_rows = []
        for row in rows:
            bal = row.balance_try
            renk = BORC if bal > 0 else ALACAK if bal < 0 else INK
            durum = "Borçlu" if bal > 0 else "Alacaklı" if bal < 0 else "Sıfır"
            isaret = "+" if bal > 0 else "−" if bal < 0 else ""
            acik = " · ".join(_item_label(n, q, u) for n, q, u in row.items) if row.items else "—"
            table_rows.append([
                para(row.person.full_name, 8.5, INK, bold=True),
                para(row.person.district or "—", 8.5, SOFT),
                para(acik, 8.5, SOFT),
                para(durum, 8.5, renk, bold=True),
                para(isaret + money(bal), 9, renk, bold=True, align=2),
            ])
        story.append(tablo(
            ["Kişi", "İlçe", "Açık Kalem", "Durum", "Bakiye"],
            table_rows, [44 * mm, 28 * mm, 46 * mm, 24 * mm, 40 * mm]))
    else:
        story.append(para("Kayıtlı kişi yok.", 9, SOFT))

    doc.build(story)
    return buf.getvalue()
