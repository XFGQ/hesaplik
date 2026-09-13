"""API açılış bildirimi (app/services/acilis_bildirimi.py) ve ortak durum
raporu (app/services/durum.py).

Kilitlenenler:
- /durum ile açılış bildirimi AYNI raporu üretir (build_durum_raporu),
- api kaynağı bot canlılığını UYDURMAZ,
- lifespan bildirimi göndermeye çalışır ama beklemez,
- Telegram/veritabanı hatası API'yi düşürmez, bildirim yalnızca loglanır.

Telegram'a ve LLM katmanlarına gerçekten GİDİLMEZ.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import main as app_main
from app.config import settings
from app.services import acilis_bildirimi, durum, llm_provider, telegram_yedek

ADMIN_CHAT = 4242


def _llm():
    def kaynak(ok):
        return llm_provider.SourceStatus(ok=ok, url="http://x", model="m")

    return llm_provider.LLMStatus(
        primary="auto", active="nvidia", nvidia=kaynak(True), vllm=kaynak(False), ollama=kaynak(True)
    )


@pytest.fixture
def sahte_llm(monkeypatch):
    async def _status(session):
        return _llm()

    monkeypatch.setattr(llm_provider, "get_status", _status)


@pytest_asyncio.fixture(loop_scope="session")
async def test_db(engine, monkeypatch):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(acilis_bildirimi, "SessionLocal", maker)
    return maker


class _BosSession:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def sahte_db(monkeypatch):
    monkeypatch.setattr(acilis_bildirimi, "SessionLocal", _BosSession)


@pytest.fixture
def acik(monkeypatch):
    monkeypatch.setattr(settings, "acilis_bildirimi", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "123:gizli")
    monkeypatch.setattr(settings, "telegram_admin_chat_id", str(ADMIN_CHAT))


@pytest.fixture
def sahte_telegram(monkeypatch):
    gonder = AsyncMock()
    monkeypatch.setattr(telegram_yedek, "mesaj_gonder", gonder)
    return gonder


# ---------------------------------------------------------------- ortak rapor


async def test_build_durum_raporu_bot_icin_tum_bolumler(session, sahte_llm):
    metin = await durum.build_durum_raporu(session)

    for beklenen in (
        "🩺 Sistem durumu",
        "Veritabanı: ✅ sağlıklı",
        "Telegram botu: ✅ çalışıyor",
        "📊 Cari Hesap",
        "Aktif: NVIDIA (tercih: otomatik)",
        "💾 Yedekleme",
        "📨 Mesajlar",
    ):
        assert beklenen in metin, beklenen
    assert "API:" not in metin  # /durum çıktısı değişmedi


async def test_build_durum_raporu_api_bot_canliligini_uydurmaz(session, sahte_llm):
    metin = await durum.build_durum_raporu(session, kaynak="api")

    assert "API: ✅ çalışıyor" in metin
    assert "Telegram botu: ✅" not in metin
    assert "/durum" in metin
    assert "📊 Cari Hesap" in metin


def test_build_durum_raporu_topla_ve_bicimle_mock(monkeypatch):
    rapor = durum.DurumRaporu(db_ok=False)
    topla = AsyncMock(return_value=rapor)
    monkeypatch.setattr(durum, "durum_topla", topla)

    metin = asyncio.run(durum.build_durum_raporu(object(), kaynak="api"))

    topla.assert_awaited_once()
    assert metin == durum.format_durum(rapor, "api")
    assert "Veritabanı: ❌ ERİŞİLEMİYOR" in metin


def test_mesaj_metni_baslik_zaman_ve_rapor():
    metin = acilis_bildirimi.mesaj_metni(
        "🩺 Sistem durumu", datetime(2026, 9, 13, 9, 5, 7, tzinfo=timezone.utc)
    )
    assert metin == "🚀 Sistem ayağa kalktı\n🕒 13.09.2026 12:05:07\n\n🩺 Sistem durumu"


# ---------------------------------------------------------------- gönderim


async def test_gonder_yoneticiye_rapor_yollar(session, test_db, acik, sahte_llm, sahte_telegram):
    assert await acilis_bildirimi.gonder() is True

    sahte_telegram.assert_awaited_once()
    chat_id, metin = sahte_telegram.await_args.args
    assert chat_id == ADMIN_CHAT
    assert metin.startswith("🚀 Sistem ayağa kalktı\n🕒 ")
    assert "🩺 Sistem durumu" in metin and "API: ✅ çalışıyor" in metin
    assert "📊 Cari Hesap" in metin


async def test_gonder_ayar_kapaliysa_hic_denemez(sahte_db, acik, sahte_telegram, monkeypatch):
    monkeypatch.setattr(settings, "acilis_bildirimi", False)

    assert await acilis_bildirimi.gonder() is False
    sahte_telegram.assert_not_awaited()


@pytest.mark.parametrize("alan", ["telegram_bot_token", "telegram_admin_chat_id"])
async def test_gonder_yapilandirma_eksikse_atlar(sahte_db, acik, sahte_telegram, monkeypatch, alan):
    monkeypatch.setattr(settings, alan, "")

    assert await acilis_bildirimi.gonder() is False
    sahte_telegram.assert_not_awaited()


async def test_telegram_hatasi_firlatilmaz_loglanir(
    session, test_db, acik, sahte_llm, monkeypatch, caplog
):
    monkeypatch.setattr(
        telegram_yedek, "mesaj_gonder",
        AsyncMock(side_effect=telegram_yedek.YedekHatasi("Telegram'a bağlanılamadı: ConnectError")),
    )

    with caplog.at_level("WARNING", logger=acilis_bildirimi.log.name):
        assert await acilis_bildirimi.gonder() is False

    assert "Açılış bildirimi gönderilemedi: Telegram'a bağlanılamadı" in caplog.text


async def test_beklenmedik_hata_firlatilmaz_token_loga_sizmaz(
    sahte_db, acik, sahte_telegram, monkeypatch, caplog
):
    monkeypatch.setattr(
        durum, "build_durum_raporu",
        AsyncMock(side_effect=RuntimeError("https://api.telegram.org/bot123:gizli/x")),
    )

    with caplog.at_level("WARNING", logger=acilis_bildirimi.log.name):
        assert await acilis_bildirimi.gonder() is False

    sahte_telegram.assert_not_awaited()
    assert "RuntimeError" in caplog.text
    assert "gizli" not in caplog.text


async def test_gercek_mesaj_gonder_ag_hatasini_yedek_hatasina_cevirir(acik, monkeypatch):
    """Sahte olmayan mesaj_gonder: httpx hatası token'sız YedekHatasi olur,
    gonder() onu yakalar."""
    import httpx

    def _istemci():
        return httpx.AsyncClient(transport=httpx.MockTransport(_patla))

    def _patla(request):
        raise httpx.ConnectError(f"bağlanamadı {request.url}")

    monkeypatch.setattr(telegram_yedek, "_istemci", _istemci)

    with pytest.raises(telegram_yedek.YedekHatasi) as e:
        await telegram_yedek.mesaj_gonder(ADMIN_CHAT, "merhaba")
    assert "gizli" not in str(e.value)


# ---------------------------------------------------------------- lifespan


async def test_lifespan_bildirimi_baslatir(monkeypatch):
    cagrildi = asyncio.Event()

    async def _gonder():
        cagrildi.set()
        return True

    monkeypatch.setattr(acilis_bildirimi, "gonder", _gonder)

    async with app_main.lifespan(app_main.app):
        await asyncio.wait_for(cagrildi.wait(), timeout=1)


async def test_lifespan_yavas_bildirimi_beklemez_kapanista_iptal_eder(monkeypatch):
    basladi, iptal = asyncio.Event(), asyncio.Event()

    async def _asili():
        basladi.set()
        try:
            await asyncio.sleep(3600)  # Telegram cevap vermiyor
        except asyncio.CancelledError:
            iptal.set()
            raise

    monkeypatch.setattr(acilis_bildirimi, "gonder", _asili)

    # Açılış 1 sn içinde tamamlanmalı; bildirim asılı kalsa bile.
    cm = app_main.lifespan(app_main.app)
    await asyncio.wait_for(cm.__aenter__(), timeout=1)
    await asyncio.wait_for(basladi.wait(), timeout=1)  # görev gerçekten asılı
    await cm.__aexit__(None, None, None)

    assert iptal.is_set()


async def test_lifespan_telegram_kapaliyken_api_acilir(
    session, test_db, acik, sahte_llm, monkeypatch
):
    """Uçtan uca: gerçek gonder(), Telegram hata veriyor → lifespan yine açılır
    ve API istek karşılar."""
    from httpx import ASGITransport, AsyncClient

    telegram = AsyncMock(side_effect=telegram_yedek.YedekHatasi("Telegram reddetti: 502"))
    monkeypatch.setattr(telegram_yedek, "mesaj_gonder", telegram)

    async with app_main.lifespan(app_main.app):
        async with AsyncClient(transport=ASGITransport(app=app_main.app), base_url="http://t") as c:
            assert (await c.get("/api/health")).json() == {"ok": True}
        for _ in range(100):
            if telegram.await_count:
                break
            await asyncio.sleep(0.02)

    telegram.assert_awaited_once()
