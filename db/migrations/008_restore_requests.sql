-- Geri yukleme (restore) istek kuyrugu — "Yol A": panel ISTER, host UYGULAR.
--
-- API container'i veritabanini geri YUKLEYEMEZ: icinde docker/compose yok ve
-- restic deposu salt okunur bagli (bkz. app/services/backup.py). Bu yuzden
-- panel yalnizca BURAYA bir istek yazar; host'ta calisan izleyici
-- (scripts/restore-apply.sh, systemd ile) kuyrugu okur, once guvenlik yedegi
-- alir, sonra pg_restore ile yukler ve durumu buradan gunceller.
--
-- Neden tablo: istek kalici olmali. Panel sekmesi kapansa, API yeniden
-- baslasa, host o an mesgul olsa bile istek kaybolmaz ve durumu izlenebilir.

CREATE TABLE IF NOT EXISTS restore_requests (
    id                  BIGSERIAL   PRIMARY KEY,
    snapshot_id         TEXT        NOT NULL,   -- restic short_id (ana yapilacak yedek)
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    requested_by        TEXT        NOT NULL,   -- 'admin-panel@<ip>'
    status              TEXT        NOT NULL DEFAULT 'bekliyor'
        CHECK (status IN ('bekliyor', 'yedekleniyor', 'yukleniyor', 'tamamlandi', 'hata')),
    -- Geri yuklemeden ONCE alinan guvenlik yedeginin restic kimligi. Host
    -- izleyici doldurur; kullanici "eski hale don" derse bu snapshot aranir.
    pre_backup_snapshot TEXT,
    started_at          TIMESTAMPTZ,            -- host isi eline aldiginda
    finished_at         TIMESTAMPTZ,            -- tamamlandi/hata aninda
    error_detail        TEXT                    -- hata durumunda net sebep
);

-- Panel "son istek ne oldu" diye sorar, en yeni ustte.
CREATE INDEX IF NOT EXISTS idx_restore_requested ON restore_requests (requested_at DESC);

-- TEK SEFERDE TEK AKTIF RESTORE. Uygulama katmani da kontrol eder (kullaniciya
-- anlasilir 409 doner) ama asil garanti burada: iki yonetici ayni anda
-- tikladiginda ikinci INSERT veritabani tarafindan reddedilir. Yarim kalmis
-- iki es zamanli pg_restore, defterin basina gelebilecek en kotu sey.
CREATE UNIQUE INDEX IF NOT EXISTS uq_restore_tek_aktif
    ON restore_requests ((true))
    WHERE status IN ('bekliyor', 'yedekleniyor', 'yukleniyor');
