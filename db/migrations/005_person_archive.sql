-- Kisi silme = arsivleme (CLAUDE.md > "Bot kisi silme = arsivleme").
-- Hicbir sey gercekten silinmez: kisi karti + o anki bakiye + TUM
-- islemlerinin snapshot'i buraya yazilir, sonra persons.is_active=false
-- yapilir (satir DB'de kalir, defterde/aramada gorunmez).

CREATE TABLE IF NOT EXISTS archived_persons (
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

CREATE INDEX IF NOT EXISTS idx_archived_persons_original
    ON archived_persons (original_person_id);
