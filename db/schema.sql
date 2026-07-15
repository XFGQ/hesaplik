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

-- ---------------------------------------------------------------- ham mesajlar (dokunulmaz)

CREATE TABLE raw_messages (
    id             BIGSERIAL PRIMARY KEY,
    channel        TEXT        NOT NULL,      -- 'telegram', 'web'
    external_id    TEXT,                      -- telegram update_id (idempotency)
    chat_id        TEXT,
    payload        JSONB       NOT NULL,
    trace_id       TEXT,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at   TIMESTAMPTZ,
    transaction_id BIGINT REFERENCES transactions(id)
);

CREATE UNIQUE INDEX uq_raw_external ON raw_messages (channel, external_id)
    WHERE external_id IS NOT NULL;

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
