-- Coklu istek kuyrugu (CLAUDE.md > "Coklu istek — kalici istek kuyrugu").
-- Tek mesajdan cikan islemler bellekte (chat_data) degil burada durur ki bot
-- soru sorup beklese, internet kopsa, bot yeniden baslasa bile kaldigi yerden
-- devam edilebilsin. Ayni mesajdan gelen istekler ayni batch_id'yi paylasir,
-- sira_no ile sirali islenir.

CREATE TABLE IF NOT EXISTS pending_requests (
    id         BIGSERIAL   PRIMARY KEY,
    chat_id    TEXT        NOT NULL,
    batch_id   TEXT        NOT NULL,
    raw_text   TEXT        NOT NULL,
    sira_no    INTEGER     NOT NULL,
    durum      TEXT        NOT NULL DEFAULT 'beklemede'
        CHECK (durum IN ('beklemede', 'isleniyor', 'tamamlandi', 'basarisiz', 'iptal')),
    sonuc      TEXT,
    hata       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pending_chat_durum ON pending_requests (chat_id, durum);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_batch_sira
    ON pending_requests (batch_id, sira_no);
