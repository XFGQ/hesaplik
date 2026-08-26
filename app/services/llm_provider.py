"""LLM sağlayıcıları (Faz 4, Faz 4c — dinamik geçiş).

Kural parser (app/services/parser.py) çözemediği cümleler için fallback —
kural parser HİÇBİR ZAMAN kaldırılmaz, bu yalnızca ek bir kaynaktır.
LLM çıktısı asla doğrudan güvenilmez: intent_resolver'daki kişi eşleştirme
(pg_trgm + SIMILARITY_STRONG/GAP) ve catalog kuralları aynen uygulanır
(CLAUDE.md > "Faz 4 — LLM").

İki eş değerde sağlayıcı var: VLLMProvider (Bosna, 2080 Super, BİRİNCİL —
hızlı) ve OllamaProvider (İzmir, YEDEK — yavaş ama her zaman orada). İkisi
de aynı SYSTEM_PROMPT'u kullanır, aynı parsed_intent_from_json doğrulama/
güvenlik katmanından geçer; tek fark HTTP istek/yanıt şekli.

Hangi sağlayıcının aktif olacağı artık sabit config'ten değil, DB'deki
settings.llm_primary'den (runtime, admin panelden /admin değiştirilebilir)
belirlenir — get_active_provider(session) bkz. Kaynaklardan hiçbiri
erişilemezse `parse()` None döner, sistem ÇÖKMEZ — kural parser + "elle
gir" ile çalışmaya devam eder.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting
from app.services.catalog import normalize
from app.services.llm_prompt import RESPONSE_JSON_SCHEMA, SYSTEM_PROMPT
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


def _info_intent_from_json(data: dict, kind: str, raw_text: str | None) -> ParsedIntent | None:
    """LLM'in "islem": "bilgi_menu"/"iletisim" çıktısını çevirir (bkz.
    llm_prompt.py, CLAUDE.md > "DÜZELTME — 'bilgi ver' belirsiz, SOR").
    Rapor ailesinden farklı olarak burada kişisiz bir "menü" fallback'i
    anlamsızdır (ikisi de zaten bir kişiyi hedefler) — kişi adı
    doğrulanamazsa None dönülür, çağıran yer "anlaşılamadı" sayar."""
    person_name = _verified_person_name(_clean_str(data.get("kisi")), raw_text)
    if person_name is None:
        return None
    return ParsedIntent(kind=kind, person_name=person_name)


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


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(content: str) -> dict | None:
    """LLM çıktısı bazen SADECE JSON olmuyor — markdown kod bloğuna sarılmış
    ya da öncesinde/sonrasında sohbet metniyle geliyor (gözlem 2026-08: vLLM
    200 OK dönüyor ama SYSTEM_PROMPT'un "SADECE JSON döndür" talimatına
    uymayıp serbest metinle cevap verebiliyor — response_format=json_object
    her zaman zorlamıyor, bkz. VLLMProvider'daki guided_json). Önce içeriğin
    TAMAMINI doğrudan JSON olarak dener, olmazsa metindeki İLK süslü parantez
    bloğunu ayıklayıp tekrar dener. Hiçbir yerde geçerli bir JSON nesnesi
    yoksa (gerçek sohbet metni, hiç JSON içermiyor) None döner — bu durumda
    ayıklanacak bir şey yoktur, model gerçekten anlamamıştır."""
    for candidate in (content, _first_json_block(content)):
        if candidate is None:
            continue
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _first_json_block(content: str) -> str | None:
    match = _JSON_BLOCK_RE.search(content)
    return match.group(0) if match else None


def parsed_intent_from_json(data: dict, raw_text: str | None = None) -> ParsedIntent | None:
    """LLM'in ürettiği JSON sözlüğünü ParsedIntent'e çevirir. Şema dışı ya
    da anlamsız bir çıktı gelirse None döner (LLM çözemedi sayılır).

    raw_text verilirse (asıl kullanıcı mesajı), kişi adı buna karşı
    doğrulanır — LLM'in isim uydurmasını (CLAUDE.md > "KRİTİK — LLM isim
    bozuyor") engeller. Çekim eki temizleme LLM'e bırakılmaz, burada da
    yapılmaz: ek temizleme intent_resolver'da (name_utils) olur, burada
    yalnızca "bu isim ham metinden mi geliyor" kontrol edilir."""
    islem = data.get("islem")
    if islem == "rapor":
        return _report_intent_from_json(data, raw_text)
    if islem == "bilgi_menu":
        return _info_intent_from_json(data, "info_menu", raw_text)
    if islem == "iletisim":
        return _info_intent_from_json(data, "person_contact", raw_text)

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

        data = _extract_json_object(content)
        if data is None:
            logger.warning("Ollama geçersiz JSON döndürdü: %r", content)
            return None

        return parsed_intent_from_json(data, text)


class VLLMProvider:
    """vLLM'in OpenAI-uyumlu /v1/chat/completions uç noktası üzerinden
    çalışır (Bosna, 2080 Super, WireGuard tüneli). Ollama'dan tek farkı
    istek/yanıt şekli — aynı SYSTEM_PROMPT, aynı parsed_intent_from_json
    doğrulama/güvenlik katmanı (isim halüsinasyon kontrolü, VALID_KINDS,
    Decimal çevirimi) aynen uygulanır. Bağlantı hatası/timeout/geçersiz
    yanıt -> None döner, sistemi ÇÖKERTMEZ.

    `client` parametresi yalnızca testler içindir (httpx.MockTransport ile
    gerçek ağa çıkmadan mock'lamak için); normal kullanımda boş bırakılır,
    her çağrıda kısa ömürlü bir AsyncClient açılır.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = client

    async def _request(self, payload: dict) -> dict | None:
        url = f"{self.base_url}/v1/chat/completions"
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
            logger.warning("vLLM'e erişilemedi ya da geçersiz yanıt: %s", e)
            return None

    async def parse(self, text: str) -> ParsedIntent | None:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "response_format": {"type": "json_object"},
            # vLLM'e özgü grammar-constrained decoding ipucu (bkz.
            # llm_prompt.RESPONSE_JSON_SCHEMA docstring'i): response_format
            # tek başına her zaman yeterli olmuyor, model bazen serbest
            # sohbet metniyle cevap veriyor (200 OK, JSON değil). Sunucu bu
            # alanı tanımıyorsa (eski vLLM sürümü) sessizce yok sayılır.
            "guided_json": RESPONSE_JSON_SCHEMA,
            "temperature": 0,
        }
        body = await self._request(payload)
        if body is None:
            return None

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        if not content:
            return None

        data = _extract_json_object(content)
        if data is None:
            logger.warning("vLLM geçersiz JSON döndürdü: %r", content)
            return None

        return parsed_intent_from_json(data, text)


# --------------------------------------------------------------- sağlık kontrolü
#
# "auto" modda hangi kaynağın kullanılacağına ve admin panelin (/admin)
# durum ekranına karar vermek için. HIZLI olmalı (llm_health_timeout, ~3
# sn) — her parse() çağrısında tam timeout beklenmesin diye sonuç ayrıca
# _cached_health ile 10 sn önbelleklenir.


async def _probe(url: str, timeout: float) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


async def vllm_healthy() -> bool:
    """vLLM'in OpenAI-uyumlu sunucusundaki standart /health uç noktasını
    yoklar. vllm_url boşsa (yapılandırılmamış) hiç denenmez."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    if not settings.vllm_url:
        return False
    return await _probe(f"{settings.vllm_url.rstrip('/')}/health", settings.llm_health_timeout)


async def ollama_healthy() -> bool:
    """Ollama'nın yerel model listesini (/api/tags) yoklar — model
    yüklenmemiş olsa bile sunucu ayaktaysa hızlı döner."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    return await _probe(f"{settings.ollama_url.rstrip('/')}/api/tags", settings.llm_health_timeout)


_HEALTH_CACHE_TTL = 10.0
_health_cache: dict[str, tuple[float, bool]] = {}


async def _cached_health(source: str) -> bool:
    """vllm_healthy/ollama_healthy sonucunu _HEALTH_CACHE_TTL saniye
    önbellekler ki her mesajda health check ağa çıkmasın. `source`:
    "vllm" | "ollama"."""
    now = time.monotonic()
    cached = _health_cache.get(source)
    if cached is not None and (now - cached[0]) < _HEALTH_CACHE_TTL:
        return cached[1]
    check = vllm_healthy if source == "vllm" else ollama_healthy
    ok = await check()
    _health_cache[source] = (now, ok)
    return ok


def reset_health_cache() -> None:
    """Test yardımcı fonksiyonu: sağlık önbelleğini temizler (testler
    arasında sızıntı olmasın diye conftest'te otomatik çağrılır)."""
    _health_cache.clear()


async def vllm_reachable_cached() -> bool:
    """vLLM aç/kapat panelinin (bkz. app/services/vllm_control.py) "gerçek"
    durumu için: get_status ile aynı 10 sn önbellekli kontrol, dışa açık
    (routes.py _cached_health'e doğrudan erişmesin diye)."""
    return await _cached_health("vllm")


# --------------------------------------------------------------- tercih (DB)
#
# settings tablosunda "llm_primary" anahtarı — runtime'da admin panelden
# (/admin) değiştirilir, .env değil (CLAUDE.md > "Dinamik LLM geçişi").

LLM_PRIMARY_KEY = "llm_primary"
LLM_PRIMARY_VALUES = ("auto", "vllm", "ollama", "none")
LLM_PRIMARY_DEFAULT = "auto"


async def get_llm_primary(session: AsyncSession) -> str:
    """DB'deki tercihi okur. Hiç ayarlanmamışsa ya da tanınmayan bir
    değerse "auto" sayılır — bozuk/eski bir değer sistemi LLM'siz
    bırakmaz, güvenli varsayılana düşer."""
    row = await session.get(Setting, LLM_PRIMARY_KEY)
    value = row.value if row is not None else None
    return value if value in LLM_PRIMARY_VALUES else LLM_PRIMARY_DEFAULT


async def set_llm_primary(session: AsyncSession, value: str) -> None:
    if value not in LLM_PRIMARY_VALUES:
        raise ValueError(f"Geçersiz llm_primary değeri: {value!r}")
    setting = await session.get(Setting, LLM_PRIMARY_KEY)
    if setting is None:
        session.add(Setting(key=LLM_PRIMARY_KEY, value=value))
    else:
        setting.value = value
    await session.flush()


def select_source(primary: str, vllm_ok: bool, ollama_ok: bool) -> str:
    """Tercihe ve sağlık durumuna göre hangi kaynağın kullanılacağını
    belirleyen SAF fonksiyon (ağa çıkmaz) — hem get_active_provider hem
    admin durum uç noktası (/api/admin/llm) bunu kullanır ki seçim mantığı
    tek yerde yaşasın, ikisi asla birbirinden sapmasın.

    - "none": her zaman kapalı.
    - "ollama": her zaman Ollama — vLLM'e hiç dokunulmaz (kullanıcı GPU'yu
      kendi kullanmak istediğinde "tek tuş kapat" senaryosu).
    - "vllm": zorla vLLM; erişilemezse NONE'a düşer, Ollama'ya değil
      (kullanıcı özellikle vLLM istemiştir).
    - "auto" (ya da tanınmayan değer): vLLM sağlıklıysa vLLM, değilse
      Ollama, o da değilse none.
    """
    if primary == "none":
        return "none"
    if primary == "ollama":
        return "ollama"
    if primary == "vllm":
        return "vllm" if vllm_ok else "none"
    if vllm_ok:
        return "vllm"
    if ollama_ok:
        return "ollama"
    return "none"


async def get_active_provider(session: AsyncSession) -> LLMProvider | None:
    """settings.llm_primary'ye ve (gerekirse) canlı sağlık kontrolüne göre
    aktif sağlayıcıyı döner. "none" ya da hiçbir kaynak erişilemiyorsa
    None (LLM'e hiç gidilmez, kural parser + "elle gir" ile devam edilir).

    "ollama" tercihinde vLLM'e HİÇ dokunulmaz (health check bile atılmaz)
    — GPU'yu kullanıcı kendi kullanmak istediğinde bu davranış kasıtlıdır.
    """
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    primary = await get_llm_primary(session)

    if primary == "ollama":
        return OllamaProvider(settings.ollama_url, settings.llm_model, timeout=settings.llm_timeout)

    # "ollama" dışındaki tüm dallarda vLLM sağlığı gerekiyor; Ollama sağlığı
    # yalnızca "auto"da (ve vLLM sağlıksızsa) — gereksiz ağ çağrısından
    # kaçınmak için kısa devre yapılır (select_source'a zaten hesaplanmış
    # bayraklar geçilir).
    vllm_ok = await _cached_health("vllm") if primary != "none" else False
    ollama_ok = await _cached_health("ollama") if (primary == "auto" and not vllm_ok) else False

    source = select_source(primary, vllm_ok, ollama_ok)
    if source == "vllm":
        return VLLMProvider(settings.vllm_url, settings.vllm_model, timeout=settings.vllm_timeout)
    if source == "ollama":
        return OllamaProvider(settings.ollama_url, settings.llm_model, timeout=settings.llm_timeout)

    if primary == "vllm":
        logger.warning("llm_primary=vllm ama vLLM erişilemiyor, LLM devre dışı (Ollama'ya düşülmez)")
    return None


# --------------------------------------------------------------- durum (admin panel)


@dataclass(slots=True)
class SourceStatus:
    ok: bool
    url: str
    model: str


@dataclass(slots=True)
class LLMStatus:
    primary: str
    active: str
    vllm: SourceStatus
    ollama: SourceStatus


async def get_status(session: AsyncSession) -> LLMStatus:
    """Admin panelin (/api/admin/llm) gösterdiği anlık durum. select_source
    ile aynı saf karar mantığını kullanır, ama get_active_provider'daki kısa
    devreden (ör. "ollama" tercihinde vLLM'e hiç dokunmama) FARKLI olarak
    HER İKİ kaynağı da her zaman yoklar — admin ikisinin de gerçek durumunu
    görmek ister, yalnızca aktif olanınkini değil."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    primary = await get_llm_primary(session)
    vllm_ok = await _cached_health("vllm")
    ollama_ok = await _cached_health("ollama")
    active = select_source(primary, vllm_ok, ollama_ok)

    return LLMStatus(
        primary=primary,
        active=active,
        vllm=SourceStatus(ok=vllm_ok, url=settings.vllm_url, model=settings.vllm_model),
        ollama=SourceStatus(ok=ollama_ok, url=settings.ollama_url, model=settings.llm_model),
    )
