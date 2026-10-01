-- Free time a plan gifts when a comercio subscribes (0 = none). It postpones the first billing date.
-- Run this on the SuperAdmin database (the one that holds the `planes` table).
ALTER TABLE planes ADD COLUMN IF NOT EXISTS dias_gratis integer NOT NULL DEFAULT 0;
