"""Web sohbet asistanı (/api/chat/*) — Telegram botunun web karşılığı.

Aynı beyni (app/services/message_processor.py) kullanır; yalnızca
giriş/çıkış farklı (JSON, InlineKeyboard yerine buton listesi). Bkz.
app/services/web_chat.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import ChatConfirmIn, ChatIn, ChatResponse, ChatVoiceResponse
from app.services import auth, web_chat

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Tarayıcı MediaRecorder'ının üretebildiği ve Groq'un kabul ettiği biçimler.
# Uzantı allowlist'i: dosya adı istemciden gelir, olduğu gibi Groq'a
# geçirilmez — tanımadığımız uzantı sessizce webm sayılır.
VOICE_EXTENSIONS = {"webm", "ogg", "opus", "mp4", "m4a", "mp3", "wav", "flac"}
VOICE_DEFAULT_EXTENSION = "webm"
# ~2 dakikalık opus kaydı 1 MB'ın altındadır; sınır kötü niyetli/kazara
# yüklemeye karşı, normal kullanımda hiç görülmez.
VOICE_MAX_BYTES = 10 * 1024 * 1024


def _voice_filename(upload: UploadFile) -> str:
    """Groq biçimi DOSYA ADININ uzantısından anlar (bkz. app/services/stt.py).
    İstemcinin gönderdiği addan yalnızca uzantı alınır, gerisi atılır — yol
    ayracı/uzun ad gibi sürprizler Groq'a gitmez."""
    name = (upload.filename or "").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in VOICE_EXTENSIONS:
        ext = VOICE_DEFAULT_EXTENSION
    return f"voice.{ext}"


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


@router.post("/cancel", response_model=ChatResponse)
async def chat_cancel(
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(auth.require_auth),
):
    """Durdurma (⏹) butonu — bekleyen soruyu ve kuyrukta kalanları iptal eder.
    Gövde almaz: iptal edilecek şey zaten oturumun (actor) kendi durumudur."""
    return await web_chat.handle_cancel(session, actor)


@router.post("/voice", response_model=ChatVoiceResponse)
async def chat_voice(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(auth.require_auth),
):
    """Mikrofon kaydı → Groq whisper → METİN → yazılı mesajla AYNI akış.
    Telegram'daki sesli mesajın web karşılığı; ayrı bir parse mantığı yok.

    STT kapalıysa ya da ses anlaşılamazsa 200 + açıklayıcı bir sohbet mesajı
    döner (hata kodu değil): kullanıcı için bu bir arıza değil, "anlayamadım,
    yazar mısın?" cevabıdır ve arayüzde tek bir yol vardır."""
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=422, detail="Ses kaydı boş geldi")
    if len(audio) > VOICE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Ses kaydı çok uzun, kısa tutar mısın?")
    return await web_chat.handle_voice(session, actor, audio, _voice_filename(file))
