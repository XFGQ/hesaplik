-- Web sohbet asistani (CLAUDE.md > "Web'e chat asistani ekle").
--
-- pending_requests'e web'in tek raw_messages satirini isaretlemek icin
-- raw_message_id eklenir (Telegram bu kolonu hic kullanmaz, kuyrugu hala
-- bellekte tutuyor). web_chat_pending, botun context.chat_data'daki "bir
-- soruya cevap bekliyorum" durumunun web (durumsuz HTTP) karsiligidir.

ALTER TABLE pending_requests
    ADD COLUMN IF NOT EXISTS raw_message_id BIGINT REFERENCES raw_messages(id);

CREATE TABLE IF NOT EXISTS web_chat_pending (
    chat_id    TEXT        PRIMARY KEY,
    kind       TEXT,
    payload    JSONB,
    undo       JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
