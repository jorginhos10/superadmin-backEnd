-- Product images for the marketplace catalog: up to 5 per product, stored as data URLs
-- (the first one is the cover). Run on the SuperAdmin database (marketplace_productos).
ALTER TABLE marketplace_productos ADD COLUMN IF NOT EXISTS imagenes jsonb NOT NULL DEFAULT '[]'::jsonb;
