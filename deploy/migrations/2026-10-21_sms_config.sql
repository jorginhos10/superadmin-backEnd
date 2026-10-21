-- Conexión con el proveedor de SMS, administrable desde el panel SuperAdmin (Mensajería › tuerca).
-- Una sola fila (id = 1). El backend de los comercios la lee para enviar los códigos de verificación.
CREATE TABLE IF NOT EXISTS sms_config (
    id                 smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    proveedor          text    NOT NULL DEFAULT 'inalambria',
    activo             boolean NOT NULL DEFAULT false,
    url                text    NOT NULL DEFAULT '',
    metodo             text    NOT NULL DEFAULT 'POST',
    auth_tipo          text    NOT NULL DEFAULT 'basic',   -- basic | bearer | header | ninguna
    auth_usuario       text    NOT NULL DEFAULT '',
    auth_secreto       text    NOT NULL DEFAULT '',
    auth_header_nombre text    NOT NULL DEFAULT '',
    remitente          text    NOT NULL DEFAULT '',
    content_type       text    NOT NULL DEFAULT 'json',    -- json | form
    cuerpo_plantilla   text    NOT NULL DEFAULT '{"to": "{telefono}", "message": "{mensaje}"}',
    exito_contiene     text    NOT NULL DEFAULT '',
    actualizado_en     timestamptz NOT NULL DEFAULT now()
);
INSERT INTO sms_config (id) VALUES (1) ON CONFLICT DO NOTHING;
