-- Ejercicio 3 — Capa Transaccional SQLite
--
-- DDL completo de la base.  Los índices secundarios (idx_*) no se
-- crean aquí: el benchmark_queries.py los aplica DESPUÉS de la
-- ingesta para poder medir el costo de cada patrón CON y SIN
-- índices.  La justificación técnica de cada decisión está en
-- schema_design.md.

-- ---------------------------------------------------------------------
-- Tabla principal
-- ---------------------------------------------------------------------
--   transaction_id  UUID4 (PK natural)               => crea índice implícito que cubre P1.
--   timestamp       ISO 8601 TEXT, ordenable lexico   => SQLite no tiene tipo datetime nativo.
--   user_id         1..50 000                         => entero corto, denso.
--   merchant_id     1..10 000                         => entero corto, denso.
--   amount          0.01..5000.00                     => REAL.
--   category        10 categorías fijas               => texto repetido; sin lookup table para mantener
--                                                       el schema fiel al del E1 (regla del PDF).
--   country_code    15 códigos ISO                    => igual.
--   status          completed/failed/pending          => igual.

CREATE TABLE transactions (
    transaction_id  TEXT    PRIMARY KEY,
    timestamp       TEXT    NOT NULL,
    user_id         INTEGER NOT NULL,
    merchant_id     INTEGER NOT NULL,
    amount          REAL    NOT NULL,
    category        TEXT    NOT NULL,
    country_code    TEXT    NOT NULL,
    status          TEXT    NOT NULL
);

-- ---------------------------------------------------------------------
-- Índices secundarios (los crea benchmark_queries.py — referencia aquí)
-- ---------------------------------------------------------------------

-- CREATE INDEX idx_txns_user_timestamp
--     ON transactions (user_id, timestamp DESC);
--   Justifica: composite leftmost-prefix.  Sirve a P2 (user_id + ORDER BY ts DESC LIMIT 20),
--   P3 (user_id + rango de timestamp), y P4 (user_id + rango de timestamp para SUM).
--
-- CREATE INDEX idx_txns_country_user
--     ON transactions (country_code, user_id);
--   Justifica: P5 filtra por country_code y agrupa por user_id.  El orden de columnas en
--   este índice deja un acceso secuencial (range scan) sobre el prefijo del índice por cada
--   país, sin tener que tocar las filas de la tabla principal (covering parcial: country_code
--   y user_id son las dos columnas usadas por la query).
