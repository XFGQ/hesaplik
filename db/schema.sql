-- Hesaplık — kanonik şema
-- Temel ilke: transactions APPEND-ONLY. Düzeltme UPDATE değil, ters kayıttır.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TYPE tx_kind   AS ENUM ('DEBIT', 'CREDIT');
CREATE TYPE tx_status AS ENUM ('PENDING', 'CONFIRMED', 'REJECTED');
CREATE TYPE tx_source AS ENUM ('WEB', 'TELEGRAM_TEXT', 'TELEGRAM_VOICE', 'SYSTEM');

-- ---------------------------------------------------------------- kişiler

CREATE TABLE persons (
    id         BIGSERIAL PRIMARY KEY,
    full_name  TEXT        NOT NULL,
    phone      TEXT,
    city       TEXT,                          -- il
    district   TEXT,                          -- ilce
    address    TEXT,
    note       TEXT,
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_persons_name_trgm ON persons USING gin (full_name gin_trgm_ops);

CREATE TABLE person_aliases (
    id        BIGSERIAL PRIMARY KEY,
    person_id BIGINT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    alias     TEXT   NOT NULL
);

CREATE UNIQUE INDEX uq_person_alias ON person_aliases (lower(alias));
CREATE INDEX idx_person_alias_trgm ON person_aliases USING gin (alias gin_trgm_ops);

-- ---------------------------------------------------------------- ürünler

CREATE TABLE products (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT        NOT NULL UNIQUE,
    base_unit  TEXT        NOT NULL,          -- 'adet', 'kg', 'balya', 'ton'
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE product_aliases (
    id         BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    alias      TEXT   NOT NULL
);

CREATE UNIQUE INDEX uq_product_alias ON product_aliases (lower(alias));
CREATE INDEX idx_product_alias_trgm ON product_aliases USING gin (alias gin_trgm_ops);

CREATE TABLE price_history (
    id         BIGSERIAL PRIMARY KEY,
    product_id BIGINT        NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    unit_price NUMERIC(14,2) NOT NULL CHECK (unit_price >= 0),
    valid_from DATE          NOT NULL,
    created_at TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_price_from UNIQUE (product_id, valid_from)
);

-- ---------------------------------------------------------------- defter

CREATE TABLE transactions (
    id             BIGSERIAL PRIMARY KEY,
    person_id      BIGINT        NOT NULL REFERENCES persons(id),
    kind           tx_kind       NOT NULL,
    occurred_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    amount_try     NUMERIC(14,2) NOT NULL CHECK (amount_try > 0),
    note           TEXT,
    source         tx_source     NOT NULL DEFAULT 'WEB',
    raw_text       TEXT,
    llm_confidence NUMERIC(4,3)  CHECK (llm_confidence BETWEEN 0 AND 1),
    engine         TEXT,
    status         tx_status     NOT NULL DEFAULT 'CONFIRMED',
    reverses_id    BIGINT        REFERENCES transactions(id),
    trace_id       TEXT,
    created_by     TEXT          NOT NULL,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT chk_no_self_reverse CHECK (reverses_id IS NULL OR reverses_id <> id)
);

-- Bir kayit yalnizca bir kez ters kaydedilebilir
CREATE UNIQUE INDEX uq_tx_reverses ON transactions (reverses_id) WHERE reverses_id IS NOT NULL;
CREATE INDEX idx_tx_person_status ON transactions (person_id, status);
CREATE INDEX idx_tx_occurred ON transactions (occurred_at DESC);
CREATE INDEX idx_tx_created ON transactions (created_at DESC);

CREATE TABLE transaction_lines (
    id             BIGSERIAL PRIMARY KEY,
    transaction_id BIGINT        NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    product_id     BIGINT        NOT NULL REFERENCES products(id),
    qty            NUMERIC(14,3) NOT NULL CHECK (qty > 0),
    unit           TEXT          NOT NULL,
    unit_price     NUMERIC(14,2) NOT NULL CHECK (unit_price >= 0),
    line_total     NUMERIC(14,2) NOT NULL CHECK (line_total >= 0)
);

CREATE INDEX idx_lines_tx ON transaction_lines (transaction_id);

-- ---------------------------------------------------------------- append-only zorlama

CREATE OR REPLACE FUNCTION tx_append_only() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF current_setting('app.archiving', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'transactions append-only: DELETE yasak (id=%)', OLD.id;
    END IF;
    -- Tek izin verilen gecis: PENDING -> CONFIRMED | REJECTED
    IF OLD.status = 'PENDING' AND NEW.status IN ('CONFIRMED', 'REJECTED')
       AND NEW.person_id  IS NOT DISTINCT FROM OLD.person_id
       AND NEW.kind       IS NOT DISTINCT FROM OLD.kind
       AND NEW.amount_try IS NOT DISTINCT FROM OLD.amount_try THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'transactions append-only: duzeltme icin ters kayit acin (id=%)', OLD.id;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tx_append_only
    BEFORE UPDATE OR DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION tx_append_only();

-- ---------------------------------------------------------------- arsiv (silme = arsive tasi)
-- "Sil" kaydi yok etmez: kaydi + kalemlerini buraya kopyalar, sonra canli
-- transactions'tan gercekten siler (SET LOCAL app.archiving='on' ile).

CREATE TABLE archived_transactions (
    id             BIGINT        PRIMARY KEY,
    person_id      BIGINT        NOT NULL REFERENCES persons(id),
    kind           tx_kind       NOT NULL,
    occurred_at    TIMESTAMPTZ   NOT NULL,
    amount_try     NUMERIC(14,2) NOT NULL,
    note           TEXT,
    source         tx_source     NOT NULL,
    raw_text       TEXT,
    llm_confidence NUMERIC(4,3),
    engine         TEXT,
    status         tx_status     NOT NULL,
    reverses_id    BIGINT,
    trace_id       TEXT,
    created_by     TEXT          NOT NULL,
    created_at     TIMESTAMPTZ   NOT NULL,
    lines_json     JSONB         NOT NULL,
    archived_by    TEXT          NOT NULL,
    archived_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    archive_reason TEXT
);

CREATE INDEX idx_archived_tx_person ON archived_transactions (person_id);

-- ---------------------------------------------------------------- kisi arsivi (silme = arsivle)
-- "Furkanı sil" kişiyi yok etmez: kişi kartı + o anki bakiye + TÜM işlemlerinin
-- snapshot'ı buraya yazılır, sonra persons.is_active=false yapılır (satır DB'de
-- kalır, defterde/aramada görünmez). transactions'a dokunulmaz.

CREATE TABLE archived_persons (
    id                    BIGSERIAL     PRIMARY KEY,
    original_person_id    BIGINT        NOT NULL REFERENCES persons(id),
    full_name             TEXT          NOT NULL,
    phone                 TEXT,
    city                  TEXT,
    district              TEXT,
    person_created_at     TIMESTAMPTZ   NOT NULL,
    balance_try           NUMERIC(14,2) NOT NULL,
    transactions_snapshot JSONB         NOT NULL,
    archived_by           TEXT          NOT NULL,
    archived_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    archive_reason        TEXT
);

CREATE INDEX idx_archived_persons_original ON archived_persons (original_person_id);

-- ---------------------------------------------------------------- ham mesajlar (dokunulmaz)
-- Ham metin hicbir zaman degismez. detected_*/parse_*/outcome_* alanlari
-- (Faz 7, admin paneli "Islem Akisi") mesaj islenirken YAN ETKI olarak
-- doldurulur: musteri ne yazdi -> sistem ne algiladi -> ne yapti. Hepsi
-- nullable; yazilamamalari defteri etkilemez.

CREATE TABLE raw_messages (
    id             BIGSERIAL PRIMARY KEY,
    channel        TEXT        NOT NULL,      -- 'telegram', 'web'
    external_id    TEXT,                      -- telegram update_id (idempotency)
    chat_id        TEXT,
    payload        JSONB       NOT NULL,
    trace_id       TEXT,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at   TIMESTAMPTZ,
    -- ON DELETE SET NULL: kayit arsive tasininca (ledger.archive_transaction)
    -- ham mesaj SILINMEZ, yalnizca baglantisi kopar (bkz. 011 migration).
    transaction_id BIGINT REFERENCES transactions(id) ON DELETE SET NULL,
    voice_transcript TEXT,             -- sesli mesajin Groq'la cevrilmis metni (Faz 5)

    -- izleme (admin paneli): sistem ne algiladi
    detected_kind    TEXT,                    -- debt/payment/query/edit/archive/none
    detected_person  TEXT,
    detected_amount  NUMERIC(14,2),
    detected_product TEXT,
    detected_qty     NUMERIC(14,2),
    detected_unit    TEXT,
    parse_source     TEXT,                    -- 'regex' | 'llm' | 'none'
    parse_ms         INTEGER,                 -- parse suresi (ms)
    outcome          TEXT,                    -- kaydedildi/yanitlandi/soru_soruldu/hata/yok_sayildi
    outcome_detail   TEXT
);

CREATE UNIQUE INDEX uq_raw_external ON raw_messages (channel, external_id)
    WHERE external_id IS NOT NULL;
CREATE INDEX idx_raw_received ON raw_messages (received_at DESC);
CREATE INDEX idx_raw_detected_kind ON raw_messages (detected_kind);

CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    actor      TEXT        NOT NULL,
    action     TEXT        NOT NULL,
    entity     TEXT        NOT NULL,
    entity_id  TEXT,
    before     JSONB,
    after      JSONB,
    trace_id   TEXT,
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_at ON audit_log (at DESC);

-- ---------------------------------------------------------------- ayarlar

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO settings (key, value) VALUES ('business_name', 'Hesaplık');

-- ------------------------------------------------------------ istek kuyrugu
-- Tek mesajdan cikan islemler bellekte degil burada durur: bot soru sorup
-- beklese, internet kopsa, bot yeniden baslasa bile istek kaybolmaz.
-- Ayni mesajdan gelenler ayni batch_id'yi paylasir, sira_no ile sirali islenir.

CREATE TABLE pending_requests (
    id             BIGSERIAL   PRIMARY KEY,
    chat_id        TEXT        NOT NULL,
    batch_id       TEXT        NOT NULL,
    raw_text       TEXT        NOT NULL,
    sira_no        INTEGER     NOT NULL,
    durum          TEXT        NOT NULL DEFAULT 'beklemede'
        CHECK (durum IN ('beklemede', 'isleniyor', 'tamamlandi', 'basarisiz', 'iptal')),
    sonuc          TEXT,
    hata           TEXT,
    -- Web sohbeti: bu batch'i doguran tek raw_messages satiri (Telegram
    -- botu kullanmaz, kuyrugu hala bellekte).
    raw_message_id BIGINT      REFERENCES raw_messages(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_pending_chat_durum ON pending_requests (chat_id, durum);
CREATE UNIQUE INDEX uq_pending_batch_sira ON pending_requests (batch_id, sira_no);

-- ------------------------------------------------------------ web sohbet durumu
-- Telegram botu "bir soruya cevap bekliyorum" durumunu bellekte
-- (context.chat_data) tutar. Web HTTP istekleri arasi durumsuz oldugundan
-- ayni durum burada saklanir (bkz. app/services/web_chat_state.py). `kind`
-- ayni anda YALNIZCA biri aktif olur; `undo` bundan bagimsiz ayri bir
-- penceredir.

CREATE TABLE web_chat_pending (
    chat_id    TEXT        PRIMARY KEY,
    kind       TEXT,
    payload    JSONB,
    undo       JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------- geri yukleme istegi
-- "Yol A": panel ISTER, host UYGULAR. API container'i DB'yi geri yukleyemez
-- (docker/compose yok, depo salt okunur); istek buraya yazilir, host'taki
-- izleyici (scripts/restore-apply.sh) once guvenlik yedegi alip sonra
-- pg_restore ile yukler ve durumu buradan gunceller.

CREATE TABLE restore_requests (
    id                  BIGSERIAL   PRIMARY KEY,
    snapshot_id         TEXT        NOT NULL,   -- restic short_id
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    requested_by        TEXT        NOT NULL,   -- 'admin-panel@<ip>'
    status              TEXT        NOT NULL DEFAULT 'bekliyor'
        CHECK (status IN ('bekliyor', 'yedekleniyor', 'yukleniyor', 'tamamlandi', 'hata')),
    pre_backup_snapshot TEXT,                   -- restore oncesi guvenlik yedegi
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    error_detail        TEXT
);

CREATE INDEX idx_restore_requested ON restore_requests (requested_at DESC);

-- Tek seferde tek aktif restore (uygulama katmani da kontrol eder, asil
-- garanti burada: es zamanli iki istek veritabaninda reddedilir).
CREATE UNIQUE INDEX uq_restore_tek_aktif
    ON restore_requests ((true))
    WHERE status IN ('bekliyor', 'yedekleniyor', 'yukleniyor');

-- ---------------------------------------------------------------- görünümler
-- Bakiye > 0  => kisi bize borclu (bizim alacagimiz)
-- Ters kayit karsit kind ile eklendigi icin toplamda kendiliginden sifirlanir.

CREATE VIEW v_person_balance AS
SELECT p.id                AS person_id,
       p.full_name,
       COALESCE(SUM(CASE t.kind WHEN 'DEBIT' THEN t.amount_try ELSE -t.amount_try END), 0)::NUMERIC(14,2) AS balance_try,
       MAX(t.occurred_at)  AS last_activity
FROM persons p
LEFT JOIN transactions t
       ON t.person_id = p.id AND t.status = 'CONFIRMED'
GROUP BY p.id, p.full_name;

CREATE VIEW v_person_open_items AS
SELECT t.person_id,
       l.product_id,
       pr.name AS product_name,
       l.unit,
       SUM(CASE t.kind WHEN 'DEBIT' THEN l.qty ELSE -l.qty END)::NUMERIC(14,3) AS qty
FROM transactions t
JOIN transaction_lines l ON l.transaction_id = t.id
JOIN products pr         ON pr.id = l.product_id
WHERE t.status = 'CONFIRMED'
GROUP BY t.person_id, l.product_id, pr.name, l.unit
HAVING SUM(CASE t.kind WHEN 'DEBIT' THEN l.qty ELSE -l.qty END) <> 0;
