-- Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
-- Instrument ratings, stored as HISTORY rather than as current state.
--
-- THE TABLE IS APPEND-ONLY AND THIS IS THE WHOLE DESIGN. A store holding one
-- current rating per instrument would have said DHFL is AAA every day until the
-- day it said DHFL is D, with no record that anything moved. The level fooled
-- every investor who looked at it; the trajectory did not. So every download
-- inserts a dated observation and nothing is ever updated in place.
--
-- RATINGS ATTACH TO THE ISIN, NOT THE ISSUER. Yes Bank's AT1 was written to
-- zero in March 2020 while its senior debt was money-good. An issuer-level
-- rating column would have carried one grade for both, and would have been
-- wrong by several notches in the one case that cost money.

CREATE SCHEMA IF NOT EXISTS ref;

-- The normalised borrower. The hard part of this whole pipeline is deciding
-- that 'DEWAN HOUSING FIN CORP LTD', 'DHFL' and 'Dewan Housing Finance
-- Corporation Limited' are one company, because exposures do not aggregate
-- until they are.
CREATE TABLE IF NOT EXISTS ref.issuer (
    issuer_id       BIGSERIAL PRIMARY KEY,
    canonical_name  TEXT NOT NULL UNIQUE,
    sector          TEXT,              -- 'nbfc'|'hfc'|'bank'|'psu'|...
    group_name      TEXT,              -- for group-level limits
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every spelling ever seen, and where it was seen. Resolution is recorded
-- rather than performed on the fly, so a wrong merge can be found and undone.
CREATE TABLE IF NOT EXISTS ref.issuer_alias (
    alias_id        BIGSERIAL PRIMARY KEY,
    issuer_id       BIGINT NOT NULL REFERENCES ref.issuer,
    alias           TEXT NOT NULL,
    source          TEXT NOT NULL,     -- 'nsdl'|'nse_wdm'|'amc_portfolio'
    confidence      TEXT NOT NULL DEFAULT 'exact',  -- exact|fuzzy|manual
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (alias, source)
);

CREATE TABLE IF NOT EXISTS ref.instrument (
    isin            CHAR(12) PRIMARY KEY,
    issuer_id       BIGINT REFERENCES ref.issuer,
    sectype         TEXT,              -- NSE WDM code: GS, SG, DB, AT, CP...
    tier            TEXT NOT NULL,     -- fund_filters.instruments tier
    state_code      CHAR(2),           -- SDL only
    description     TEXT,
    issue_date      DATE,
    maturity_date   DATE,              -- NULL for perpetual
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (tier IN ('sovereign','state','corporate','subordinated',
                    'money_market_paper','unknown'))
);
CREATE INDEX IF NOT EXISTS instrument_issuer_idx ON ref.instrument (issuer_id);
CREATE INDEX IF NOT EXISTS instrument_tier_idx ON ref.instrument (tier);

-- APPEND ONLY. One row per (instrument, as-on date, agency).
CREATE TABLE IF NOT EXISTS ref.instrument_rating (
    rating_id       BIGSERIAL PRIMARY KEY,
    isin            CHAR(12) NOT NULL REFERENCES ref.instrument,
    as_on           DATE NOT NULL,     -- the file's as-on date, not now()
    agency          TEXT,
    grade           TEXT,              -- 'AAA', 'A1+', NULL if unparsed
    scale           TEXT,              -- 'long'|'short'
    rank            SMALLINT,          -- ordinal; lower is better
    enhanced        BOOLEAN NOT NULL DEFAULT FALSE,  -- (CE) or (SO)
    outlook         TEXT,
    withdrawn       BOOLEAN NOT NULL DEFAULT FALSE,
    raw             TEXT NOT NULL,     -- the string exactly as received
    ingest_run_id   BIGINT,            -- REFERENCES ref.ingest_run
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (scale IS NULL OR scale IN ('long','short')),
    -- Idempotent re-ingest: running the same file twice changes nothing.
    UNIQUE (isin, as_on, agency)
);
CREATE INDEX IF NOT EXISTS rating_isin_ason_idx
    ON ref.instrument_rating (isin, as_on DESC);

-- The raw file, kept so a parser bug can be re-run against what was actually
-- downloaded rather than against what we remember downloading.
CREATE TABLE IF NOT EXISTS ref.rating_download (
    download_id     BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,     -- 'nsdl_debt_instruments'|'nsdl_active'
    as_on           DATE NOT NULL,
    url             TEXT,
    file_path       TEXT NOT NULL,
    sha256          CHAR(64) NOT NULL,
    row_count       INTEGER,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, as_on, sha256)
);

-- Current rating per instrument, derived rather than stored, so there is
-- exactly one definition of 'current' and it cannot drift from the history.
CREATE OR REPLACE VIEW ref.instrument_rating_current AS
SELECT DISTINCT ON (isin, agency) *
FROM ref.instrument_rating
ORDER BY isin, agency, as_on DESC;

-- Every movement, which is the thing worth alerting on. Negative notch_change
-- is a downgrade.
CREATE OR REPLACE VIEW ref.instrument_rating_change AS
SELECT isin, agency, scale,
       lag(as_on) OVER w  AS prev_as_on,
       lag(grade) OVER w  AS prev_grade,
       lag(rank)  OVER w  AS prev_rank,
       as_on, grade, rank,
       lag(rank) OVER w - rank AS notch_change
FROM ref.instrument_rating
WHERE grade IS NOT NULL
WINDOW w AS (PARTITION BY isin, agency ORDER BY as_on);
