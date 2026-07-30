"""Bot kişi düzenleme akışı (CLAUDE.md > "Silme mesajı + kişi düzenleme —
Grup 4"). NET komut (alan+değer belli, "mehmet ilçe ahmetbeyler yap") bir
Evet/Hayır onayı ister; BELİRSİZ komut ("mehmet düzenle") alan seçim menüsü
sunar, seçilince yeni değeri düz metinle sorar. İkisinde de gerçek
güncelleme person_edit.update_person_field ile yapılır."""

from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import main as bot_main
from app.models import Person, RawMessage
from app.services.intent_resolver import ResolutionStatus, ResolvedIntent


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


@pytest_asyncio.fixture(loop_scope="session")
async def mehmet(session):
    p = Person(full_name="Mehmet Yılmaz")
    session.add(p)
    await session.flush()
    await session.commit()
    return p


# --------------------------------------------------------------- NET komut: Evet/Hayır onayı


async def test_net_komut_evet_ile_gunceller(session, patch_session_local, mehmet):
    resolved = ResolvedIntent(
        status=ResolutionStatus.READY,
        kind="edit_person",
        person=mehmet,
        field_name="district",
        new_value="Ahmetbeyler",
    )
    context = FakeContext()
    query = FakeQuery()

    await bot_main._prompt_edit_confirm(query.edit_message_text, context, resolved)
    assert context.chat_data["edit_confirm"] == {
        "person_id": mehmet.id, "field": "district", "value": "Ahmetbeyler",
    }
    query.edit_message_text.assert_awaited_with(
        "Mehmet Yılmaz'ın ilçesi Ahmetbeyler yapılsın mı?",
        reply_markup=bot_main._edit_confirm_keyboard(),
    )

    await bot_main._handle_edit_confirm_yes(query, context)

    assert "edit_confirm" not in context.chat_data
    query.edit_message_text.assert_awaited_with("Mehmet Yılmaz'ın ilçesi Ahmetbeyler olarak güncellendi.")

    await session.refresh(mehmet)
    assert mehmet.district == "Ahmetbeyler"


async def test_net_komut_hayir_ile_hicbir_sey_degismez(session, patch_session_local, mehmet):
    context = FakeContext()
    context.chat_data["edit_confirm"] = {
        "person_id": mehmet.id, "field": "district", "value": "Ahmetbeyler",
    }
    query = FakeQuery(data="edit:no")
    update = FakeUpdate(callback_query=query)

    await bot_main.on_callback(update, context)

    assert "edit_confirm" not in context.chat_data
    query.edit_message_text.assert_awaited_with("Tamam, değişiklik yapılmadı.")

    await session.refresh(mehmet)
    assert mehmet.district is None


async def test_edit_yes_pending_yoksa_gecerli_degil_mesaji(session, patch_session_local):
    context = FakeContext()
    query = FakeQuery()

    await bot_main._handle_edit_confirm_yes(query, context)

    query.edit_message_text.assert_awaited_with("Bu istek artık geçerli değil.")


async def test_net_komut_olmayan_kisi_bulunamadi_der(session, patch_session_local):
    context = FakeContext()
    query = FakeQuery()
    context.chat_data["edit_confirm"] = {"person_id": 999999, "field": "district", "value": "X"}

    await bot_main._handle_edit_confirm_yes(query, context)

    query.edit_message_text.assert_awaited_with("Kişi bulunamadı.")


# --------------------------------------------------------------- BELİRSİZ komut: alan menüsü + değer


async def test_belirsiz_komut_menu_sonra_deger_ile_gunceller(session, patch_session_local, mehmet):
    resolved = ResolvedIntent(
        status=ResolutionStatus.READY, kind="edit_person", person=mehmet, field_name=None, new_value=None,
    )
    context = FakeContext()
    query = FakeQuery()

    await bot_main._prompt_edit_field_menu(query.edit_message_text, context, resolved)
    assert context.chat_data["edit_field_flow"] == {"person_id": mehmet.id, "person_name": "Mehmet Yılmaz"}
    query.edit_message_text.assert_awaited_with(
        "Hangi bilgiyi düzenlemek istersin?", reply_markup=bot_main._edit_field_keyboard()
    )

    # Mehmet'in henüz telefonu yok — alan seçilince "(boş)" gösterilmeli.
    await bot_main._handle_edit_field_pick(query, context, "phone")
    assert context.chat_data["edit_field_flow"]["field"] == "phone"
    query.edit_message_text.assert_awaited_with("Telefon bilgisi: (boş)\nYeni telefon için yazın:")

    flow = context.chat_data["edit_field_flow"]
    update = FakeUpdate()
    await bot_main._handle_edit_field_value_text(update, context, flow, "05551112233")

    assert "edit_field_flow" not in context.chat_data
    update.message.reply_text.assert_awaited_with(
        "Mehmet Yılmaz'ın telefonu 05551112233 olarak güncellendi."
    )

    await session.refresh(mehmet)
    assert mehmet.phone == "05551112233"


async def test_edit_field_pick_dolu_alanda_eski_degeri_gosterir(session, patch_session_local):
    p = Person(full_name="Ayşe Kaya", district="Bergama")
    session.add(p)
    await session.flush()
    await session.commit()

    context = FakeContext()
    context.chat_data["edit_field_flow"] = {"person_id": p.id, "person_name": "Ayşe Kaya"}
    query = FakeQuery()

    await bot_main._handle_edit_field_pick(query, context, "district")

    query.edit_message_text.assert_awaited_with("İlçe bilgisi: Bergama\nYeni ilçe için yazın:")


