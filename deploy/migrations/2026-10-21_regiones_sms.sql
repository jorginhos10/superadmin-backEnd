-- Verificación por SMS país por país: solo los países con el interruptor encendido piden el código
-- al registrarse. Las regiones que ya existían quedan encendidas, para no cambiar lo que pasa hoy.
ALTER TABLE regiones ADD COLUMN IF NOT EXISTS sms_verificacion boolean NOT NULL DEFAULT false;
UPDATE regiones SET sms_verificacion = true WHERE NOT sms_verificacion AND fecha_creacion < now();
