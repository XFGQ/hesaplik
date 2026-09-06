-- Web sohbetinde mikrofonla girilen kayitlar (Faz 5'in web ayagi): tarayici
-- MediaRecorder -> POST /api/chat/voice -> Groq whisper -> AYNI metin akisi.
-- Kaynak izi Telegram sesinden ayrilir ki "bunu kim, nasil soyledi" sorusu
-- defterde yanit bulsun (raw_messages.channel = 'web_voice').
--
-- ALTER TYPE ... ADD VALUE PostgreSQL 12+'da transaction icinde calisir,
-- ama ayni islemde KULLANILAMAZ; bu yuzden tek basina bir migration.

ALTER TYPE tx_source ADD VALUE IF NOT EXISTS 'WEB_VOICE' AFTER 'WEB';
