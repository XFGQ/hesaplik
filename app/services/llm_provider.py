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

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from app.services.llm_prompt import SYSTEM_PROMPT
from app.services.parser import ParsedIntent

logger = logging.getLogger(__name__)

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


def _report_intent_from_json(data: dict) -> ParsedIntent:
    """LLM'in "islem": "rapor" çıktısını rapor niyetine çevirir. tur
    belirsiz/tanınmayan bir değerse ya da tur "kisi" olup kişi adı boşsa,
    kod UYDURMAZ — report_menu döner, kullanıcıya hangi raporu istediği
    sorulur (bkz. CLAUDE.md > "Rapor komutları — gelişmiş anlama")."""
    tur = data.get("tur")
    kind = REPORT_TUR_TO_KIND.get(tur)
    if kind is None:
        return ParsedIntent(kind="report_menu")

    if kind == "report_person":
        person_name = _clean_str(data.get("kisi"))
        if person_name is None:
            return ParsedIntent(kind="report_menu")
        return ParsedIntent(kind="report_person", person_name=person_name)

    return ParsedIntent(kind=kind)


def parsed_intent_from_json(data: dict) -> ParsedIntent | None:
    """LLM'in ürettiği JSON sözlüğünü ParsedIntent'e çevirir. Şema dışı ya
    da anlamsız bir çıktı gelirse None döner (LLM çözemedi sayılır)."""
    if data.get("islem") == "rapor":
        return _report_intent_from_json(data)

    kind = data.get("kind")
    if kind not in VALID_KINDS:
        return None

    person_name = _clean_str(data.get("person_name"))
    if kind in ("debt", "payment", "balance_query") and person_name is None:
        # Kayıt niyeti kişisiz anlamsızdır — intent_resolver zaten kişisiz
        # ParsedIntent'i reddeder ama burada erken çıkmak niyeti açıkça
        # "çözülemedi" sayar (LLM belirsiz kaldıysa None dönmesi doğrudur).
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

        return parsed_intent_from_json(data)


def get_provider() -> LLMProvider | None:
    """Config'e (LLM_PROVIDER) göre aktif sağlayıcıyı döner. "none" ya da
    tanınmayan bir değer -> None (LLM'e hiç gidilmez)."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    if settings.llm_provider == "ollama":
        return OllamaProvider(settings.ollama_url, settings.llm_model, timeout=settings.llm_timeout)
    return None
