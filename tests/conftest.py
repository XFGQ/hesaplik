import os

import asyncpg
import bcrypt
import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.services import auth, llm_provider
from app.services.parser import ParsedIntent

# .env'deki TEST_DSN gibi değişkenler için (app.config kendi .env'ini
# pydantic-settings ile zaten okur, bu yalnızca os.getenv kullanan yerler
# için gerekli).
load_dotenv()

DSN = os.getenv("TEST_DSN", "postgresql+asyncpg://hesaplik:hesaplik@localhost:5432/hesaplik_test")


# ------------------------------------------------------------------ LLM izolasyonu
#
# Testler gerçek bir LLM servisine (Ollama/vLLM) ASLA bağlanmaz: CI'da ve
# sunucuda böyle bir servis yok, yerelde açıksa da her çağrı 10+ saniye
# sürüp testi kilitliyor. .env'de LLM_PROVIDER=ollama olsa bile aşağıdaki
# autouse fixture'lar devrede olduğu için hiçbir test yanlışlıkla ağa
# çıkamaz. LLM'e düşen davranışı test etmek isteyen testler `fake_llm`
# fixture'ını isteyip `fake_llm.intent = ...` ile yanıtı belirler.


class FakeLLMProvider:
    """LLMProvider Protocol'ünü karşılayan, ağa hiç çıkmayan sahte
    sağlayıcı. `intent` None ise "LLM de çözemedi" davranışını taklit
    eder (UNRECOGNIZED'a düşer); bir ParsedIntent verilirse her çağrıda
    onu döner. Deterministiktir — aynı girdi her koşuda aynı sonucu verir.

    `name_match` ayrıca isim eşleştirme + öngörücü teyit testleri için
    (bkz. app/services/llm_provider.suggest_person_match,
    intent_resolver._llm_suggest_person): None ise chat_json hiçbir eşleşme
    bulamamış gibi davranır (eslesen_kisi: null), bir isim verilirse o ismi
    "eslesen_kisi" olarak döner (gerçek isim doğrulaması suggest_person_match
    içinde zaten yapılıyor, burada sahte sağlayıcı yalnızca LLM'in HAM
    çıktısını taklit eder)."""

    def __init__(self, intent: ParsedIntent | None = None):
        self.intent = intent
        self.calls: list[str] = []
        self.name_match: str | None = None
        self.chat_json_calls: list[tuple[str, str]] = []

    @property
    def called(self) -> bool:
        return bool(self.calls)

    async def parse(self, text: str) -> ParsedIntent | None:
        self.calls.append(text)
        return self.intent

    async def chat_json(
        self, system_prompt: str, user_text: str, max_tokens: int | None = None
    ) -> dict | None:
        self.chat_json_calls.append((system_prompt, user_text))
        return {"eslesen_kisi": self.name_match}


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    """Her testte get_active_provider()'ı sahte sağlayıcıya çevirir. Ayrıca
    settings.llm_provider'ı "none" yapar ve timeout'ları kısaltır: bir kod
    yolu get_active_provider()'ı atlayıp doğrudan bir provider kurmaya
    kalkarsa bile gerçek bir istek uzun süre asılı kalamaz. Sağlık
    önbelleği de her testte temizlenir ki testler arasında sızmasın."""
    llm_provider.reset_health_cache()
    provider = FakeLLMProvider()

    async def _get_active_provider(session):
        return provider

    monkeypatch.setattr(llm_provider, "get_active_provider", _get_active_provider)
    monkeypatch.setattr(settings, "llm_provider", "none")
    monkeypatch.setattr(settings, "llm_timeout", 1.0)
    monkeypatch.setattr(settings, "vllm_timeout", 1.0)
    monkeypatch.setattr(settings, "llm_health_timeout", 1.0)
    return provider


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Son güvenlik ağı: httpx'in GERÇEK taşıyıcısı test sırasında hiç
    çalışmamalı. httpx.MockTransport ayrı bir sınıf olduğu için
    tests/test_llm_provider.py'nin mock'lu OllamaProvider testleri
    etkilenmez; kaçak bir çağrı olursa sessizce beklemek yerine hemen
    patlar ve testte görünür."""

    def _blocked(*_args, **_kwargs):
        raise RuntimeError(
            "Testlerde gerçek ağ çağrısı yasak (httpx). LLM için "
            "conftest.FakeLLMProvider ya da httpx.MockTransport kullan."
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked)


# ------------------------------------------------------------------ auth (JWT)
#
# Tüm testler aynı sabit kullanıcı/şifre çiftini kullanır. Hash düşük
# rounds'la (4) üretilir — güvenlik için değil, bcrypt kasıtlı yavaş ve
# testlerde onlarca kez doğrulanıyor; üretimde gensalt() varsayılanı (12)
# kullanılır (bkz. app/services/auth.py).
AUTH_USERNAME = "test-kullanici"
AUTH_PASSWORD = "cok-gizli-parola"
_AUTH_PASSWORD_HASH = bcrypt.hashpw(AUTH_PASSWORD.encode("utf-8"), bcrypt.gensalt(4)).decode("utf-8")


@pytest.fixture
def auth_account(monkeypatch):
    """auth_username/auth_password_hash/jwt_secret'ı ayarlar, giriş
    kilidini temizler. Testler AUTH_USERNAME/AUTH_PASSWORD ile giriş
    yapabilir."""
    monkeypatch.setattr(settings, "auth_username", AUTH_USERNAME)
    monkeypatch.setattr(settings, "auth_password_hash", _AUTH_PASSWORD_HASH)
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setattr(settings, "jwt_expire_hours", 24)
    auth._failures.clear()
    return AUTH_USERNAME, AUTH_PASSWORD


@pytest.fixture
def auth_headers(auth_account):
    """Geçerli bir Authorization header'ı — endpoint fonksiyonlarını
    doğrudan çağıran testler için değil, HTTP üzerinden çağıranlar için."""
    token, _ = auth.create_access_token(AUTH_USERNAME)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    eng = create_async_engine(DSN, echo=False)
    with open("db/schema.sql", encoding="utf-8") as f:
        ddl = f.read()
    raw = await asyncpg.connect(DSN.replace("postgresql+asyncpg://", "postgresql://"))
    await raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await raw.execute(ddl)
    await raw.close()
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session(engine):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
        for t in ("audit_log", "web_chat_pending", "raw_messages", "pending_requests", "restore_requests",
                  "archived_transactions",
                  "archived_persons", "transaction_lines", "transactions",
                  "price_history", "product_aliases", "products",
                  "person_aliases", "persons"):
            await s.execute(text(f"TRUNCATE {t} RESTART IDENTITY CASCADE"))
        await s.commit()
