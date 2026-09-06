"""Gelen mesajın uçtan uca izi: müşteri ne yazdı → sistem ne algıladı → ne yaptı.

Admin panelindeki "İşlem Akışı" bölümünün (Faz 7) veri kaynağı. `raw_messages`
şimdiye kadar yalnızca ham metni tutuyordu; parse çıktısı hiçbir yere
yazılmadığı için bir mesajın neden yanlış anlaşıldığı sonradan görülemiyordu.
Bu modül parse sonucunu ve işlem sonucunu aynı satıra YAN ETKİ olarak yazar.

İki kural:

1. **Ham metne dokunulmaz.** `payload`/`received_at` asla değişmez; yalnızca
   `detected_*`/`parse_*`/`outcome*` alanları doldurulur.
2. **İzleme defteri etkilemez.** Buradaki hiçbir şey bir kaydın yazılmasını
   ya da yazılmamasını değiştirmez; alanlar nullable, yazılamazsa akış aynen
   sürer (bkz. `safe_fill`).

Çok istekli mesajlarda (CLAUDE.md > "Tek mesajda birden çok istek") tek bir
Telegram güncellemesi = tek `raw_messages` satırı, ama birden çok işlem
vardır. Satır SON işlenen parçanın izini taşır; parça metni tam mesajdan
farklıysa `outcome_detail` başına `[parça: "..."]` eklenir ki panelde hangi
kısmın izlendiği görünsün.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.models import RawMessage
from app.services.intent_resolver import ResolvedIntent

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- sözlükler

# detected_kind: niyetin kaba sınıfı. Panelde rozet olarak görünür, filtrede
# kullanılır. Ayrıntı (hangi sorgu, hangi rapor) outcome_detail'de durur.
KIND_DEBT = "debt"
KIND_PAYMENT = "payment"
KIND_QUERY = "query"
KIND_EDIT = "edit"
KIND_ARCHIVE = "archive"
KIND_NONE = "none"

DETECTED_KINDS = (KIND_DEBT, KIND_PAYMENT, KIND_QUERY, KIND_EDIT, KIND_ARCHIVE, KIND_NONE)

_KIND_BY_INTENT = {
    "debt": KIND_DEBT,
    "payment": KIND_PAYMENT,
    "balance_query": KIND_QUERY,
    "list_all": KIND_QUERY,
    "list_debtors": KIND_QUERY,
    "list_creditors": KIND_QUERY,
    "list_district": KIND_QUERY,
    "search": KIND_QUERY,
    "report_menu": KIND_QUERY,
    "report_daily": KIND_QUERY,
    "report_general": KIND_QUERY,
    "report_person": KIND_QUERY,
    "person_contact": KIND_QUERY,
    "info_menu": KIND_QUERY,
    # Kişi kartına yazan işlemler: yeni kişi açma da alan güncelleme de
    # defteri (para) değil kişi bilgisini değiştirir.
    "create_person": KIND_EDIT,
    "edit_person": KIND_EDIT,
    "archive_person": KIND_ARCHIVE,
    "archive_and_recreate": KIND_ARCHIVE,
    # Koşan format matematiği tutmuyor: henüz borç mu tahsilat mı olacağı
    # kullanıcının cevabına bağlı, deftere hiçbir şey yazılmadı — bu yüzden
    # debt/payment değil, "sorgu" rozetiyle görünür.
    "running_mismatch": KIND_QUERY,
}

# parse_source: mesajı kim çözdü. message_processor içinde kural motoru
# "rule" diye anılır, panelde/veride "regex" olarak durur (kullanıcının
# istediği ad). Hiçbiri çözemediyse "none".
SOURCE_REGEX = "regex"
SOURCE_LLM = "llm"
SOURCE_NONE = "none"

# outcome: sistem sonuçta ne yaptı.
OUTCOME_RECORDED = "kaydedildi"     # deftere/kişi kartına yazıldı
OUTCOME_ANSWERED = "yanitlandi"     # salt okunur sorgu/rapor cevaplandı
OUTCOME_ASKED = "soru_soruldu"      # kullanıcıya soru soruldu, iş yarım
OUTCOME_ERROR = "hata"              # işleme sırasında hata
OUTCOME_IGNORED = "yok_sayildi"     # anlaşılamadı, hiçbir şey yapılmadı

# ProcessOutcome.value -> (outcome, kısa açıklama). message_processor'ı
# import etmemek için (döngüsel import olurdu) enum yerine değerleri
# kullanır; ProcessOutcome bir str enum'dır.
_OUTCOME_MAP: dict[str, tuple[str, str]] = {
    "recorded": (OUTCOME_RECORDED, "deftere yazıldı"),
    "create_person": (OUTCOME_RECORDED, "kişi kaydı"),
    "balance": (OUTCOME_ANSWERED, "bakiye gösterildi"),
    "list": (OUTCOME_ANSWERED, "liste gösterildi"),
    "search": (OUTCOME_ANSWERED, "arama sonucu gösterildi"),
    "report_daily": (OUTCOME_ANSWERED, "günlük rapor gönderildi"),
    "report_general": (OUTCOME_ANSWERED, "genel rapor gönderildi"),
    "report_person": (OUTCOME_ANSWERED, "kişi ekstresi gönderildi"),
    "person_contact": (OUTCOME_ANSWERED, "kişi bilgileri gösterildi"),
    "report_menu": (OUTCOME_ASKED, "hangi rapor? sorusu"),
    "info_menu": (OUTCOME_ASKED, "ne bilgisi? sorusu"),
    "needs_confirmation": (OUTCOME_ASKED, "hangi kişi? sorusu"),
    "person_not_found": (OUTCOME_ASKED, "kişi bulunamadı, ekleyeyim mi? sorusu"),
    "product_needs_confirmation": (OUTCOME_ASKED, "ürün önerisi onayı"),
    "llm_confirmation": (OUTCOME_ASKED, "bunu mu demek istediniz? onayı"),
    "archive_confirm": (OUTCOME_ASKED, "silme onayı (yazarak)"),
    "edit_person_confirm": (OUTCOME_ASKED, "düzenleme onayı"),
    "edit_person_menu": (OUTCOME_ASKED, "hangi alan? sorusu"),
    # Koşan format (CLAUDE.md > "Koşan format"): ikisi de bir SORUDUR,
    # hiçbir şey kaydedilmemiştir.
    "running_mismatch": (OUTCOME_ASKED, "koşan format matematiği sorusu"),
    "running_amount_needed": (OUTCOME_ASKED, "koşan format tutar sorusu"),
    "unrecognized": (OUTCOME_IGNORED, "anlaşılamadı"),
}

_MAX_TEXT = 500


# ---------------------------------------------------------------- yardımcılar

def payload_text(payload: dict | None) -> str | None:
    """raw_messages.payload (Telegram güncellemesi) içinden kullanıcının
    yazdığı metni çıkarır. Buton basımlarında (callback_query) metin yerine
    callback verisi döner — panelde "kullanıcı ne yaptı" yine görünsün."""
    if not isinstance(payload, dict):
        return None

    # Web tarafından/testlerden gelen düz {"text": ...} biçimi.
    direct = payload.get("text")
    if isinstance(direct, str):
        return direct

    # Web sesli mesajı (bkz. web_intake.save_web_voice_message): payload'da
    # metin YOKTUR, ses de saklanmaz. Çeviri başarılıysa display_text zaten
    # voice_transcript'i gösterir; başarısızsa panelde boş satır değil,
    # "burada bir ses vardı ama çevrilemedi" görünsün.
    if payload.get("voice") is True:
        return "(ses kaydı)"

    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        node = payload.get(key)
        if isinstance(node, dict):
            for field in ("text", "caption"):
                value = node.get(field)
                if isinstance(value, str):
                    return value
            if node.get("voice"):
                return "(ses kaydı)"

    callback = payload.get("callback_query")
    if isinstance(callback, dict) and isinstance(callback.get("data"), str):
        return f"(buton) {callback['data']}"

    return None


def display_text(raw: RawMessage) -> str | None:
    """Panelde gösterilecek "ham metin". Sesli mesajsa Groq'un çevirdiği
    metni (raw.voice_transcript) döner — bu, payload_text'in ses için
    döndürdüğü "(ses kaydı)" yer tutucusundan daha bilgilendiricidir.
    Metin mesajlarda (voice_transcript boş) davranış değişmez."""
    return raw.voice_transcript or payload_text(raw.payload)


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:_MAX_TEXT]


def _kind_of(resolved: ResolvedIntent | None) -> str:
    if resolved is None or resolved.kind is None:
        return KIND_NONE
    return _KIND_BY_INTENT.get(resolved.kind, KIND_QUERY)


def _person_of(resolved: ResolvedIntent | None) -> str | None:
    if resolved is None:
        return None
    if resolved.person is not None:
        return resolved.person.full_name
    # Kişi netleşmemiş olabilir ("hangisi?" soruldu) — ham isim yine değerli.
    return resolved.person_name_raw


def _product_of(resolved: ResolvedIntent | None) -> str | None:
    if resolved is None:
        return None
    if resolved.product is not None:
        return resolved.product.name
    return resolved.product_name_raw


def _detail(base: str, resolved: ResolvedIntent | None, extra: str | None) -> str:
    """Kısa açıklama: temel açıklama + niyetin ayrıntısı + varsa ek."""
    parts = [base]
    if resolved is not None and resolved.kind:
        parts.append(f"niyet: {resolved.kind}")
    if extra:
        parts.append(extra)
    return " · ".join(parts)


# ---------------------------------------------------------------- yazma

def fill(
    raw: RawMessage,
    *,
    resolved: ResolvedIntent | None,
    outcome: str,
    source: str,
    parse_ms: int | None,
    text: str | None = None,
    extra: str | None = None,
) -> None:
    """İzleme alanlarını doldurur. `outcome` ProcessOutcome'ın değeridir
    (ör. "recorded"); tanınmayan bir değer gelirse "hata" sayılır ki panelde
    sessizce kaybolmasın."""
    mapped, base = _OUTCOME_MAP.get(outcome, (OUTCOME_ERROR, f"bilinmeyen sonuç: {outcome}"))

    raw.detected_kind = _kind_of(resolved)
    raw.detected_person = _clip(_person_of(resolved))
    raw.detected_amount = resolved.amount if resolved is not None else None
    raw.detected_product = _clip(_product_of(resolved))
    raw.detected_qty = _round_qty(resolved.qty if resolved is not None else None)
    raw.detected_unit = _clip(resolved.unit if resolved is not None else None)
    raw.parse_source = source
    raw.parse_ms = parse_ms
    raw.outcome = mapped
    raw.outcome_detail = _clip(_detail(base, resolved, _piece_note(raw, text) or extra))


def fill_error(
    raw: RawMessage,
    *,
    resolved: ResolvedIntent | None,
    source: str,
    parse_ms: int | None,
    error: str,
    text: str | None = None,
) -> None:
    """İşleme sırasında istisna oluştuğunda çağrılır. Kayıt geri alınmış
    olabilir; izleme satırı yine de "hata" olarak görünmelidir."""
    raw.detected_kind = _kind_of(resolved)
    raw.detected_person = _clip(_person_of(resolved))
    raw.detected_amount = resolved.amount if resolved is not None else None
    raw.detected_product = _clip(_product_of(resolved))
    raw.detected_qty = _round_qty(resolved.qty if resolved is not None else None)
    raw.detected_unit = _clip(resolved.unit if resolved is not None else None)
    raw.parse_source = source
    raw.parse_ms = parse_ms
    raw.outcome = OUTCOME_ERROR
    raw.outcome_detail = _clip(_detail(error, resolved, _piece_note(raw, text)))


def safe_fill(raw: RawMessage, **kwargs) -> None:
    """`fill` ama asla patlamaz. İzleme yan etkidir: burada bir sorun çıkarsa
    log'lanır, mesaj işleme akışı etkilenmez."""
    try:
        fill(raw, **kwargs)
    except Exception:  # pragma: no cover - savunma amaçlı
        log.exception("izleme alanları yazılamadı (raw_message_id=%s)", getattr(raw, "id", None))


def _round_qty(qty: Decimal | None) -> Decimal | None:
    """detected_qty NUMERIC(14,2); kalem miktarı 3 haneli olabilir
    (transaction_lines.qty). İzleme için 2 haneye yuvarlanır — para değil,
    yalnızca panelde gösterilecek bilgi."""
    if qty is None:
        return None
    return qty.quantize(Decimal("0.01"))


def _piece_note(raw: RawMessage, text: str | None) -> str | None:
    """Çok istekli mesajda bu satırın hangi parçayı izlediğini işaretler."""
    if not text:
        return None
    full = payload_text(raw.payload)
    if full is None or full.strip() == text.strip():
        return None
    return f'parça: "{text[:120]}"'
