-- Si está activo, un comercio nuevo pasa directo a 'aprobado' al enviar su solicitud de
-- registro (ver onboarding.py en cloud-control-backEnd), sin pasar por la cola de revisión
-- manual del SuperAdmin.
ALTER TABLE ajustes_generales ADD COLUMN IF NOT EXISTS autoaceptar_registros boolean NOT NULL DEFAULT false;
