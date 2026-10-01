-- Lets a support message carry a pasted/attached image (stored as a data: URL, like the comercio logo).
-- Run on the superadmin database.
ALTER TABLE soporte_mensajes ADD COLUMN IF NOT EXISTS imagen_url text;
