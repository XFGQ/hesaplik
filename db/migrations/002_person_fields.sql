-- Kisi kartina adres alanlari. Mevcut veriyi bozmaz.
ALTER TABLE persons ADD COLUMN IF NOT EXISTS city     TEXT;  -- il
ALTER TABLE persons ADD COLUMN IF NOT EXISTS district TEXT;  -- ilce
ALTER TABLE persons ADD COLUMN IF NOT EXISTS address  TEXT;
