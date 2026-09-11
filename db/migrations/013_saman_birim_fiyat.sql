-- Varsayilan saman balya fiyati (CLAUDE.md > "Varsayilan saman fiyati").
-- Saman tutari yazilmadiginda tutar = adet x bu fiyat. Kullanici sol
-- paneldeki "Guncel saman fiyati" kartindan degistirir (audit_log'a yazilir).
-- Idempotent: fiyat daha once ayarlandiysa DOKUNULMAZ.

INSERT INTO settings (key, value) VALUES ('saman_birim_fiyat', '180.00')
    ON CONFLICT (key) DO NOTHING;
