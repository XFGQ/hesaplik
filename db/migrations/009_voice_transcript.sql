-- Sesli komut (Faz 5): Telegram sesli mesaji Groq whisper-large-v3 ile
-- Turkce metne cevrilir. raw_messages.payload ASLA degismez (append-only
-- ham kayit), bu yuzden ceviri ayri, nullable bir kolonda tutulur --
-- admin panelindeki "Islem Akisi"/"LLM Izleme" bu kolonu payload_text'e
-- tercihen gosterir (bkz. app/services/message_trace.py > display_text).

ALTER TABLE raw_messages
    ADD COLUMN IF NOT EXISTS voice_transcript TEXT;
