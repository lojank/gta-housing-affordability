DROP VIEW IF EXISTS affordability;
DROP TABLE IF EXISTS monthly;
DROP TABLE IF EXISTS data_quality;

CREATE TABLE monthly (
    month             TEXT PRIMARY KEY,  -- YYYY-MM
    year              INTEGER NOT NULL,
    price_index       REAL,              -- new housing price index, Dec 2016 = 100
    cpi               REAL,              -- consumer price index, 2002 = 100
    rent_2br          REAL,              -- average 2-bedroom rent, $/month
    income_real_2024  REAL,              -- median total income, 2024 dollars
    income_nominal    REAL,              -- median total income, that year's dollars
    policy_rate       REAL,              -- %, monthly average
    mortgage_rate_5y  REAL,              -- %, monthly average of posted rate
    rate_period       TEXT CHECK (rate_period IN ('rising', 'falling', 'flat')),
    estimated_notes   TEXT,              -- which values are estimates or carried forward
    is_estimated      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE data_quality (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    check_name  TEXT NOT NULL,
    ref_date    TEXT,
    detail      TEXT,
    action      TEXT
);
