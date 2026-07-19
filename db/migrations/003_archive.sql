-- Silme = arsive tasi. Canli transactions'tan DELETE hala yasak, yalnizca
-- "arsivle-ve-sil" servis fonksiyonu SET LOCAL app.archiving='on' ile izin alir.

CREATE TABLE IF NOT EXISTS archived_transactions (
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

CREATE INDEX IF NOT EXISTS idx_archived_tx_person ON archived_transactions (person_id);

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
