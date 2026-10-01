-- Imagen de vista previa de cada apariencia, la que ve el comercio al elegir su estilo visual.
-- Se guarda como data URL (ya reducida por el panel). Run on the superadmin database.
CREATE TABLE IF NOT EXISTS apariencia_imagenes (
    apariencia_key varchar(30) PRIMARY KEY,
    imagen text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
