-- Iconos de la app móvil que está en construcción, cargados desde el panel del SuperAdmin (pestaña Apps).
-- La imagen se guarda como data URL, igual que las del marketplace. Run on the superadmin database.
CREATE TABLE IF NOT EXISTS app_iconos (
    id serial PRIMARY KEY,
    nombre varchar(120) NOT NULL,
    plataforma varchar(10) NOT NULL DEFAULT 'todas' CHECK (plataforma IN ('todas', 'android', 'ios')),
    dimensiones varchar(20) NOT NULL DEFAULT '',
    tamano_bytes integer NOT NULL DEFAULT 0,
    imagen text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
