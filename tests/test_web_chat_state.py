from app.services import web_chat_state

CHAT = "furkan"


async def test_load_satir_yoksa_bos_olusturur(session):
    row = await web_chat_state.load(session, CHAT)
    assert row.chat_id == CHAT
    assert row.kind is None
    assert row.payload is None
    assert row.undo is None


async def test_set_pending_sonra_load_ayni_degeri_doner(session):
    await web_chat_state.set_pending(session, CHAT, "pending", {"person_name_raw": "ahmet"})
    row = await web_chat_state.load(session, CHAT)
    assert row.kind == "pending"
    assert row.payload == {"person_name_raw": "ahmet"}


async def test_clear_pending_undo_alanina_dokunmaz(session):
    await web_chat_state.set_undo(session, CHAT, 5, "2026-01-01T00:00:00+00:00")
    await web_chat_state.set_pending(session, CHAT, "pending", {"a": 1})

    await web_chat_state.clear_pending(session, CHAT)

    row = await web_chat_state.load(session, CHAT)
    assert row.kind is None
    assert row.payload is None
    assert row.undo == {"tx_id": 5, "expires_at": "2026-01-01T00:00:00+00:00"}


async def test_clear_undo_pending_alanina_dokunmaz(session):
    await web_chat_state.set_pending(session, CHAT, "pending", {"a": 1})
    await web_chat_state.set_undo(session, CHAT, 5, "2026-01-01T00:00:00+00:00")

    await web_chat_state.clear_undo(session, CHAT)

    row = await web_chat_state.load(session, CHAT)
    assert row.undo is None
    assert row.kind == "pending"


async def test_iki_farkli_chat_id_birbirini_etkilemez(session):
    await web_chat_state.set_pending(session, "biri", "pending", {"x": 1})
    await web_chat_state.set_pending(session, "digeri", "llm_confirm", {"y": 2})

    biri = await web_chat_state.load(session, "biri")
    digeri = await web_chat_state.load(session, "digeri")
    assert biri.kind == "pending"
    assert digeri.kind == "llm_confirm"
