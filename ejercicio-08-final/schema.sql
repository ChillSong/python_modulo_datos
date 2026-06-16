-- Esquema de la tabla de transacciones.
-- Mismo schema fijo del modulo (E1 paso 1), reutilizado en E3/E4/E5/E6/E8.
-- Los indices secundarios del E3 se crean en setup_db.py tras la ingesta
-- masiva, para maximizar la velocidad de carga inicial.

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
