"""LLM sağlayıcıları (Faz 4).

Kural parser (app/services/parser.py) çözemediği cümleler için fallback —
kural parser HİÇBİR ZAMAN kaldırılmaz, bu yalnızca ek bir kaynaktır.
LLM çıktısı asla doğrudan güvenilmez: intent_resolver'daki kişi eşleştirme
(pg_trgm + SIMILARITY_STRONG/GAP) ve catalog kuralları aynen uygulanır
(CLAUDE.md > "Faz 4 — LLM").

Provider soyutlaması: hangi sağlayıcı kullanılacağı config'ten
(LLM_PROVIDER) belirlenir, koddan değil. Ollama yoksa/erişilemezse
`parse()` None döner, sistem ÇÖKMEZ — kural parser + "elle gir" ile
çalışmaya devam eder.
"""

from __future__ import annotations

import difflib
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from app.services.catalog import normalize
from app.services.llm_prompt import SYSTEM_PROMPT
from app.services.name_utils import strip_turkish_suffix
from app.services.parser import ParsedIntent

logger = logging.getLogger(__name__)

# LLM'in döndürdüğü kişi adı, ham metindeki hiçbir kelimeye yeterince
# benzemiyorsa harf uydurmuş sayılır ve güvenilmez (CLAUDE.md > "KRİTİK —
# LLM isim bozuyor": "mehmetten" -> "mehtap" gibi).
#
# Saf difflib oranı kısa isimlerde yanlış pozitif (yani GEÇERLİ ismi
# reddetme) veriyordu: örn. "ali"/"aliden" 0.67, ama "eda"/"edadan" 0.67,
# "su"/"sudan" 0.57 — kısa kök + Türkçe ek eklendikçe oran hızla düşüyor,
# 0.7 eşiği bunları eler. Bu yüzden önce DETERMİNİSTİK bir kök eşleşmesi
# denenir (strip_turkish_suffix ile hem isim hem ham kelimenin eki
# soyulur, kökler birebir eşleşiyorsa kabul); yalnızca kök eşleşmezse
# difflib'e (daha düşük eşikle) düşülür. Kök soyucu tam bir morfolojik
# çözümleyici olmadığından (bkz. name_utils) ikinci katman hâlâ gerekli.
NAME_HALLUCINATION_THRESHOLD = 0.6

VALID_KINDS = {
    "debt",
    "payment",
    "balance_query",
    "list_all",
    "list_debtors",
    "list_creditors",
    "list_district",
}

# LLM rapor isteklerini ayrı bir alan çiftiyle ("islem"/"tur") döner (bkz.
# llm_prompt.py), "kind" şemasıyla karışmasın diye. tur -> ParsedIntent.kind
# eşlemesi burada yapılır.
REPORT_TUR_TO_KIND = {
    "genel": "report_general",
    "gunluk": "report_daily",
    "kisi": "report_person",
}


class LLMProvider(Protocol):
    async def parse(self, text: str) -> ParsedIntent | None: ...


