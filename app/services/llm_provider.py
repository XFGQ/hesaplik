"""LLM sağlayıcıları (Faz 4, Faz 4c — dinamik geçiş).

Kural parser (app/services/parser.py) çözemediği cümleler için fallback —
kural parser HİÇBİR ZAMAN kaldırılmaz, bu yalnızca ek bir kaynaktır.
LLM çıktısı asla doğrudan güvenilmez: intent_resolver'daki kişi eşleştirme
(pg_trgm + SIMILARITY_STRONG/GAP) ve catalog kuralları aynen uygulanır
(CLAUDE.md > "Faz 4 — LLM").

Dört katmanlı sağlayıcı var, öncelik sırasıyla: NVIDIAProvider (bulut NIM,
EN ZEKİ — ama ağ bağımlı + rate limit'li), VLLMProvider (Bosna, 2080
Super, hızlı), OllamaProvider (İzmir, YEDEK — yavaş ama her zaman orada).
Hepsi aynı SYSTEM_PROMPT'u kullanır, aynı parsed_intent_from_json
doğrulama/güvenlik katmanından geçer; tek fark HTTP istek/yanıt şekli ve
(NVIDIA'da) kimlik doğrulama.

Hangi sağlayıcının aktif olacağı artık sabit config'ten değil, DB'deki
settings.llm_primary'den (runtime, admin panelden /admin değiştirilebilir)
belirlenir — get_active_provider(session) bkz. "auto" modda katman
sırasıyla düşülür: NVIDIA sağlıksız/limit dolu -> vLLM, o da yoksa
Ollama, o da yoksa none. Kaynaklardan hiçbiri erişilemezse `parse()` None
döner, sistem ÇÖKMEZ — kural parser + "elle gir" ile çalışmaya devam eder.
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
    "create_person",
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

    async def chat_json(
        self, system_prompt: str, user_text: str, max_tokens: int | None = None
    ) -> dict | None: ...


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
    if kind in ("debt", "payment", "balance_query", "create_person") and person_name is None:
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


# --------------------------------------------------------------- isim eşleştirme (öngörücü teyit)
#
# CLAUDE.md > "İsim eşleştirme + öngörücü teyit": pg_trgm hiçbir aday
# bulamadığında (ör. "doman" -> "Duman" benzerliği 0.33, SIMILARITY_CANDIDATE
# 0.35'in altında kalıyor) son çare LLM'e danışılır — kayıtlı kişi listesi
# verilir, "kullanıcı bunu yazdı, hangisini kastetmiş olabilir?" diye
# sorulur. Çağıran (intent_resolver.find_person_match) yalnızca pg_trgm SIFIR
# aday bulduğunda buraya düşer; normal (net ya da adaylı) eşleşmede bu hiç
# çalışmaz, hızlı yol etkilenmez.
#
# NVIDIA'nın gpt-oss-20b modeli reasoning yapabildiği için max_tokens düşük
# kalırsa content null dönebiliyor (bkz. NVIDIAProvider.chat_json) — bu
# yüzden burada reasoning+content ikisine yetecek kadar geniş bir bütçe
# kullanılır.
NAME_MATCH_MAX_TOKENS = 400

# Tek seferde LLM'e gönderilecek kayıtlı kişi sayısının üst sınırı: prompt
# büyüklüğünü (ve dolayısıyla gecikmeyi/rate limit tüketimini) sınırlar. Çok
# büyük bir müşteri tabanında bile bu, "isim tam eşleşmedi" durumunun (nadir)
# son çare adımıdır — üst sınıra takılan işletmeler için eksiksizlik yerine
# hız/maliyet tercih edilir.
NAME_MATCH_CANDIDATE_LIMIT = 300


def _build_name_match_prompt(candidate_names: list[str]) -> str:
    joined = "\n".join(f"- {name}" for name in candidate_names)
    return (
        "Bir cari hesap defterinde kayıtlı kişi listesi aşağıdadır. Kullanıcı "
        "bir isim yazdı ama otomatik (bulanık) eşleştirme hiçbir aday "
        "bulamadı — yazım hatası ya da eksik/fazla harf olabilir "
        "(\"doman\" -> \"Duman\" gibi). Kullanıcının YAZDIĞI isimle listedeki "
        "hangi kişiyi kastetmiş OLABİLECEĞİNİ bul.\n\n"
        f"Kayıtlı kişiler:\n{joined}\n\n"
        "SADECE şu JSON'u döndür, başka hiçbir metin/açıklama yazma:\n"
        '{"eslesen_kisi": string|null}\n\n'
        "Kurallar:\n"
        "- eslesen_kisi, yukarıdaki listedeki isimlerden BİRİYLE HARFİ "
        "HARFİNE aynı olmalı. Listede olmayan bir isim UYDURMA.\n"
        "- Makul, açık bir eşleşme yoksa ya da emin değilsen null döndür — "
        "tahmin ETME, yanlış kişiyi göstermek yanlış kişiye para yazılmasına "
        "yol açabilir."
    )


async def suggest_person_match(
    provider: LLMProvider, name_raw: str, candidate_names: list[str]
) -> str | None:
    """pg_trgm sıfır aday bulduğunda son çare: LLM'e kayıtlı isim listesini
    verip kullanıcının hangisini kastetmiş olabileceğini sorar.

    GÜVENLİK: LLM'in döndürdüğü isim candidate_names listesindeki (normalize
    edilmiş) bir isimle BİREBİR eşleşmiyorsa asla güvenilmez ve None döner —
    LLM listede olmayan bir isim uyduramaz (CLAUDE.md > "KRİTİK — LLM isim
    bozuyor" ile aynı ilke: LLM öneri sunar, karar/doğrulama kod tarafında).
    """
    if not candidate_names:
        return None

    chat_json = getattr(provider, "chat_json", None)
    if chat_json is None:
        return None

    data = await chat_json(
        _build_name_match_prompt(candidate_names), name_raw, max_tokens=NAME_MATCH_MAX_TOKENS
    )
    if not data:
        return None

    suggested = _clean_str(data.get("eslesen_kisi"))
    if suggested is None:
        return None

    suggested_norm = normalize(suggested)
    for candidate in candidate_names:
        if normalize(candidate) == suggested_norm:
            return candidate

    logger.warning(
        "LLM isim eşleştirme listede olmayan bir isim döndürdü, güvenilmiyor: %r",
        suggested,
    )
    return None


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

    async def chat_json(
        self, system_prompt: str, user_text: str, max_tokens: int | None = None
    ) -> dict | None:
        """parse()'tan bağımsız, serbest sistem prompt'uyla tek seferlik bir
        JSON isteği (bkz. CLAUDE.md > isim eşleştirme + öngörücü teyit,
        llm_provider.suggest_person_match). Ollama'da reasoning/content
        ayrımı yok, max_tokens burada yalnızca arayüz tutarlılığı için var."""
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "format": "json",
            "stream": False,
        }
        if max_tokens is not None:
            payload["options"] = {"num_predict": max_tokens}
        body = await self._request(payload)
        if body is None:
            return None

        content = (body.get("message") or {}).get("content")
        if not content:
            return None
        return _extract_json_object(content)


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

    async def chat_json(
        self, system_prompt: str, user_text: str, max_tokens: int | None = None
    ) -> dict | None:
        """parse()'tan bağımsız, serbest sistem prompt'uyla tek seferlik bir
        JSON isteği (bkz. llm_provider.suggest_person_match). guided_json
        BİLEREK eklenmez — o intent şemasına özgü (RESPONSE_JSON_SCHEMA),
        bu genel amaçlı metodun çıktı şekli çağırana göre değişir."""
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        body = await self._request(payload)
        if body is None:
            return None

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        if not content:
            return None
        return _extract_json_object(content)


# --------------------------------------------------------------- NVIDIA rate limit
#
# NVIDIA NIM 40 istek/dk ile sınırlı. NVIDIAProvider.parse() 429 aldığında
# bir süre "rate limited" işaretlenir; nvidia_healthy() bu süre boyunca
# HİÇ ağa çıkmadan False döner — hem limiti health check'in kendisi
# tüketmesin diye hem de auto modun aynı dakika içinde tekrar tekrar 429
# yiyerek zaman kaybetmemesi için. Modül seviyesinde, süreç ömrü boyunca
# tutulur (test_llm_provider.py'de reset_health_cache ile birlikte
# temizlenir).

_NVIDIA_RATE_LIMIT_COOLDOWN = 60.0
_nvidia_rate_limited_until = 0.0


def _mark_nvidia_rate_limited() -> None:
    global _nvidia_rate_limited_until
    _nvidia_rate_limited_until = time.monotonic() + _NVIDIA_RATE_LIMIT_COOLDOWN


def _nvidia_rate_limited() -> bool:
    return time.monotonic() < _nvidia_rate_limited_until


class NVIDIAProvider:
    """NVIDIA NIM (bulut, integrate.api.nvidia.com) üzerinden çalışır —
    OpenAI-uyumlu /chat/completions, Authorization: Bearer {api_key}.
    EN ÖNCELİKLİ katman (Faz 4c): bulutta en güçlü model burada çalışır,
    ama tek başına güvenilir değil (ağ bağımlılığı + 40 istek/dk rate
    limit) — bu yüzden erişilemezse ya da limit dolarsa (429) sessizce bir
    sonraki katmana (vLLM) düşülür (bkz. select_source, nvidia_healthy).

    VLLMProvider/OllamaProvider ile AYNI SYSTEM_PROMPT, AYNI
    parsed_intent_from_json doğrulama/güvenlik katmanı (isim halüsinasyon
    kontrolü, VALID_KINDS, Decimal çevirimi) aynen uygulanır — tek fark
    HTTP istek/yanıt şekli ve kimlik doğrulama. api_key hiçbir log
    satırına yazılmaz.

    `client` parametresi yalnızca testler içindir (httpx.MockTransport ile
    gerçek ağa çıkmadan mock'lamak için); normal kullanımda boş bırakılır,
    her çağrıda kısa ömürlü bir AsyncClient açılır.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client

    async def _request(self, payload: dict) -> dict | None:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            logger.warning("NVIDIA NIM'e erişilemedi: %s", e)
            return None

        if resp.status_code == 429:
            logger.warning("NVIDIA NIM rate limit doldu (429), bir sonraki katmana düşülüyor")
            _mark_nvidia_rate_limited()
            return None

        try:
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("NVIDIA NIM geçersiz yanıt döndürdü: %s", e)
            return None

    async def parse(self, text: str) -> ParsedIntent | None:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "response_format": {"type": "json_object"},
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
            logger.warning("NVIDIA NIM geçersiz JSON döndürdü: %r", content)
            return None

        return parsed_intent_from_json(data, text)

    async def chat_json(
        self, system_prompt: str, user_text: str, max_tokens: int | None = None
    ) -> dict | None:
        """parse()'tan bağımsız, serbest sistem prompt'uyla tek seferlik bir
        JSON isteği (bkz. llm_provider.suggest_person_match). NVIDIA'nın
        gpt-oss-20b modeli reasoning yapabiliyor: max_tokens düşük kalırsa
        tüm bütçe reasoning'e gidip "content" null dönebiliyor. Çağıran
        (suggest_person_match) bu yüzden max_tokens'ı reasoning+content
        ikisine yetecek kadar geniş verir; content yine de boş/null gelirse
        (ör. model çok uzun reasoning yaptıysa) REASONING ALANINA HİÇ
        BAKILMADAN None dönülür — güvenli taraf, isim uydurmaktansa "bulamadım"
        sayılır."""
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        body = await self._request(payload)
        if body is None:
            return None

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        if not content:
            return None
        return _extract_json_object(content)


# --------------------------------------------------------------- sağlık kontrolü
#
# "auto" modda hangi kaynağın kullanılacağına ve admin panelin (/admin)
# durum ekranına karar vermek için. HIZLI olmalı (llm_health_timeout, ~3
# sn) — her parse() çağrısında tam timeout beklenmesin diye sonuç ayrıca
# _cached_health ile 10 sn önbelleklenir.


async def _probe(url: str, timeout: float, headers: dict[str, str] | None = None) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
        # 429 (rate limit) "sunucu ayakta ama şu an kullanılamaz" demektir,
        # < 500 olsa da sağlıklı sayılmaz (NVIDIA'nın 40 istek/dk limiti).
        return resp.status_code < 500 and resp.status_code != 429
    except httpx.HTTPError:
        return False


async def nvidia_healthy() -> bool:
    """NVIDIA NIM sağlığı. api_key yapılandırılmamışsa (varsayılan) hiç
    ağa çıkmadan False döner. Yakın zamanda 429 yediyse (_nvidia_rate_
    limited) de ağa çıkmadan False döner — health check'in kendisi rate
    limit bütçesini tüketmesin diye (CLAUDE.md: sağlık kontrolü ucuz ve
    hızlı olmalı). Aksi halde hafif bir /models isteğiyle yoklanır."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    if not settings.nvidia_api_key:
        return False
    if _nvidia_rate_limited():
        return False
    headers = {"Authorization": f"Bearer {settings.nvidia_api_key}"}
    return await _probe(
        f"{settings.nvidia_url.rstrip('/')}/models", settings.llm_health_timeout, headers=headers
    )


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
    """nvidia_healthy/vllm_healthy/ollama_healthy sonucunu _HEALTH_CACHE_TTL
    saniye önbellekler ki her mesajda health check ağa çıkmasın. `source`:
    "nvidia" | "vllm" | "ollama". Modül seviyesindeki fonksiyon adları
    ÇAĞRI ANINDA (globals() ile) okunur, dict'e önceden bağlanmaz — testler
    monkeypatch.setattr(llm_provider, "vllm_healthy", ...) ile bu isimleri
    değiştirir, önceden bağlanmış bir referans bu değişikliği görmezdi."""
    now = time.monotonic()
    cached = _health_cache.get(source)
    if cached is not None and (now - cached[0]) < _HEALTH_CACHE_TTL:
        return cached[1]
    check = globals()[f"{source}_healthy"]
    ok = await check()
    _health_cache[source] = (now, ok)
    return ok


def reset_health_cache() -> None:
    """Test yardımcı fonksiyonu: sağlık önbelleğini VE NVIDIA rate limit
    cooldown'unu temizler (testler arasında sızıntı olmasın diye
    conftest'te otomatik çağrılır)."""
    global _nvidia_rate_limited_until
    _health_cache.clear()
    _nvidia_rate_limited_until = 0.0


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
LLM_PRIMARY_VALUES = ("auto", "nvidia", "vllm", "ollama", "none")
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


def select_source(primary: str, vllm_ok: bool, ollama_ok: bool, nvidia_ok: bool = False) -> str:
    """Tercihe ve sağlık durumuna göre hangi kaynağın kullanılacağını
    belirleyen SAF fonksiyon (ağa çıkmaz) — hem get_active_provider hem
    admin durum uç noktası (/api/admin/llm) bunu kullanır ki seçim mantığı
    tek yerde yaşasın, ikisi asla birbirinden sapmasın.

    - "none": her zaman kapalı.
    - "ollama": her zaman Ollama — vLLM'e ve NVIDIA'ya hiç dokunulmaz
      (kullanıcı GPU'yu kendi kullanmak istediğinde "tek tuş kapat"
      senaryosu).
    - "vllm": zorla vLLM; erişilemezse NONE'a düşer, Ollama'ya değil
      (kullanıcı özellikle vLLM istemiştir).
    - "nvidia": zorla NVIDIA; erişilemezse/limit doluysa NONE'a düşer,
      vLLM'e değil (kullanıcı özellikle NVIDIA istemiştir — aynı "zorla"
      simetrisi vllm ile aynı).
    - "auto" (ya da tanınmayan değer): öncelik NVIDIA > vLLM > Ollama >
      none — NVIDIA sağlıklıysa NVIDIA, değilse vLLM, o da değilse
      Ollama, o da değilse none.
    """
    if primary == "none":
        return "none"
    if primary == "ollama":
        return "ollama"
    if primary == "vllm":
        return "vllm" if vllm_ok else "none"
    if primary == "nvidia":
        return "nvidia" if nvidia_ok else "none"
    if nvidia_ok:
        return "nvidia"
    if vllm_ok:
        return "vllm"
    if ollama_ok:
        return "ollama"
    return "none"


async def get_active_provider(session: AsyncSession) -> LLMProvider | None:
    """settings.llm_primary'ye ve (gerekirse) canlı sağlık kontrolüne göre
    aktif sağlayıcıyı döner. "none" ya da hiçbir kaynak erişilemiyorsa
    None (LLM'e hiç gidilmez, kural parser + "elle gir" ile devam edilir).

    "ollama" tercihinde vLLM'e ve NVIDIA'ya HİÇ dokunulmaz (health check
    bile atılmaz) — GPU'yu kullanıcı kendi kullanmak istediğinde bu
    davranış kasıtlıdır. Diğer dallarda katmanlar sırayla (NVIDIA -> vLLM
    -> Ollama) ve yalnızca gerektiğinde yoklanır — bir üst katman
    sağlıklıysa alttakine hiç ağ çağrısı atılmaz."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    primary = await get_llm_primary(session)

    if primary == "ollama":
        return OllamaProvider(settings.ollama_url, settings.llm_model, timeout=settings.llm_timeout)

    nvidia_ok = await _cached_health("nvidia") if primary in ("auto", "nvidia") else False
    vllm_ok = (
        await _cached_health("vllm")
        if primary == "vllm" or (primary == "auto" and not nvidia_ok)
        else False
    )
    ollama_ok = (
        await _cached_health("ollama")
        if primary == "auto" and not nvidia_ok and not vllm_ok
        else False
    )

    source = select_source(primary, vllm_ok, ollama_ok, nvidia_ok)
    if source == "nvidia":
        return NVIDIAProvider(
            settings.nvidia_url, settings.nvidia_api_key, settings.nvidia_model,
            timeout=settings.nvidia_timeout,
        )
    if source == "vllm":
        return VLLMProvider(settings.vllm_url, settings.vllm_model, timeout=settings.vllm_timeout)
    if source == "ollama":
        return OllamaProvider(settings.ollama_url, settings.llm_model, timeout=settings.llm_timeout)

    if primary == "nvidia":
        logger.warning("llm_primary=nvidia ama NVIDIA erişilemiyor, LLM devre dışı (vLLM'e düşülmez)")
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
    nvidia: SourceStatus
    vllm: SourceStatus
    ollama: SourceStatus


async def get_status(session: AsyncSession) -> LLMStatus:
    """Admin panelin (/api/admin/llm) gösterdiği anlık durum. select_source
    ile aynı saf karar mantığını kullanır, ama get_active_provider'daki kısa
    devreden (ör. "ollama" tercihinde vLLM'e/NVIDIA'ya hiç dokunmama)
    FARKLI olarak HER ÜÇ kaynağı da her zaman yoklar — admin üçünün de
    gerçek durumunu görmek ister, yalnızca aktif olanınkini değil."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    primary = await get_llm_primary(session)
    nvidia_ok = await _cached_health("nvidia")
    vllm_ok = await _cached_health("vllm")
    ollama_ok = await _cached_health("ollama")
    active = select_source(primary, vllm_ok, ollama_ok, nvidia_ok)

    return LLMStatus(
        primary=primary,
        active=active,
        nvidia=SourceStatus(ok=nvidia_ok, url=settings.nvidia_url, model=settings.nvidia_model),
        vllm=SourceStatus(ok=vllm_ok, url=settings.vllm_url, model=settings.vllm_model),
        ollama=SourceStatus(ok=ollama_ok, url=settings.ollama_url, model=settings.llm_model),
    )
