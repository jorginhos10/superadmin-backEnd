-- Ajustes generales del panel SuperAdmin: una sola fila (id = 1) con flags globales.
-- Run this on the SuperAdmin database (the one that holds the `planes`/`apariencia_login` tables).
CREATE TABLE IF NOT EXISTS ajustes_generales (
    id smallint PRIMARY KEY DEFAULT 1,
    categoria_registros_habilitada boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ajustes_generales_singleton CHECK (id = 1)
);

INSERT INTO ajustes_generales (id, categoria_registros_habilitada)
VALUES (1, true)
ON CONFLICT (id) DO NOTHING;
