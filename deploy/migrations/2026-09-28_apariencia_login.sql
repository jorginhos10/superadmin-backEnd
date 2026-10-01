-- Apariencia con la que se muestra la pantalla de inicio de sesión de los comercios (un solo valor global).
-- Run on the superadmin database.
CREATE TABLE IF NOT EXISTS apariencia_login (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    apariencia_key varchar(30) NOT NULL DEFAULT 'violet-original',
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO apariencia_login (id) VALUES (1) ON CONFLICT DO NOTHING;
