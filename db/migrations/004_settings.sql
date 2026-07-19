-- Isletme ayarlari: basit anahtar-deger tablosu. Sol paneldeki isletme adi
-- burada saklanir, her cihazda ayni gorunur, yenileyince kaybolmaz.

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO settings (key, value) VALUES ('business_name', 'Hesaplık')
    ON CONFLICT (key) DO NOTHING;
