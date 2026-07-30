"""Bot çoklu işlem akışı (CLAUDE.md > "Tek mesajda birden çok istek",
Grup 5). Bölme güvenliği app/services/message_splitter.py'de test edilir;
burada bot'un bölünmüş parçaları SIRAYLA işleyip her biri için AYRI cevap
verdiğini doğrular. Kritik: bir parça onay/seçim beklemeye başlarsa
(chat_data["pending_queue"]) kalan parçalar HEMEN işlenmez ama kullanıcı
o onayı cevaplayınca (Evet/Hayır/hangisi — ne olursa olsun) kuyruktaki bir
sonraki parça OTOMATİK işlenir — hiçbir parça sessizce kaybolmaz."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import Person, Product, Transaction


@pytest_asyncio.fixture(loop_scope="session")
async def patch_session_local(engine, monkeypatch):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(bot_main, "SessionLocal", maker)
    return maker


class FakeMessage:
    def __init__(self, chat_id: int = 1, text: str = ""):
        self.chat_id = chat_id
        self.text = text
        self.reply_text = AsyncMock()
        self.reply_document = AsyncMock()


class FakeQuery:
    def __init__(self, message: FakeMessage | None = None, data: str = ""):
        self.message = message or FakeMessage()
        self.data = data
        self.edit_message_text = AsyncMock()
        self.edit_message_reply_markup = AsyncMock()
        self.answer = AsyncMock()


class _FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeUpdate:
    def __init__(self, message: FakeMessage | None = None, callback_query: FakeQuery | None = None):
        self.message = message or FakeMessage()
        self.callback_query = callback_query
        self.effective_chat = _FakeChat(self.message.chat_id)

    def to_dict(self):
        return {
            "update_id": id(self),
            "message": {"text": self.message.text, "chat": {"id": self.message.chat_id}},
        }


class FakeContext:
    def __init__(self):
        self.chat_data: dict = {}
        self.bot = AsyncMock()


def _reply_texts(update: FakeUpdate) -> list[str]:
    return [call.args[0] for call in update.message.reply_text.await_args_list]


async def _tx_count(session) -> int:
    return (await session.execute(select(func.count(Transaction.id)))).scalar_one()


# --------------------------------------------------------------- temel davranış (onaysız)


async def test_tek_parca_mesajda_mevcut_akis_aynen_calisir(session, patch_session_local):
    ahmet = Person(full_name="Ahmet Yılmaz")
    session.add(ahmet)
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = "ahmet yılmaz 1000 tl borç yazdım"
    update = FakeUpdate(FakeMessage(text=text))

    await bot_main.on_text(update, context)

    replies = _reply_texts(update)
    # Tek parça varken "N işlem algılandı" anonsu ASLA gönderilmez.
    assert not any("işlem algılandı" in r for r in replies)
    assert len(replies) == 1

    assert await _tx_count(session) == 1


async def test_iki_temiz_islem_sirayla_islenir_ayri_cevap_verilir(session, patch_session_local):
    ahmet = Person(full_name="Ahmet Yılmaz")
    mehmet = Person(full_name="Mehmet Öztürk")
    session.add_all([ahmet, mehmet])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = "ahmet yılmaz 1000 tl borç yazdım\nmehmet öztürk 500 tl ödedi"
    update = FakeUpdate(FakeMessage(text=text))

    await bot_main.on_text(update, context)

    replies = _reply_texts(update)
    assert replies[0] == "2 işlem algılandı, sırayla işliyorum:"
    assert len(replies) == 3  # anons + 2 ayrı işlem cevabı
    assert "Ahmet Yılmaz" in replies[1]
    assert "Mehmet Öztürk" in replies[2]
    # Hiç onay gerekmediyse kuyruk hiç kurulmaz, "tamamlandı" mesajı da
    # gösterilmez — her parça zaten kendi cevabını aldı, ekstra gürültü yok.
    assert "pending_queue" not in context.chat_data
    assert not any("tamamlandı" in r for r in replies)

    assert await _tx_count(session) == 2

    ahmet_tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))
    ).scalar_one()
    assert ahmet_tx.amount_try == Decimal("1000.00")

    mehmet_tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == mehmet.id))
    ).scalar_one()
    assert mehmet_tx.amount_try == Decimal("500.00")


# --------------------------------------------------------------- bug 1: onaydan sonra kuyruk devam eder


async def test_ortadaki_parca_onay_isterse_kuyruk_kurulur(session, patch_session_local):
    mehmet = Person(full_name="Mehmet Öztürk")
    ahmet_a = Person(full_name="Ahmet Yılmaz")
    ahmet_b = Person(full_name="Ahmet Yıldız")
    furkan = Person(full_name="Furkan Duman")
    session.add_all([mehmet, ahmet_a, ahmet_b, furkan])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = (
        "mehmet öztürk 500 tl borç yazdım\n"
        "ahmet 300 tl ödedi\n"
        "furkan duman 200 tl borç yazdım"
    )
    update = FakeUpdate(FakeMessage(text=text))

    await bot_main.on_text(update, context)

    replies = _reply_texts(update)
    assert replies[0] == "3 işlem algılandı, sırayla işliyorum:"
    assert len(replies) == 3  # anons + mehmet cevabı + ahmet "hangisi?" sorusu

    assert "pending" in context.chat_data
    assert context.chat_data["pending"]["person_name_raw"] == "ahmet"
    assert context.chat_data["pending_queue"]["remaining"] == ["furkan duman 200 tl borç yazdım"]

    assert await _tx_count(session) == 1  # yalnızca mehmet, furkan henüz kaydedilmedi


async def test_hangisi_cevaplaninca_kuyruktaki_sonraki_parca_otomatik_islenir(session, patch_session_local):
    mehmet = Person(full_name="Mehmet Öztürk")
    ahmet_a = Person(full_name="Ahmet Yılmaz")
    ahmet_b = Person(full_name="Ahmet Yıldız")
    furkan = Person(full_name="Furkan Duman")
    session.add_all([mehmet, ahmet_a, ahmet_b, furkan])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = (
        "mehmet öztürk 500 tl borç yazdım\n"
        "ahmet 300 tl ödedi\n"
        "furkan duman 200 tl borç yazdım"
    )
    update = FakeUpdate(FakeMessage(text=text))
    await bot_main.on_text(update, context)
    assert await _tx_count(session) == 1

    # Kullanıcı "hangisi?" sorusuna Ahmet Yılmaz'ı seçiyor.
    query = FakeQuery(data=f"person:pick:{ahmet_a.id}")
    callback_update = FakeUpdate(callback_query=query)
    await bot_main.on_callback(callback_update, context)

    # Ahmet'in kaydı tamamlandı VE kuyruktaki furkan otomatik işlendi.
    assert await _tx_count(session) == 3

    ahmet_tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet_a.id))
    ).scalar_one()
    assert ahmet_tx.amount_try == Decimal("300.00")

    furkan_tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == furkan.id))
    ).scalar_one()
    assert furkan_tx.amount_try == Decimal("200.00")

    # Kuyruk tamamen boşaldı, "Tüm işlemler tamamlandı." denildi.
    assert "pending_queue" not in context.chat_data
    query_replies = [call.args[0] for call in query.message.reply_text.await_args_list]
    assert "Tüm işlemler tamamlandı." in query_replies


async def test_ilk_parca_onay_isterse_de_ikinci_parca_sonradan_islenir(session, patch_session_local):
    ahmet_a = Person(full_name="Ahmet Yılmaz")
    ahmet_b = Person(full_name="Ahmet Yıldız")
    mehmet = Person(full_name="Mehmet Öztürk")
    session.add_all([ahmet_a, ahmet_b, mehmet])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = "ahmet 300 tl ödedi\nmehmet öztürk 500 tl borç yazdım"
    update = FakeUpdate(FakeMessage(text=text))
    await bot_main.on_text(update, context)

    assert await _tx_count(session) == 0
    assert context.chat_data["pending_queue"]["remaining"] == ["mehmet öztürk 500 tl borç yazdım"]

    query = FakeQuery(data=f"person:pick:{ahmet_a.id}")
    callback_update = FakeUpdate(callback_query=query)
    await bot_main.on_callback(callback_update, context)

    assert await _tx_count(session) == 2
    assert "pending_queue" not in context.chat_data


async def test_urun_onerisi_sonrasi_kuyruk_devam_eder(session, patch_session_local):
    ahmet = Person(full_name="Ahmet Yılmaz")
    mehmet = Person(full_name="Mehmet Öztürk")
    saman = Product(name="Saman", base_unit="balya")
    session.add_all([ahmet, mehmet, saman])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = "ahmet yılmaz 20 balya samaan aldı 5000 tl borç\nmehmet öztürk 500 tl ödedi"
    update = FakeUpdate(FakeMessage(text=text))
    await bot_main.on_text(update, context)

    assert "product_confirm" in context.chat_data
    assert context.chat_data["pending_queue"]["remaining"] == ["mehmet öztürk 500 tl ödedi"]
    assert await _tx_count(session) == 0

    query = FakeQuery(data="product:yes")
    callback_update = FakeUpdate(callback_query=query)
    await bot_main.on_callback(callback_update, context)

    assert await _tx_count(session) == 2
    ahmet_tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))
    ).scalar_one()
    assert ahmet_tx.lines[0].product_id == saman.id
    assert "pending_queue" not in context.chat_data


async def test_silme_onayi_sonrasi_kuyruk_devam_eder(session, patch_session_local):
    furkan = Person(full_name="Furkan Duman")
    mehmet = Person(full_name="Mehmet Öztürk")
    session.add_all([furkan, mehmet])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = "furkanı sil\nmehmet öztürk 500 tl borç yazdım"
    update = FakeUpdate(FakeMessage(text=text))
    await bot_main.on_text(update, context)

    assert "archive_confirm" in context.chat_data
    assert context.chat_data["pending_queue"]["remaining"] == ["mehmet öztürk 500 tl borç yazdım"]
    assert await _tx_count(session) == 0

    onay = context.chat_data["archive_confirm"]["onay_kelimesi"]
    confirm_update = FakeUpdate(FakeMessage(text=onay))
    await bot_main.on_text(confirm_update, context)

    await session.refresh(furkan)
    assert furkan.is_active is False
    assert await _tx_count(session) == 1  # mehmet'in borcu otomatik işlendi
    assert "pending_queue" not in context.chat_data


async def test_iptal_de_kuyrugu_ilerletir(session, patch_session_local):
    # Kullanıcı bir onayı REDDETSE bile (Hayır/İptal) o parça "bitmiş"
    # sayılır — kuyruktaki bir sonraki parça yine de işlenir.
    ahmet_a = Person(full_name="Ahmet Yılmaz")
    ahmet_b = Person(full_name="Ahmet Yıldız")
    mehmet = Person(full_name="Mehmet Öztürk")
    session.add_all([ahmet_a, ahmet_b, mehmet])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = "ahmet 300 tl ödedi\nmehmet öztürk 500 tl borç yazdım"
    update = FakeUpdate(FakeMessage(text=text))
    await bot_main.on_text(update, context)

    query = FakeQuery(data="person:no")
    callback_update = FakeUpdate(callback_query=query)
    await bot_main.on_callback(callback_update, context)

    # Ahmet için hiçbir şey kaydedilmedi (reddedildi) AMA mehmet işlendi.
    assert await _tx_count(session) == 1
    mehmet_tx = (
        await session.execute(select(Transaction).where(Transaction.person_id == mehmet.id))
    ).scalar_one()
    assert mehmet_tx.amount_try == Decimal("500.00")
    assert "pending_queue" not in context.chat_data


async def test_zincirleme_iki_onay_gerektiren_parca(session, patch_session_local):
    # 1. parça: "ahmet" -> hangisi? (kuyruk: [2. parça])
    # 2. parça de "ahmet" içeriyor -> hangisi? cevaplanınca kuyruk yine
    #    ilerler, ama BİRİNCİ hangisi cevaplanana kadar kuyruk hiç dokunulmaz.
    ahmet_a = Person(full_name="Ahmet Yılmaz")
    ahmet_b = Person(full_name="Ahmet Yıldız")
    furkan = Person(full_name="Furkan Duman")
    session.add_all([ahmet_a, ahmet_b, furkan])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = "ahmet 300 tl ödedi\nfurkan duman 200 tl borç yazdım"
    update = FakeUpdate(FakeMessage(text=text))
    await bot_main.on_text(update, context)

    assert context.chat_data["pending_queue"]["remaining"] == ["furkan duman 200 tl borç yazdım"]

    query = FakeQuery(data=f"person:pick:{ahmet_a.id}")
    callback_update = FakeUpdate(callback_query=query)
    await bot_main.on_callback(callback_update, context)

    assert await _tx_count(session) == 2
    assert "pending_queue" not in context.chat_data


# --------------------------------------------------------------- bug 3: parse edilemeyen parça diğerlerini durdurmaz


async def test_anlasilmayan_parca_digerlerini_durdurmaz(session, patch_session_local):
    ahmet = Person(full_name="Ahmet Yılmaz")
    mehmet = Person(full_name="Mehmet Öztürk")
    session.add_all([ahmet, mehmet])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = (
        "ahmet yılmaz 1000 tl borç yazdım\n"
        "bu tamamen anlamsiz bir yazi\n"
        "mehmet öztürk 500 tl ödedi"
    )
    update = FakeUpdate(FakeMessage(text=text))

    await bot_main.on_text(update, context)

    replies = _reply_texts(update)
    assert replies[0] == "3 işlem algılandı, sırayla işliyorum:"
    assert len(replies) == 4  # anons + 3 parça cevabı (biri anlaşılmadı mesajı)
    assert any("Şu kısmı anlayamadım" in r and "bu tamamen anlamsiz bir yazi" in r for r in replies)

    # Diğer İKİ işlem yine de kaydedildi — biri anlaşılmadı diye hepsi durmadı.
    assert await _tx_count(session) == 2
    assert "pending_queue" not in context.chat_data


async def test_tek_parcada_anlasilamadi_mesaji_degismez(session, patch_session_local):
    # Tek parçalı (bölünmemiş) bir mesaj anlaşılamazsa mesaj DEĞİŞMEMELİ —
    # yalnızca çoklu işlem bölmesinde parçaya özel mesaj gösterilir.
    context = FakeContext()
    text = "bugün hava çok güzel"
    update = FakeUpdate(FakeMessage(text=text))

    await bot_main.on_text(update, context)

    replies = _reply_texts(update)
    assert len(replies) == 1
    assert "Şu kısmı anlayamadım" not in replies[0]
    assert replies[0] == bot_main.ANLASILAMADI_METNI


# --------------------------------------------------------------- bug 2: onay mesajı başlığı temiz kalmalı


async def test_kayit_onay_basligi_sadece_kisi_adi(session, patch_session_local):
    ahmet = Person(full_name="Ahmet Yılmaz")
    saman = Product(name="Saman", base_unit="balya")
    session.add_all([ahmet, saman])
    await session.flush()
    await session.commit()

    context = FakeContext()
    text = "ahmet yılmaz 20 balya saman aldı 1000 tl borç"
    update = FakeUpdate(FakeMessage(text=text))

    await bot_main.on_text(update, context)

    replies = _reply_texts(update)
    header = replies[0].split("\n")[0]
    assert header == "✅ Ahmet Yılmaz"
