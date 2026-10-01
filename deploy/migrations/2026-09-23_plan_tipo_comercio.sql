-- Each plan belongs to a type of commerce: 'restobar' or 'store' (the SuperAdmin manages them in two lists).
-- Existing plans stay as 'restobar'. Run this on the SuperAdmin database (the one that holds the `planes` table).
ALTER TABLE planes ADD COLUMN IF NOT EXISTS tipo_comercio varchar(20) NOT NULL DEFAULT 'restobar';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'planes_tipo_comercio_chk') THEN
        ALTER TABLE planes ADD CONSTRAINT planes_tipo_comercio_chk CHECK (tipo_comercio IN ('restobar', 'store'));
    END IF;
END
$$;
