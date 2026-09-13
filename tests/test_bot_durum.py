"""/durum yönetici raporu (CLAUDE.md > '"/" komut menüsü').

Kilitlenenler:
- yetki: TELEGRAM_ADMIN_IDS ∪ TELEGRAM_ADMIN_CHAT_ID; yetkisize sessiz, logda iz,
- bölümler: 📊 Cari Hesap, 🤖 LLM, 💾 Yedekleme, 📨 Mesajlar — sayılar doğru,
- "İşlenmemiş" etiketi YOK: processed_at yalnızca kayıt yazan mesajda dolar,
  sorgular tasarım gereği boş kalır → "Kayıt oluşturan" / "Sorgu/diğer",
- son yedek zamanı settings.son_yedek_zamani'dan okunur, başarılı yedek yazar.

LLM katmanları gerçekten YOKLANMAZ (llm_provider.get_status taklit edilir).
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.config import settings
from app.models import AuditLog, Person, RawMessage, Setting, TxSource
from app.services import llm_provider, telegram_yedek
from app.services.ledger import TxMeta, add_debt, add_payment
from app.services.queries import TotalBalance
from test_bot_running_format import FakeContext, FakeMessage, FakeUpdate, _reply_texts

ADMIN_CHAT = 4242
KOK = Path(__file__).resolve().parent.parent


@pytest_asyncio.fixture(loop_scope="session")
async def patch_session_local(engine, monkeypatch):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(bot_main, "SessionLocal", maker)
    return maker


def _llm(active="nvidia", primary="auto", nvidia=True, vllm=False, ollama=True):
    def kaynak(ok):
        return llm_provider.SourceStatus(ok=ok, url="http://x", model="m")

    return llm_provider.LLMStatus(
        primary=primary, active=active,
        nvidia=kaynak(nvidia), vllm=kaynak(vllm), ollama=kaynak(ollama),
    )


@pytest.fixture
def sahte_llm(monkeypatch):
    async def _status(session):
        return _llm()

    monkeypatch.setattr(llm_provider, "get_status", _status)


@pytest.fixture
def yonetici(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "")
    monkeypatch.setattr(settings, "telegram_admin_chat_id", str(ADMIN_CHAT))


def _bos_defter() -> TotalBalance:
    return TotalBalance(
        kisi_sayisi=0, borclu_sayisi=0, alacakli_sayisi=0,
        toplam_alacak=Decimal("0.00"), toplam_borc=Decimal("0.00"),
    )


async def _durum(chat_id: int = ADMIN_CHAT) -> FakeUpdate:
    update = FakeUpdate(FakeMessage(chat_id=chat_id, text="/durum"))
    await bot_main.cmd_durum(update, FakeContext())
    return update


# ---------------------------------------------------------------- yetki


async def test_durum_yalnizca_admin_chat_id_tanimli_yoneticide_calisir(
    session, patch_session_local, yonetici, sahte_llm
):
    """Eskiden _is_admin yalnızca TELEGRAM_ADMIN_IDS'e bakıyordu: menüde
    /durum'u gören (TELEGRAM_ADMIN_CHAT_ID) yönetici sessizlikle karşılaşıyordu."""
    assert "Sistem durumu" in _reply_texts(await _durum())[-1]


