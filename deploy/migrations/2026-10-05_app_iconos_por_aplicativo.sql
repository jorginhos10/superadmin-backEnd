-- Ahora hay 4 aplicativos distintos (domiciliario, cocina, escritorio, driver) en vez de un solo
-- balde de iconos para "la app móvil": cada icono se asocia a uno de ellos. Escritorio y Driver
-- son apps de Windows, así que se agrega esa plataforma.
-- Run on the superadmin database.
ALTER TABLE app_iconos DROP CONSTRAINT IF EXISTS app_iconos_plataforma_check;
ALTER TABLE app_iconos ADD CONSTRAINT app_iconos_plataforma_check
    CHECK (plataforma IN ('todas', 'android', 'ios', 'windows'));

ALTER TABLE app_iconos ADD COLUMN IF NOT EXISTS app_key varchar(20) NOT NULL DEFAULT 'domiciliario';
ALTER TABLE app_iconos ALTER COLUMN app_key DROP DEFAULT;
ALTER TABLE app_iconos ADD CONSTRAINT app_iconos_app_key_check
    CHECK (app_key IN ('domiciliario', 'cocina', 'escritorio', 'driver'));
