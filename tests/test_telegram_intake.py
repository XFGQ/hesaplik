from sqlalchemy import select

from app.models import RawMessage
from app.services import telegram_intake


def _text_update(update_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1234567890,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


async def test_save_raw_message_idempotent(session):
    update = _text_update(1001, 555, "merhaba")

    first = await telegram_intake.save_raw_message(session, update)
    await session.flush()
    second = await telegram_intake.save_raw_message(session, update)

    assert first.id == second.id

    rows = (
        await session.execute(select(RawMessage).where(RawMessage.external_id == "1001"))
    ).scalars().all()
    assert len(rows) == 1


async def test_save_raw_message_kaydeder(session):
    update = _text_update(2002, 777, "20 balya saman borç")

    raw = await telegram_intake.save_raw_message(session, update)

    assert raw.channel == "telegram"
    assert raw.external_id == "2002"
    assert raw.chat_id == "777"
    assert raw.payload["message"]["text"] == "20 balya saman borç"


async def test_farkli_update_id_ayri_kayit(session):
    await telegram_intake.save_raw_message(session, _text_update(3001, 1, "a"))
    await telegram_intake.save_raw_message(session, _text_update(3002, 1, "b"))

    rows = (
        await session.execute(
            select(RawMessage).where(RawMessage.external_id.in_(["3001", "3002"]))
        )
    ).scalars().all()
    assert len(rows) == 2
