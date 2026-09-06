"""Groq Whisper STT (Faz 5 — sesli komut).

Telegram sesli mesajı (.ogg) doğrudan Groq'un OpenAI-uyumlu
/audio/transcriptions ucuna gönderilir (dönüşüm gerekmez, CLAUDE.md >
"Telegram sesli mesajları için speech-to-text ekle"). Çıktı Türkçe metne
çevrilir ve MEVCUT message_processor akışına (parser -> LLM fallback ->
intent_resolver) sanki kullanıcı yazmış gibi verilir — STT yalnızca metne
çeviren bir ön adımdır, kendi parse mantığı yoktur.

Aynı sağlayıcı web sohbetindeki mikrofon kaydı için de kullanılır
(POST /api/chat/voice — bkz. app/services/web_chat.py > handle_voice).
Oradaki ses tarayıcının MediaRecorder'ından gelir: Chrome/Firefox webm/opus,
iOS Safari mp4/aac üretir. Groq her ikisini de kabul eder, ayırt etmek için
DOSYA ADININ UZANTISI yeter — bu yüzden `filename` çağıran tarafından
gerçek biçime göre verilir, içerik tipi de uzantıdan türetilir.

Groq erişilemezse/429 ise ya da yanıt beklenen şemada değilse `transcribe`
None döner, sistemi ÇÖKERTMEZ — bot/web kullanıcıya yazarak göndermesini
ister (bkz. app/bot/main.py > on_voice, app/services/web_chat.py >
handle_voice).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Türkçe ipucu: Whisper dil algılamayı atlar, doğruluk artar.
LANGUAGE = "tr"

# Groq'un kabul ettiği biçimlerden bizim ürettiklerimiz. Sunucu asıl olarak
# dosya adının UZANTISINA bakar; içerik tipi yine de doğru gönderilir ki
# vekil/proxy katmanları da doğru yorumlasın. Bilinmeyen uzantı olduğu gibi
# gönderilmez — çağıran taraf zaten allowlist uygular (bkz. app/api/chat.py).
CONTENT_TYPES = {
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "webm": "audio/webm",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
}
DEFAULT_CONTENT_TYPE = "audio/ogg"


def content_type_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return CONTENT_TYPES.get(ext, DEFAULT_CONTENT_TYPE)


class GroqSTTProvider:
    """`client` parametresi yalnızca testler içindir (httpx.MockTransport
    ile gerçek ağa çıkmadan mock'lamak için); normal kullanımda boş
    bırakılır, her çağrıda kısa ömürlü bir AsyncClient açılır."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str | None:
        url = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = {"model": self.model, "language": LANGUAGE}
        files = {"file": (filename, audio, content_type_for(filename))}
        try:
            if self._client is not None:
                resp = await self._client.post(url, headers=headers, data=data, files=files)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, headers=headers, data=data, files=files)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Groq STT'ye erişilemedi ya da geçersiz yanıt: %s", e)
            return None

        text = body.get("text")
        if not isinstance(text, str):
            return None
        text = text.strip()
        return text or None


def get_stt_provider() -> GroqSTTProvider | None:
    """api_key yapılandırılmamışsa (varsayılan) None döner — çağıran taraf
    bunu "sesli mesaj desteği kapalı" olarak yorumlar."""
    from app.config import settings  # döngüsel import olmasın diye gecikmeli

    if not settings.groq_api_key:
        return None
    return GroqSTTProvider(
        settings.groq_stt_url,
        settings.groq_api_key,
        settings.groq_stt_model,
        timeout=settings.groq_stt_timeout,
    )
