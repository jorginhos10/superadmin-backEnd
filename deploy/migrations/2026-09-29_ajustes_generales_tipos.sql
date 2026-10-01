-- Reemplaza el flag único por dos, uno por cada tipo de comercio que se ofrece en el registro.
-- Run this on the SuperAdmin database (the one that holds the `planes`/`apariencia_login` tables).
ALTER TABLE ajustes_generales ADD COLUMN IF NOT EXISTS tipo_store_habilitado boolean NOT NULL DEFAULT true;
ALTER TABLE ajustes_generales ADD COLUMN IF NOT EXISTS tipo_restobar_habilitado boolean NOT NULL DEFAULT true;
ALTER TABLE ajustes_generales DROP COLUMN IF EXISTS categoria_registros_habilitada;
