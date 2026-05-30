-- Ejercicio 6 — Pipeline ETL
--
-- Esquema de la tabla destino. Identico al del E3 (mismo schema fijo del
-- modulo), copiado aqui para que el pipeline sea self-contained: el evaluador
-- puede clonar el repo y correr `pipeline.py` sin haber corrido el E3 antes.
-- Si se desea cargar sobre la base del E3, basta con apuntar `--db` a ella.
--
-- Solo creamos la tabla. Los indices secundarios del E3 son para queries y
-- no afectan la correctness de la ingesta; solo penalizarian la insercion.

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id  TEXT    PRIMARY KEY,
    timestamp       TEXT    NOT NULL,
    user_id         INTEGER NOT NULL,
    merchant_id     INTEGER NOT NULL,
    amount          REAL    NOT NULL,
    category        TEXT    NOT NULL,
    country_code    TEXT    NOT NULL,
    status          TEXT    NOT NULL
);
