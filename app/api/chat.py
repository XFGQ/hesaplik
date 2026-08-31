"""Web sohbet asistanı (/api/chat/*) — Telegram botunun web karşılığı.

Aynı beyni (app/services/message_processor.py) kullanır; yalnızca
giriş/çıkış farklı (JSON, InlineKeyboard yerine buton listesi). Bkz.
app/services/web_chat.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import ChatConfirmIn, ChatIn, ChatResponse
from app.services import auth, web_chat

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatIn,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(auth.require_auth),
):
    return await web_chat.handle_text(session, actor, body.text)


@router.post("/confirm", response_model=ChatResponse)
async def chat_confirm(
    body: ChatConfirmIn,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(auth.require_auth),
):
    return await web_chat.handle_action(session, actor, body.action)
