-- Shipping price of a marketplace product. It is charged once per product line of an order,
-- whatever the quantity. Run on the SuperAdmin database (the one with marketplace_productos).
ALTER TABLE marketplace_productos ADD COLUMN IF NOT EXISTS precio_envio numeric(12, 2) NOT NULL DEFAULT 0;
