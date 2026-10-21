-- Indicativo telefónico de cada región (solo dígitos, sin "+"), para armar el número internacional
-- cuando alguien escribe su teléfono sin el indicativo del país.
ALTER TABLE regiones ADD COLUMN IF NOT EXISTS indicativo varchar(6) NOT NULL DEFAULT '';

UPDATE regiones r SET indicativo = v.ind
FROM (VALUES
    ('CO','57'),('ES','34'),('FR','33'),('GT','502'),('BZ','501'),('HN','504'),('SV','503'),('NI','505'),
    ('CR','506'),('PA','507'),('VE','58'),('EC','593'),('PE','51'),('BR','55'),('BO','591'),('CL','56'),
    ('AR','54'),('PY','595'),('UY','598'),('GY','592'),('SR','597'),('CU','53'),('DO','1'),('HT','509'),
    ('JM','1'),('TT','1'),('BS','1'),('BB','1'),('AG','1'),('LC','1'),('MX','52'),('US','1'),('CA','1')
) AS v(codigo, ind)
WHERE r.codigo_pais = v.codigo AND r.indicativo = '';