def _clean_str(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _to_decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _verified_person_name(person_name: str | None, raw_text: str | None) -> str | None:
    """LLM'in döndürdüğü kişi adını ham metinle doğrular. raw_text
    verilmemişse (ör. eski/doğrudan birim testleri) doğrulama atlanır.
    Ham metindeki hiçbir kelimeye yeterince benzemiyorsa None döner — bu,
    LLM'in harf uydurduğu (mehmetten -> mehtap gibi) anlamına gelir ve
    güvenilmez; çağıran yer bunu "kişi yok" gibi ele alıp güvenli tarafta
    kalır (sor ya da anlaşılamadı de, asla yanlış kişiye yazma).

    Deterministiktir: aynı (person_name, raw_text) çifti her zaman aynı
    sonucu verir. Önce kök eşleşmesi denenir (ek farklarından etkilenmez),
    yalnızca o başarısız olursa difflib oranına bakılır."""
    if person_name is None or raw_text is None:
        return person_name

    raw_words = normalize(raw_text).split()
    if not raw_words:
        return person_name

    name_norm = normalize(person_name)
    name_root = strip_turkish_suffix(person_name)
    if any(word == name_norm or strip_turkish_suffix(word) == name_root for word in raw_words):
        return person_name

    best_ratio = max(
        difflib.SequenceMatcher(None, name_norm, w).ratio() for w in raw_words
    )
    if best_ratio < NAME_HALLUCINATION_THRESHOLD:
        logger.warning(
            "LLM kişi adı ham metinle örtüşmüyor, güvenilmiyor: %r (metin: %r)",
            person_name, raw_text,
        )
        return None
    return person_name


def _report_intent_from_json(data: dict, raw_text: str | None = None) -> ParsedIntent:
    """LLM'in "islem": "rapor" çıktısını rapor niyetine çevirir. tur
    belirsiz/tanınmayan bir değerse ya da tur "kisi" olup kişi adı boşsa,
    kod UYDURMAZ — report_menu döner, kullanıcıya hangi raporu istediği
    sorulur (bkz. CLAUDE.md > "Rapor komutları — gelişmiş anlama")."""
    tur = data.get("tur")
    kind = REPORT_TUR_TO_KIND.get(tur)
    if kind is None:
        return ParsedIntent(kind="report_menu")

    if kind == "report_person":
        person_name = _verified_person_name(_clean_str(data.get("kisi")), raw_text)
        if person_name is None:
            return ParsedIntent(kind="report_menu")
        return ParsedIntent(kind="report_person", person_name=person_name)

    return ParsedIntent(kind=kind)


def parsed_intent_from_json(data: dict, raw_text: str | None = None) -> ParsedIntent | None:
    """LLM'in ürettiği JSON sözlüğünü ParsedIntent'e çevirir. Şema dışı ya
    da anlamsız bir çıktı gelirse None döner (LLM çözemedi sayılır).

    raw_text verilirse (asıl kullanıcı mesajı), kişi adı buna karşı
    doğrulanır — LLM'in isim uydurmasını (CLAUDE.md > "KRİTİK — LLM isim
    bozuyor") engeller. Çekim eki temizleme LLM'e bırakılmaz, burada da
    yapılmaz: ek temizleme intent_resolver'da (name_utils) olur, burada
    yalnızca "bu isim ham metinden mi geliyor" kontrol edilir."""
    if data.get("islem") == "rapor":
        return _report_intent_from_json(data, raw_text)

    kind = data.get("kind")
    if kind not in VALID_KINDS:
        return None

    person_name = _verified_person_name(_clean_str(data.get("person_name")), raw_text)
    if kind in ("debt", "payment", "balance_query") and person_name is None:
        # Kayıt niyeti kişisiz anlamsızdır — intent_resolver zaten kişisiz
        # ParsedIntent'i reddeder ama burada erken çıkmak niyeti açıkça
        # "çözülemedi" sayar (LLM belirsiz kaldıysa ya da isim ham metinle
        # örtüşmüyorsa None dönmesi doğrudur).
        return None

    return ParsedIntent(
        kind=kind,
        person_name=person_name,
        qty=_to_decimal(data.get("qty")),
        unit=_clean_str(data.get("unit")),
        product=_clean_str(data.get("product")),
        amount=_to_decimal(data.get("amount")),
        district=_clean_str(data.get("district")),
    )


class OllamaProvider:
    """Ollama /api/chat üzerinden çalışır. format=json ile JSON zorlanır.
    Bağlantı hatası/timeout/geçersiz yanıt -> None döner, sistemi
    ÇÖKERTMEZ.

    `client` parametresi yalnızca testler içindir (httpx.MockTransport ile
    gerçek ağa çıkmadan mock'lamak için); normal kullanımda boş bırakılır,
    her çağrıda kısa ömürlü bir AsyncClient açılır.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = client

    async def _request(self, payload: dict) -> dict | None:
        url = f"{self.base_url}/api/chat"
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Ollama'ya erişilemedi ya da geçersiz yanıt: %s", e)
            return None

    async def parse(self, text: str) -> ParsedIntent | None:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "format": "json",
            "stream": False,
        }
        body = await self._request(payload)
        if body is None:
            return None

        content = (body.get("message") or {}).get("content")
        if not content:
            return None

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Ollama geçersiz JSON döndürdü: %r", content)
            return None
        if not isinstance(data, dict):
            return None

        return parsed_intent_from_json(data, text)


def get_provider() -> LLMProvider | None:
    """Config'e (LLM_PROVIDER) göre aktif sağlayıcıyı döner. "none" ya da
    tanınmayan bir değer -> None (LLM'e hiç gidilmez)."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    if settings.llm_provider == "ollama":
        return OllamaProvider(settings.ollama_url, settings.llm_model, timeout=settings.llm_timeout)
    return None