async def test_edit_field_pick_bos_alanda_bos_gosterir(session, patch_session_local, mehmet):
    context = FakeContext()
    context.chat_data["edit_field_flow"] = {"person_id": mehmet.id, "person_name": "Mehmet Yılmaz"}
    query = FakeQuery()

    await bot_main._handle_edit_field_pick(query, context, "address")

    query.edit_message_text.assert_awaited_with("Adres bilgisi: (boş)\nYeni adres için yazın:")


async def test_edit_field_pick_kisi_silinmisse_bulunamadi_der(session, patch_session_local):
    context = FakeContext()
    context.chat_data["edit_field_flow"] = {"person_id": 999999, "person_name": "Hayalet"}
    query = FakeQuery()

    await bot_main._handle_edit_field_pick(query, context, "phone")

    query.edit_message_text.assert_awaited_with("Kişi bulunamadı.")
    assert "edit_field_flow" not in context.chat_data


async def test_belirsiz_komut_bos_deger_tekrar_sorar(session, patch_session_local, mehmet):
    context = FakeContext()
    flow = {"person_id": mehmet.id, "person_name": "Mehmet Yılmaz", "field": "city"}
    update = FakeUpdate()

    await bot_main._handle_edit_field_value_text(update, context, flow, "   ")

    update.message.reply_text.assert_awaited_with("Değer boş olamaz, tekrar yazar mısın?")
    await session.refresh(mehmet)
    assert mehmet.city is None


async def test_edit_field_pick_flow_yoksa_gecerli_degil_mesaji(session, patch_session_local):
    context = FakeContext()
    query = FakeQuery()

    await bot_main._handle_edit_field_pick(query, context, "phone")

    query.edit_message_text.assert_awaited_with("Bu istek artık geçerli değil.")


# --------------------------------------------------------------- on_text yönlendirmesi


async def test_on_text_alan_secilmeden_normal_akisa_duser(session, patch_session_local, mehmet):
    # Menü gösterildi ama henüz bir alan seçilmedi ("field" anahtarı yok) —
    # bu sırada gelen düz metin normal mesaj işleme akışına düşmeli, değer
    # olarak yutulmamalı.
    context = FakeContext()
    context.chat_data["edit_field_flow"] = {"person_id": mehmet.id, "person_name": "Mehmet Yılmaz"}

    update = FakeUpdate(FakeMessage(text="mehmet yılmaz borcu ne"))
    await bot_main.on_text(update, context)

    # Akış hâlâ bekliyor olmalı (temizlenmemiş), normal mesaj cevaplanmış olmalı.
    assert context.chat_data.get("edit_field_flow") == {
        "person_id": mehmet.id, "person_name": "Mehmet Yılmaz",
    }
    update.message.reply_text.assert_awaited()


# --------------------------------------------------------------- çoklu aday + edit_person


# --------------------------------------------------------------- büyük/küçük harf düzeltmesi
#
# parser.normalize() tüm metni küçük harfe çevirir ("mehmet il izmir yap"
# -> new_value="izmir") — özel isim sayılan alanlarda (ad soyad/il/ilçe)
# bu ham haliyle kaydedilirse "izmir" gibi çirkin bir görünüm ortaya çıkar.
# _prompt_edit_confirm bunu _title_tr ile düzeltmeli.


async def test_net_komut_il_degeri_buyuk_harfle_gosterilir_ve_kaydedilir(session, patch_session_local, mehmet):
    from app.services import message_processor

    text = "mehmet yılmaz il izmir yap"
    raw = RawMessage(channel="telegram", external_id="edit-tc", chat_id="1", payload={"text": text})
    session.add(raw)
    await session.flush()
    result = await message_processor.process_raw_message(session, raw, text)
    assert result.outcome == message_processor.ProcessOutcome.EDIT_PERSON_CONFIRM
    assert result.resolved.new_value == "izmir"  # parser çıktısı ham, küçük harf

    context = FakeContext()
    query = FakeQuery()
    await bot_main._prompt_edit_confirm(query.edit_message_text, context, result.resolved)

    assert context.chat_data["edit_confirm"]["value"] == "İzmir"
    query.edit_message_text.assert_awaited_with(
        "Mehmet Yılmaz'ın ili İzmir yapılsın mı?", reply_markup=bot_main._edit_confirm_keyboard()
    )

    await bot_main._handle_edit_confirm_yes(query, context)
    await session.refresh(mehmet)
    assert mehmet.city == "İzmir"


async def test_coklu_aday_secilince_net_komut_onayi_gosterilir(session, patch_session_local):
    a = Person(full_name="Furkan Duman")
    b = Person(full_name="Furkan Yılmaz")
    session.add_all([a, b])
    await session.flush()
    raw = RawMessage(
        channel="telegram", external_id="edit-1", chat_id="1",
        payload={"text": "furkan ilçe bergama yap"},
    )
    session.add(raw)
    await session.flush()
    await session.commit()

    pending = {
        "kind": "edit_person",
        "person_name_raw": "furkan",
        "qty": None,
        "unit": None,
        "product_name": None,
        "amount": None,
        "raw_message_id": raw.id,
        "raw_text": "furkan ilçe bergama yap",
        "field": "district",
        "new_value": "bergama",
    }
    context = FakeContext()
    query = FakeQuery()
    context.chat_data["pending"] = pending

    await bot_main._handle_person_pick(query, context, a.id)

    assert "edit_confirm" in context.chat_data
    confirm = context.chat_data["edit_confirm"]
    assert confirm == {"person_id": a.id, "field": "district", "value": "Bergama"}
    query.edit_message_text.assert_awaited()

    await session.refresh(a)
    assert a.district is None