async def test_durum_admin_ids_ile_de_calisir(session, patch_session_local, sahte_llm, monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", f" 11 , {ADMIN_CHAT} ")
    monkeypatch.setattr(settings, "telegram_admin_chat_id", "")

    assert "Sistem durumu" in _reply_texts(await _durum())[-1]


async def test_durum_yetkisizde_sessiz_kalir_ama_loga_yazar(
    session, patch_session_local, sahte_llm, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "telegram_admin_ids", "11")
    monkeypatch.setattr(settings, "telegram_admin_chat_id", str(ADMIN_CHAT))

    with caplog.at_level("WARNING", logger=bot_main.logger.name):
        update = await _durum(chat_id=999)

    assert _reply_texts(update) == []  # komutun varlığı sızmaz
    assert "yetkisiz /durum denemesi: chat_id=999" in caplog.text


# ---------------------------------------------------------------- rapor içeriği


async def test_durum_tum_bolumleri_dogru_sayilarla_toplar(
    session, patch_session_local, yonetici, sahte_llm
):
    borclu, alacakli, sifir = Person(full_name="Borçlu"), Person(full_name="Alacaklı"), Person(full_name="Sıfır")
    session.add_all([borclu, alacakli, sifir])
    await session.flush()
    meta = TxMeta(created_by="test", source=TxSource.WEB)
    await add_debt(session, borclu.id, [], meta, amount_override=Decimal("1000"))
    await add_debt(session, alacakli.id, [], meta, amount_override=Decimal("500"))
    await add_payment(session, alacakli.id, Decimal("700"), meta)

    # 2 mesaj deftere kayıt yazmış (processed_at dolu), 3'ü sorgu/teyit (boş).
    simdi = datetime.now(timezone.utc)
    session.add_all(
        [RawMessage(channel="telegram", payload={}, processed_at=simdi) for _ in range(2)]
        + [RawMessage(channel="telegram", payload={}) for _ in range(3)]
    )
    await telegram_yedek.son_yedek_yaz(session, datetime(2026, 9, 13, 0, 28, tzinfo=timezone.utc))
    await session.commit()

    cevap = _reply_texts(await _durum())[-1]

    for beklenen in (
        "Veritabanı: ✅ sağlıklı",
        "Telegram botu: ✅ çalışıyor",
        "📊 Cari Hesap",
        "Kişi: 3",
        "Toplam alacak: 1.000,00 TL (1 kişi sana borçlu)",
        "Toplam borç: 200,00 TL (1 kişi senden alacaklı)",
        "Hesabı sıfır: 1 kişi",
        "🤖 LLM",
        "Aktif: NVIDIA (tercih: otomatik)",
        "✅ NVIDIA · ❌ vLLM · ✅ Ollama",
        "💾 Yedekleme",
        "Son yedek: 13.09.2026 03:28",  # UTC 00:28 -> İstanbul 03:28
        "Otomatik: günde 2 kez (04:00, 06:00)",
        "📨 Mesajlar",
        "Toplam ham mesaj: 5",
        "Kayıt oluşturan: 2",
        "Sorgu/diğer: 3",
    ):
        assert beklenen in cevap, beklenen
    assert "İşlenmemiş" not in cevap


async def test_llm_durumu_alinamazsa_rapor_yine_gelir(
    session, patch_session_local, yonetici, monkeypatch
):
    async def _patlar(session):
        raise RuntimeError("yoklama patladı")

    monkeypatch.setattr(llm_provider, "get_status", _patlar)

    cevap = _reply_texts(await _durum())[-1]
    assert "🤖 LLM\nDurum okunamadı" in cevap
    assert "Kayıt oluşturan: 0" in cevap


def test_bicim_llm_kapali_ve_yedek_hic_yok():
    metin = bot_main._format_durum(
        bot_main.DurumRaporu(
            db_ok=True, toplam=_bos_defter(),
            llm=_llm(active="none", primary="none", nvidia=False, vllm=False, ollama=False),
        )
    )

    assert "Aktif: yok (tercih: kapalı)" in metin
    assert "❌ NVIDIA · ❌ vLLM · ❌ Ollama" in metin
    assert "Son yedek: henüz kayıt yok" in metin


def test_bicim_veritabani_erisilemiyor():
    metin = bot_main._format_durum(bot_main.DurumRaporu(db_ok=False))

    assert "Veritabanı: ❌ ERİŞİLEMİYOR" in metin
    assert "📊" not in metin  # uydurma sıfırlar gösterilmez


# ---------------------------------------------------------------- son yedek zamanı


async def test_son_yedek_zamani_yazilir_okunur_ve_guncellenir(session):
    assert await telegram_yedek.son_yedek_oku(session) is None

    ilk = datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc)
    await telegram_yedek.son_yedek_yaz(session, ilk)
    assert await telegram_yedek.son_yedek_oku(session) == ilk

    ikinci = datetime(2026, 9, 13, 3, 0, tzinfo=timezone.utc)
    await telegram_yedek.son_yedek_yaz(session, ikinci)
    assert await telegram_yedek.son_yedek_oku(session) == ikinci

    ayar = await session.get(Setting, telegram_yedek.SON_YEDEK_KEY)
    assert ayar.value == "2026-09-13T03:00:00+00:00"


async def test_bozuk_son_yedek_degeri_none_doner(session):
    session.add(Setting(key=telegram_yedek.SON_YEDEK_KEY, value="dün gece"))
    await session.flush()

    assert await telegram_yedek.son_yedek_oku(session) is None


async def test_basarili_yedek_zamani_yazar_basarisiz_yazmaz(session):
    await telegram_yedek.sonucu_kaydet(
        session, "test", telegram_yedek.YedekSonucu(False, "Telegram reddetti", "hata")
    )
    assert await telegram_yedek.son_yedek_oku(session) is None

    zaman = datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc)
    await telegram_yedek.sonucu_kaydet(
        session, "test",
        telegram_yedek.YedekSonucu(True, "Gönderildi", "gonderildi", "a.sql.gz", 10, zaman),
    )
    assert await telegram_yedek.son_yedek_oku(session) == zaman

    denetim = (await session.execute(select(AuditLog))).scalars().all()
    assert len(denetim) == 2  # başarısız deneme de iz bırakır


async def test_yedek_komutu_basarida_son_yedek_zamanini_yazar(
    session, patch_session_local, monkeypatch
):
    monkeypatch.setattr(settings, "telegram_admin_chat_id", str(ADMIN_CHAT))
    zaman = datetime(2026, 9, 13, 1, 30, tzinfo=timezone.utc)

    async def _gonder(chat_id: int):
        return telegram_yedek.YedekSonucu(True, "Gönderildi", "gonderildi", "a.sql.gz", 10, zaman)

    monkeypatch.setattr(telegram_yedek, "yedek_gonder", _gonder)

    update = FakeUpdate(FakeMessage(chat_id=ADMIN_CHAT, text="/yedek"))
    await bot_main.cmd_yedek(update, FakeContext())

    assert await telegram_yedek.son_yedek_oku(session) == zaman


def test_otomatik_saatler_zamanlayici_ve_betikle_uyumlu():
    """/durum'daki statik saatler systemd timer'dan sapmasın; otomatik yedeği
    gönderen host betiği de aynı ayarı yazsın."""
    timer = (KOK / "deployment" / "hesaplik-telegram-yedek.timer").read_text()
    for saat in telegram_yedek.OTOMATIK_SAATLER:
        assert f"{saat}:00 Europe/Istanbul" in timer

    betik = (KOK / "scripts" / "telegram-yedek.sh").read_text()
    assert f"'{telegram_yedek.SON_YEDEK_KEY}'" in betik
