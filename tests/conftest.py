import os

import asyncpg
import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.services import llm_provider
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
    onu döner. Deterministiktir — aynı girdi her koşuda aynı sonucu verir."""

    def __init__(self, intent: ParsedIntent | None = None):
        self.intent = intent
        self.calls: list[str] = []

    @property
    def called(self) -> bool:
        return bool(self.calls)

    async def parse(self, text: str) -> ParsedIntent | None:
        self.calls.append(text)
        return self.intent


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
        for t in ("audit_log", "raw_messages", "pending_requests",
                  "archived_transactions",
                  "archived_persons", "transaction_lines", "transactions",
                  "price_history", "product_aliases", "products",
                  "person_aliases", "persons"):
            await s.execute(text(f"TRUNCATE {t} RESTART IDENTITY CASCADE"))
        await s.commit()
