"""Telegram "/" komut menüsü (CLAUDE.md > '"/" komut menüsü').

Komutlar kendi defter mantıklarını KURMAZ, mevcut olanı TETİKLER. Buradaki
testler tam olarak bunu kilitler:

- DİREKT komutlar (/yedek, /bakiye, argümanlı /kisi ve /koy) tek adımda
  mevcut fonksiyonlara gider,
- ADIM ADIM komutlar (/borc, /tahsilat, /kisiekle) mevcut pending/state
  akışlarına bağlanır: "hangisi?" seçimi, "defterde yok → Ekleyeyim mi?"
  ve adım adım kişi ekleme (Geç/Hepsini geç) aynen çalışır,
- /yedek YALNIZCA TELEGRAM_ADMIN_CHAT_ID'de çalışır.

Ayrıştırmanın kendisi tests/test_parser.py'de, kişi eşleştirme güvenliği
tests/test_intent_resolver.py'de test edilir — burada botun komut yolu var.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.config import settings
from app.models import AuditLog, Person, Product, Transaction, TxKind
from app.services import telegram_yedek
from test_bot_running_format import (
    FakeContext,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    _reply_texts,
    _tx_count,
)

ADMIN_CHAT = 4242


@pytest_asyncio.fixture(loop_scope="session")
async def patch_session_local(engine, monkeypatch):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(bot_main, "SessionLocal", maker)
    return maker


class KomutContext(FakeContext):
    """python-telegram-bot CommandHandler'ı komut argümanlarını context.args
    olarak verir ("/bakiye ahmet" -> ["ahmet"])."""

    def __init__(self, *args: str):
        super().__init__()
        self.args = list(args)


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    person = Person(full_name="Ahmet Yılmaz", district="Bergama")
    session.add_all([person, Product(name="Saman", base_unit="balya")])
    await session.flush()
    await session.commit()
    return person


async def _son_cevap(update: FakeUpdate) -> str:
    return _reply_texts(update)[-1]


# ---------------------------------------------------------------- /yedek (admin)


@pytest.fixture
def admin_chat(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_chat_id", str(ADMIN_CHAT))


@pytest.fixture
def sahte_yedek(monkeypatch):
    """Gerçek pg_dump/Telegram çağrısı YAPILMAZ: burada test edilen şey
    komutun yetkilendirmesi ve sonucu nasıl gösterdiği."""
    cagrilar: list[int] = []

    async def _gonder(chat_id: int):
        cagrilar.append(chat_id)
        return telegram_yedek.YedekSonucu(
            True, "Gönderildi: hesaplik_2026-09-13_0400.sql.gz (512 KB)", "gonderildi",
            "hesaplik_2026-09-13_0400.sql.gz", 524288,
        )

    monkeypatch.setattr(telegram_yedek, "yedek_gonder", _gonder)
    return cagrilar


async def test_yedek_yalnizca_yonetici_chatinde_calisir(
    session, patch_session_local, admin_chat, sahte_yedek
):
    update = FakeUpdate(FakeMessage(chat_id=999, text="/yedek"))
    await bot_main.cmd_yedek(update, KomutContext())

    assert await _son_cevap(update) == "Bu komut sadece yönetici içindir."
    assert sahte_yedek == []  # yedek ALINMADI


async def test_yedek_admin_chatinde_calisir_ve_denetime_yazilir(
    session, patch_session_local, admin_chat, sahte_yedek
):
    update = FakeUpdate(FakeMessage(chat_id=ADMIN_CHAT, text="/yedek"))
    await bot_main.cmd_yedek(update, KomutContext())

    assert sahte_yedek == [ADMIN_CHAT]
    # Dosyanın kendisi (başlığıyla) zaten gitti; üstüne "gönderildi" yazılmaz.
    assert _reply_texts(update) == ["Yedek alınıyor…"]

    kayit = (
        await session.execute(
            select(AuditLog).where(AuditLog.action == telegram_yedek.AUDIT_ACTION)
        )
    ).scalar_one()
    assert kayit.actor == f"telegram:{ADMIN_CHAT}"
    assert kayit.after["durum"] == "gonderildi"


async def test_yedek_basarisizsa_sebep_gosterilir(
    session, patch_session_local, admin_chat, monkeypatch
):
    async def _gonder(chat_id: int):
        return telegram_yedek.YedekSonucu(False, "Telegram reddetti: chat not found", "hata")

    monkeypatch.setattr(telegram_yedek, "yedek_gonder", _gonder)

    update = FakeUpdate(FakeMessage(chat_id=ADMIN_CHAT, text="/yedek"))
    await bot_main.cmd_yedek(update, KomutContext())

    assert await _son_cevap(update) == "Yedek alınamadı. Telegram reddetti: chat not found"


async def test_yedek_chat_id_tanimsizsa_kimseye_calismaz(
    session, patch_session_local, sahte_yedek, monkeypatch
):
    """Yanlış yapılandırmada komut AÇIK kalmaz (fail-closed)."""
    monkeypatch.setattr(settings, "telegram_admin_chat_id", "")

    update = FakeUpdate(FakeMessage(chat_id=ADMIN_CHAT, text="/yedek"))
    await bot_main.cmd_yedek(update, KomutContext())

    assert await _son_cevap(update) == "Bu komut sadece yönetici içindir."
    assert sahte_yedek == []


# ---------------------------------------------------------------- /bakiye, /kisi, /koy (direkt)


async def test_bakiye_argumansiz_defter_toplamini_verir(session, patch_session_local, ahmet):
    update = FakeUpdate(FakeMessage(text="/bakiye"))
    await bot_main.cmd_bakiye(update, KomutContext())

    assert "Defter toplamı" in await _son_cevap(update)


async def test_bakiye_isimle_kisinin_bakiyesini_verir(session, patch_session_local, ahmet):
    update = FakeUpdate(FakeMessage(text="/bakiye ahmet yılmaz"))
    await bot_main.cmd_bakiye(update, KomutContext("ahmet", "yılmaz"))

    cevap = await _son_cevap(update)
    assert "Ahmet Yılmaz" in cevap
    assert "Güncel bakiye" in cevap


async def test_kisi_argumansiz_sorar_sonra_arar(session, patch_session_local, ahmet):
    context = KomutContext()
    update = FakeUpdate(FakeMessage(text="/kisi"))
    await bot_main.cmd_kisi(update, context)

    assert "Hangi kişiyi arıyorsun" in await _son_cevap(update)
    assert context.chat_data["komut_akisi"]["tip"] == "kisi"

    cevap = FakeUpdate(FakeMessage(text="ahmet"))
    await bot_main.on_text(cevap, context)

    assert "Ahmet Yılmaz" in await _son_cevap(cevap)
    assert "komut_akisi" not in context.chat_data


async def test_kisi_argumanla_dogrudan_arar(session, patch_session_local, ahmet):
    update = FakeUpdate(FakeMessage(text="/kisi ahmet"))
    await bot_main.cmd_kisi(update, KomutContext("ahmet"))

    assert "Ahmet Yılmaz" in await _son_cevap(update)


async def test_koy_ilcedeki_kisileri_listeler(session, patch_session_local, ahmet):
    update = FakeUpdate(FakeMessage(text="/koy bergama"))
    await bot_main.cmd_koy(update, KomutContext("bergama"))

    assert "Ahmet Yılmaz" in await _son_cevap(update)


async def test_koy_argumansiz_sorar(session, patch_session_local, ahmet):
    context = KomutContext()
    update = FakeUpdate(FakeMessage(text="/koy"))
    await bot_main.cmd_koy(update, context)

    assert await _son_cevap(update) == "Hangi ilçe?"
    assert context.chat_data["komut_akisi"]["tip"] == "koy"


# ---------------------------------------------------------------- /borc, /tahsilat (adım adım)


async def test_borc_adim_adim_kisi_sonra_mal_sorar_ve_kaydeder(
    session, patch_session_local, ahmet
):
    context = KomutContext()
    update = FakeUpdate(FakeMessage(text="/borc"))
    await bot_main.cmd_borc(update, context)

    assert "Kimin borcunu eklemek istiyorsun" in await _son_cevap(update)
    assert context.chat_data["komut_akisi"] == {"tip": "kisi_sec", "kind": "debt"}

    kisi = FakeUpdate(FakeMessage(text="ahmet yılmaz"))
    await bot_main.on_text(kisi, context)

    soru = await _son_cevap(kisi)
    assert "Ahmet Yılmaz — ne aldı, ne kadar?" in soru
    assert "180,00 TL/balya" in soru  # varsayılan saman fiyatı hatırlatması
    assert context.chat_data["komut_akisi"]["tip"] == "kayit"
    assert await _tx_count(session) == 0  # kişi seçmek kayıt DEĞİLDİR

    mal = FakeUpdate(FakeMessage(text="20 balya saman 5000 tl"))
    await bot_main.on_text(mal, context)

    assert "20 balya Saman · 5.000,00 TL borç eklendi" in await _son_cevap(mal)
    assert "komut_akisi" not in context.chat_data
    tx = (await session.execute(select(Transaction))).scalar_one()
    assert tx.kind == TxKind.DEBIT
    assert tx.amount_try == Decimal("5000.00")


async def test_borc_anlasilmayan_cevapta_soru_acik_kalir(session, patch_session_local, ahmet):
    context = KomutContext("ahmet", "yılmaz")
    await bot_main.cmd_borc(FakeUpdate(FakeMessage(text="/borc ahmet yılmaz")), context)
    assert context.chat_data["komut_akisi"]["tip"] == "kayit"

    bos = FakeUpdate(FakeMessage(text="hımm bilmem"))
    await bot_main.on_text(bos, context)

    assert "Anlayamadım" in await _son_cevap(bos)
    # Akış DÜŞMEZ: kullanıcı yeniden yazabilmeli, baştan başlamak zorunda değil.
    assert context.chat_data["komut_akisi"]["tip"] == "kayit"
    assert await _tx_count(session) == 0


async def test_tahsilat_ciplak_tutarla_kaydeder(session, patch_session_local, ahmet):
    """Çıplak sayı PARADIR, adet değil. Parser'a bırakılsaydı "ahmet yılmaz
    5000" fiilsiz saman kalıbına düşer ve "5000 balya saman tahsilat mı?"
    diye sorulurdu (900.000 TL!) — komut zaten "ne kadar?" diye sorduğu için
    cevabın tamamı önce tutar olarak çözülür."""
    context = KomutContext("ahmet", "yılmaz")
    update = FakeUpdate(FakeMessage(text="/tahsilat ahmet yılmaz"))
    await bot_main.cmd_tahsilat(update, context)

    assert "Ahmet Yılmaz — ne ödedi, ne kadar?" in await _son_cevap(update)

    cevap = FakeUpdate(FakeMessage(text="5000"))
    await bot_main.on_text(cevap, context)

    tx = (await session.execute(select(Transaction))).scalar_one()
    assert tx.kind == TxKind.CREDIT
    assert tx.amount_try == Decimal("5000.00")


async def test_borcta_da_ciplak_sayi_tl_sayilir(session, patch_session_local, ahmet):
    """Aynı kural /borc'ta da geçerli: "5 bin" = 5.000 TL borç, 5000 balya
    saman değil. Adet kastediliyorsa birim/ürün yazılır (aşağıdaki test)."""
    context = KomutContext("ahmet", "yılmaz")
    await bot_main.cmd_borc(FakeUpdate(FakeMessage(text="/borc ahmet yılmaz")), context)

    cevap = FakeUpdate(FakeMessage(text="5 bin"))
    await bot_main.on_text(cevap, context)

    tx = (await session.execute(select(Transaction))).scalar_one()
    assert tx.kind == TxKind.DEBIT
    assert tx.amount_try == Decimal("5000.00")


async def test_birim_yazilirsa_adet_sayilir_ve_saman_fiyatindan_hesaplanir(
    session, patch_session_local, ahmet
):
    """"20 balya" tutar DEĞİL adettir: normal ayrıştırmaya düşer, ürün
    varsayıldığı için (saman) kayıttan önce sorulur — komut yolu mevcut
    varsayılan saman fiyatı akışını bozmaz (CLAUDE.md > "Varsayılan saman
    fiyatı")."""
    context = KomutContext("ahmet", "yılmaz")
    await bot_main.cmd_borc(FakeUpdate(FakeMessage(text="/borc ahmet yılmaz")), context)

    cevap = FakeUpdate(FakeMessage(text="20 balya"))
    await bot_main.on_text(cevap, context)

    soru = await _son_cevap(cevap)
    assert "20 balya Saman" in soru
    assert "180,00 TL/balya = 3.600,00 TL" in soru
    assert await _tx_count(session) == 0  # önce soruldu, kaydedilmedi

    onay = FakeQuery(data="llm:yes")
    await bot_main.on_callback(FakeUpdate(callback_query=onay), context)

    tx = (await session.execute(select(Transaction))).scalar_one()
    assert tx.amount_try == Decimal("3600.00")


async def test_borc_kisi_yoksa_adim_adim_ekleme_akisina_baglanir(
    session, patch_session_local, ahmet
):
    """Kişi yoksa /borc kendi kişi ekleme akışını YAZMAZ: mevcut "Ekleyeyim
    mi? → ad soyad → telefon/il/ilçe" akışına girer, kişi açılınca mal
    sorusuna döner."""
    context = KomutContext("zübeyir", "kaplan")
    update = FakeUpdate(FakeMessage(text="/borc zübeyir kaplan"))
    await bot_main.cmd_borc(update, context)

    assert "Zübeyir Kaplan defterde yok. Ekleyeyim mi?" in await _son_cevap(update)
    assert context.chat_data["pending"]["komut_kind"] == "debt"

    evet = FakeQuery(data="person:yes")
    await bot_main.on_callback(FakeUpdate(callback_query=evet), context)
    assert "Ad soyad: Zübeyir Kaplan" in evet.edit_message_text.await_args_list[-1].args[0]

    hepsini_gec = FakeQuery(data="newperson:skip_all")
    await bot_main.on_callback(FakeUpdate(callback_query=hepsini_gec), context)

    mesajlar = [c.args[0] for c in hepsini_gec.message.reply_text.await_args_list]
    assert "✅ Zübeyir Kaplan eklendi." in mesajlar
    assert any("ne aldı, ne kadar?" in m for m in mesajlar)
    assert context.chat_data["komut_akisi"]["kind"] == "debt"
    assert await _tx_count(session) == 0  # kişi açmak kayıt DEĞİLDİR

    kisi = (
        await session.execute(select(Person).where(Person.full_name == "Zübeyir Kaplan"))
    ).scalar_one()
    assert kisi.is_active


async def test_borc_birden_cok_aday_varsa_sorar_secince_devam_eder(
    session, patch_session_local, ahmet, monkeypatch
):
    """Kişi eşleştirme güvenliği komutta da geçerli: iki aday varsa otomatik
    seçilmez. (Eşiklerin kendisi tests/test_intent_resolver.py'de test edilir;
    burada botun SORDUĞU soru ve seçim sonrası akış doğrulanır.)"""
    ikinci = Person(full_name="Ahmet Yıldırım")
    session.add(ikinci)
    await session.flush()
    await session.commit()

    async def _iki_aday(session_, isim, *, allow_llm_suggestion=True):
        return None, [ahmet, ikinci]

    monkeypatch.setattr(bot_main, "find_person_match", _iki_aday)

    context = KomutContext("ahmet")
    update = FakeUpdate(FakeMessage(text="/borc ahmet"))
    await bot_main.cmd_borc(update, context)

    assert "Hangisini demek istedin?" in await _son_cevap(update)
    keyboard = update.message.reply_text.await_args_list[-1].kwargs["reply_markup"]
    butonlar = [b.callback_data for satir in keyboard.inline_keyboard for b in satir]
    assert butonlar == [f"komut:pick:{ahmet.id}", f"komut:pick:{ikinci.id}", "komut:new"]

    sec = FakeQuery(data=f"komut:pick:{ikinci.id}")
    await bot_main.on_callback(FakeUpdate(callback_query=sec), context)

    soru = sec.message.reply_text.await_args_list[-1].args[0]
    assert "Ahmet Yıldırım — ne aldı, ne kadar?" in soru
    assert context.chat_data["komut_akisi"]["person_id"] == ikinci.id


# ---------------------------------------------------------------- /kisiekle


async def test_kisiekle_mevcut_adim_adim_akisi_tetikler(session, patch_session_local):
    context = KomutContext()
    update = FakeUpdate(FakeMessage(text="/kisiekle"))
    await bot_main.cmd_kisiekle(update, context)

    assert await _son_cevap(update) == "Ad soyad?"

    isim = FakeUpdate(FakeMessage(text="veli demir"))
    await bot_main.on_text(isim, context)
    assert "Ad soyad: Veli Demir — doğru mu?" in await _son_cevap(isim)

    onay = FakeQuery(data="newperson:confirm")
    await bot_main.on_callback(FakeUpdate(callback_query=onay), context)
    assert "Telefon?" in onay.edit_message_text.await_args_list[-1].args[0]

    gec = FakeQuery(data="newperson:skip_all")
    await bot_main.on_callback(FakeUpdate(callback_query=gec), context)

    mesajlar = [c.args[0] for c in gec.message.reply_text.await_args_list]
    assert "✅ Veli Demir eklendi." in mesajlar
    kisi = (
        await session.execute(select(Person).where(Person.full_name == "Veli Demir"))
    ).scalar_one()
    assert kisi.is_active


# ---------------------------------------------------------------- menü ve akış temizliği


def test_komut_menusu_admin_komutlarini_musteriye_gostermez():
    musteri = [c.command for c in bot_main.komut_listesi()]
    yonetici = [c.command for c in bot_main.komut_listesi(admin=True)]

    assert "yedek" not in musteri and "durum" not in musteri
    assert "yedek" in yonetici and "durum" in yonetici
    for komut in ("borc", "tahsilat", "bakiye", "kisi", "koy", "kisiekle"):
        assert komut in musteri and komut in yonetici


def test_yedek_chati_yonetici_menusune_eklenir(monkeypatch):
    """/durum ve /yedek FARKLI ayarlarla yetkilendirilir; menü ikisini de
    kapsamalı, yoksa yedek chat'inde "/" listesinde /yedek görünmezdi."""
    monkeypatch.setattr(settings, "telegram_admin_ids", "11")
    monkeypatch.setattr(settings, "telegram_admin_chat_id", "22")

    assert bot_main._admin_chat_idleri() == [11, 22]


def _ayni_sohbet(context: KomutContext, *args: str) -> KomutContext:
    """Aynı sohbette YENİ bir komut: python-telegram-bot her güncellemede yeni
    bir context nesnesi verir ama chat_data AYNI sözlüktür."""
    yeni = KomutContext(*args)
    yeni.chat_data = context.chat_data
    yeni.bot = context.bot
    return yeni


async def test_yeni_komut_yarim_kalan_soruyu_dusurur(session, patch_session_local, ahmet):
    """"/borc" deyip cevaplamadan "/bakiye" yazan kullanıcının sonraki mesajı
    borç sorusunun cevabı sanılmamalı."""
    context = KomutContext()
    await bot_main.cmd_borc(FakeUpdate(FakeMessage(text="/borc")), context)
    assert "komut_akisi" in context.chat_data

    await bot_main.cmd_bakiye(FakeUpdate(FakeMessage(text="/bakiye")), _ayni_sohbet(context))
    assert "komut_akisi" not in context.chat_data


# ---------------------------------------------------------------- /bakiye harun (ek soyma)


async def test_bakiye_komutu_ek_gibi_biten_ismi_bulur(session, patch_session_local):
    """"harun" "-un" ekiyle bitiyor gibi görünür; eskiden "har"a soyulup
    "defterde yok" deniyordu. Düz metinle ("harun bakiye") AYNI sonuç: tek
    aday ama benzerlik güçlü değil, "hangisi?" sorulur — otomatik seçilmez."""
    session.add(Person(full_name="Harun Aydemir"))
    await session.commit()

    komut = FakeUpdate(FakeMessage(text="/bakiye harun"))
    await bot_main.cmd_bakiye(komut, KomutContext("harun"))

    assert await _son_cevap(komut) == "Hangisini demek istedin?"
    markup = komut.message.reply_text.await_args_list[-1].kwargs["reply_markup"]
    assert "Harun Aydemir" in str(markup)


async def test_bakiye_komutu_ve_duz_metin_ayni_cevabi_verir(session, patch_session_local):
    session.add(Person(full_name="Harun Aydemir"))
    await session.commit()

    komut = FakeUpdate(FakeMessage(text="/bakiye harun"))
    await bot_main.cmd_bakiye(komut, KomutContext("harun"))

    metin = FakeUpdate(FakeMessage(text="harun bakiye"))
    await bot_main.on_text(metin, FakeContext())

    assert _reply_texts(komut) == _reply_texts(metin)


# ---------------------------------------------------------------- /listele


async def test_listele_tum_kisileri_bakiyeleriyle_listeler(session, patch_session_local, ahmet):
    session.add(Person(full_name="Veli Demir"))
    await session.commit()

    update = FakeUpdate(FakeMessage(text="/listele"))
    await bot_main.cmd_listele(update, KomutContext())

    cevap = await _son_cevap(update)
    assert "Ahmet Yılmaz" in cevap and "Veli Demir" in cevap


async def test_listele_duz_metindeki_kisileri_listele_ile_ayni(session, patch_session_local, ahmet):
    komut = FakeUpdate(FakeMessage(text="/listele"))
    await bot_main.cmd_listele(komut, KomutContext())

    metin = FakeUpdate(FakeMessage(text="kişileri listele"))
    await bot_main.on_text(metin, FakeContext())

    assert _reply_texts(komut) == _reply_texts(metin)


def test_listele_menude_ve_yardimda():
    assert "listele" in [c.command for c in bot_main.komut_listesi()]
    assert "/listele" in bot_main.YARDIM_METNI


# ---------------------------------------------------------------- /durum (admin)


async def test_durum_yalnizca_admin_chat_id_tanimli_yoneticide_calisir(
    session, patch_session_local, monkeypatch
):
    """Eskiden _is_admin yalnızca TELEGRAM_ADMIN_IDS'e bakıyordu: menüde
    /durum'u gören (TELEGRAM_ADMIN_CHAT_ID) yönetici sessizlikle karşılaşıyordu."""
    monkeypatch.setattr(settings, "telegram_admin_ids", "")
    monkeypatch.setattr(settings, "telegram_admin_chat_id", str(ADMIN_CHAT))

    update = FakeUpdate(FakeMessage(chat_id=ADMIN_CHAT, text="/durum"))
    await bot_main.cmd_durum(update, KomutContext())

    assert "Toplam ham mesaj" in await _son_cevap(update)


async def test_durum_admin_ids_ile_de_calisir(session, patch_session_local, monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", f" 11 , {ADMIN_CHAT} ")
    monkeypatch.setattr(settings, "telegram_admin_chat_id", "")

    update = FakeUpdate(FakeMessage(chat_id=ADMIN_CHAT, text="/durum"))
    await bot_main.cmd_durum(update, KomutContext())

    assert "Toplam ham mesaj" in await _son_cevap(update)


async def test_durum_yetkisizde_sessiz_kalir_ama_loga_yazar(
    session, patch_session_local, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "telegram_admin_ids", "11")
    monkeypatch.setattr(settings, "telegram_admin_chat_id", str(ADMIN_CHAT))

    update = FakeUpdate(FakeMessage(chat_id=999, text="/durum"))
    with caplog.at_level("WARNING", logger=bot_main.logger.name):
        await bot_main.cmd_durum(update, KomutContext())

    assert _reply_texts(update) == []  # komutun varlığı sızmaz
    assert "yetkisiz /durum denemesi: chat_id=999" in caplog.text
