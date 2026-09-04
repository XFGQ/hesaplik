-- Kayit silme (arsivleme) 500 hatasi: ledger.archive_transaction kaydi
-- archived_transactions'a kopyaladiktan sonra canli transactions'tan
-- DELETE eder. raw_messages.transaction_id FK'sinda ondelete YOKTU, bu
-- yuzden mesajdan dogmus her kayit silinemiyordu:
--   ForeignKeyViolationError: ... "raw_messages_transaction_id_fkey"
--
-- Cozum SET NULL: raw_messages dokunulmaz mesaj logudur, ASLA silinmez --
-- yalnizca silinen kayda olan baglantisi kopar (mesaj gecmisi/iz korunur).
-- Veriye dokunmaz, sadece kisit degisir.

ALTER TABLE raw_messages
    DROP CONSTRAINT IF EXISTS raw_messages_transaction_id_fkey;

ALTER TABLE raw_messages
    ADD CONSTRAINT raw_messages_transaction_id_fkey
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE SET NULL;

-- NOT: transactions.reverses_id KASTEN dokunulmadan birakildi. Orada SET NULL
-- transactions uzerinde bir UPDATE'tir; append-only tetikleyicisi (rule 1) onu
-- reddeder. Dahasi ters kaydi olan bir kaydi tek basina silmek bakiyeyi bozar
-- (ters kayit ortada kalir). Bu durum artik kodda anlasilir bir is kurali
-- hatasi verir (422), 500 degil -- bkz. ledger.archive_transaction.
