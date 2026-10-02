-- Estilo que usa "Automático" en los comercios fuera de temporada (Halloween/Navidad): un solo
-- valor global que el SuperAdmin marca con la estrella en Apariencias > Sistema.
-- Run on the superadmin database.
CREATE TABLE IF NOT EXISTS apariencia_automatico (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    apariencia_key varchar(30) NOT NULL DEFAULT 'violet-original',
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO apariencia_automatico (id) VALUES (1) ON CONFLICT DO NOTHING;
