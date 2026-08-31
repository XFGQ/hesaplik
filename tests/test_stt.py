"""GroqSTTProvider ve get_stt_provider testleri. Gerçek Groq'a ASLA
bağlanılmaz — httpx.MockTransport ile ağ çağrısı taklit edilir (bkz.
tests/test_llm_provider.py'deki aynı desen)."""

import httpx

from app.config import settings
from app.services.stt import GroqSTTProvider, get_stt_provider


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_basarili_cevap_metni_doner():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openai/v1/audio/transcriptions"
        assert request.headers["Authorization"] == "Bearer gizli-anahtar"
        return httpx.Response(200, json={"text": "ahmet elli tl borç"})

    provider = GroqSTTProvider(
        "https://api.groq.com/openai/v1", "gizli-anahtar", "whisper-large-v3",
        client=_client_for(handler),
    )
    text = await provider.transcribe(b"sahte-ses-verisi")
    assert text == "ahmet elli tl borç"


async def test_bosluklu_metin_kirpilir():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "  merhaba dünya  "})

    provider = GroqSTTProvider("https://groq.test", "key", "model", client=_client_for(handler))
    assert await provider.transcribe(b"x") == "merhaba dünya"


async def test_bos_metin_none_doner():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "   "})

    provider = GroqSTTProvider("https://groq.test", "key", "model", client=_client_for(handler))
    assert await provider.transcribe(b"x") is None


async def test_beklenmeyen_govde_none_doner():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = GroqSTTProvider("https://groq.test", "key", "model", client=_client_for(handler))
    assert await provider.transcribe(b"x") is None


async def test_gecersiz_json_none_doner():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    provider = GroqSTTProvider("https://groq.test", "key", "model", client=_client_for(handler))
    assert await provider.transcribe(b"x") is None


async def test_429_rate_limit_none_doner_cokmez():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    provider = GroqSTTProvider("https://groq.test", "key", "model", client=_client_for(handler))
    assert await provider.transcribe(b"x") is None


async def test_sunucu_hatasi_none_doner_cokmez():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = GroqSTTProvider("https://groq.test", "key", "model", client=_client_for(handler))
    assert await provider.transcribe(b"x") is None


async def test_baglanti_hatasi_none_doner_cokmez():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bağlanamadı", request=request)

    provider = GroqSTTProvider("https://groq.test", "key", "model", client=_client_for(handler))
    assert await provider.transcribe(b"x") is None


# --------------------------------------------------------------- get_stt_provider


def test_api_key_bossa_none_doner(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert get_stt_provider() is None


def test_api_key_varsa_provider_kurulur(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gizli-anahtar")
    monkeypatch.setattr(settings, "groq_stt_model", "whisper-large-v3")
    provider = get_stt_provider()
    assert provider is not None
    assert provider.api_key == "gizli-anahtar"
    assert provider.model == "whisper-large-v3"
