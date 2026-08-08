-- Admin paneli "Islem Akisi" icin izleme alanlari (Faz 7).
--
-- raw_messages simdiye kadar YALNIZCA ham metni tutuyordu: musteri ne yazdi
-- biliniyordu ama SISTEM NE ALGILADI bilinmiyordu. Islem akisi bolumunun
-- ("ham metin -> algilanan -> sonuc") calisabilmesi icin parse ciktisi ve
-- islem sonucu da kaydedilir.
--
-- Hepsi NULLABLE: mevcut satirlar bozulmaz, eski kayitlar bu alanlar bos
-- olarak gorunur. Yazma islemi tamamen YAN ETKI — bu alanlarin yazilamamasi
-- defteri etkilemez (bkz. app/services/message_trace.py).

ALTER TABLE raw_messages
    ADD COLUMN IF NOT EXISTS detected_kind    TEXT,           -- debt/payment/query/edit/archive/none
    ADD COLUMN IF NOT EXISTS detected_person  TEXT,
    ADD COLUMN IF NOT EXISTS detected_amount  NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS detected_product TEXT,
    ADD COLUMN IF NOT EXISTS detected_qty     NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS detected_unit    TEXT,
    ADD COLUMN IF NOT EXISTS parse_source     TEXT,           -- 'regex' | 'llm' | 'none'
    ADD COLUMN IF NOT EXISTS parse_ms         INTEGER,        -- parse suresi (ms)
    ADD COLUMN IF NOT EXISTS outcome          TEXT,           -- kaydedildi/yanitlandi/soru_soruldu/hata/yok_sayildi
    ADD COLUMN IF NOT EXISTS outcome_detail   TEXT;

-- Islem akisi zaman sirali listelenir ve tur/sonuc ile filtrelenir.
CREATE INDEX IF NOT EXISTS idx_raw_received ON raw_messages (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_detected_kind ON raw_messages (detected_kind);
