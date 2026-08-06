-- Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
-- See LICENSE in the project root for license information.
--
-- india_debt_schema.sql -- one row per ISIN per CHANGE, not per day.
--
-- THE POINT OF THE PRIMARY KEY. (isin, last_updated_date) means a daily pull
-- writes nothing at all for an instrument whose rating has not moved. The
-- NSDL active list is 28,745 rows; a naive daily snapshot would add ten
-- million rows a year and answer no question that this does not. Ratings move
-- rarely, so on a typical day this table takes single-digit inserts.
--
-- HOW TO READ HISTORY OUT OF IT. Each row is valid from its last_updated_date
-- until the next row for the same ISIN. The rating on any past date is the row
-- with the greatest last_updated_date not after it. That is the whole query;
-- there is no end-date column to keep consistent, and therefore no way for the
-- start and end of adjacent rows to disagree.
--
-- WHAT COUNTS AS A CHANGE is decided in ingest/store.py, not here, because it
-- is a policy and policies belong where they can be tested. The columns it
-- watches are the rating, the borrower category and the issuer key.
--
-- COUNTRY-SPECIFIC BY SCHEMA. Ratings scales, borrower categories and the
-- meaning of "PSU" are all Indian here. A second country gets its own schema
-- with its own table rather than a country column, because a country column
-- invites a query that forgets to filter on it and silently averages two
-- rating scales that do not share a meaning.

CREATE SCHEMA IF NOT EXISTS india;

CREATE TABLE IF NOT EXISTS india.debt_security (
    isin                CHAR(12)     NOT NULL,
    last_updated_date   DATE         NOT NULL,

    issuer_name         TEXT         NOT NULL,
    issuer_key          TEXT         NOT NULL,
    description         TEXT,
    instrument_type     TEXT,

    tier                TEXT,
    borrower_category   TEXT         NOT NULL,
    sector              TEXT,
    ownership           TEXT,

    raw_rating          TEXT,
    rating_grade        TEXT,
    rating_rank         SMALLINT,
    enhanced            BOOLEAN      NOT NULL DEFAULT FALSE,
    not_cooperating     BOOLEAN      NOT NULL DEFAULT FALSE,

    coupon              NUMERIC(10, 4),
    face_value          NUMERIC(20, 2),
    issue_size          NUMERIC(24, 2),
    allotment           DATE,
    redemption          DATE,
    status              TEXT,

    first_seen_date     DATE         NOT NULL,
    source              TEXT         NOT NULL DEFAULT 'nsdl_active',

    PRIMARY KEY (isin, last_updated_date)
);

-- The triangulation path: given an issuer, every instrument they have out.
CREATE INDEX IF NOT EXISTS ix_debt_security_issuer_key
    ON india.debt_security (issuer_key, last_updated_date DESC);

-- The fuzzy-match path resolves a name to an issuer_key, then arrives here.
CREATE INDEX IF NOT EXISTS ix_debt_security_issuer_name
    ON india.debt_security (issuer_name);

-- ISIN characters 1-7 identify the ISSUER: INE062A is State Bank of India,
-- whatever the last five characters say. This index is what lets an ISIN that
-- has never been seen still resolve to a borrower.
CREATE INDEX IF NOT EXISTS ix_debt_security_isin_prefix
    ON india.debt_security (LEFT(isin, 7));

CREATE INDEX IF NOT EXISTS ix_debt_security_category
    ON india.debt_security (borrower_category);

-- DROPPED AND RECREATED, NOT REPLACED. CREATE OR REPLACE VIEW can only ADD
-- columns at the END of the select list: adding one in the middle fails with
-- "cannot change name of view column", which is what happened when
-- subordinated_count went in before total_issue_size. Dropping first makes the
-- file re-runnable whatever order the columns end up in, and the alternative
-- -- always appending new columns to the end -- is a rule that has to be
-- remembered every time instead of once.
--
-- Order matters here: issuer_current reads debt_security_current, so it goes
-- first. No CASCADE, deliberately: if something else has come to depend on
-- these, the DROP should fail loudly rather than take the dependent with it.
DROP VIEW IF EXISTS india.issuer_current;
DROP VIEW IF EXISTS india.debt_security_current;

-- The current picture: the latest row per ISIN and nothing else.
CREATE VIEW india.debt_security_current AS
SELECT DISTINCT ON (isin) *
FROM india.debt_security
ORDER BY isin, last_updated_date DESC;

-- One row per issuer, carrying the WORST current rating across everything
-- they have outstanding.
--
-- WORST, NOT LATEST, AND THE CHOICE MATTERS. An issuer with AAA senior paper
-- and an A+ subordinated line is not an AAA borrower; IL&FS was AAA on its
-- senior programme weeks before it defaulted. Taking the worst is the
-- conservative reading and it is the one a concentration cap should see.
-- rating_rank is ascending from AAA, so the worst is the MAXIMUM rank.
CREATE VIEW india.issuer_current AS
SELECT
    issuer_key,
    MIN(issuer_name)                              AS issuer_name,
    MODE() WITHIN GROUP (ORDER BY borrower_category) AS borrower_category,
    MAX(rating_rank)                              AS worst_rating_rank,
    (ARRAY_AGG(rating_grade ORDER BY rating_rank DESC NULLS LAST))[1]
                                                  AS worst_rating_grade,
    MAX(last_updated_date)                        AS last_updated_date,
    COUNT(*)                                      AS instrument_count,
    -- Seniority is a property of the INSTRUMENT, not the borrower, so an
    -- issuer-level answer cannot state it. What it CAN state is whether this
    -- borrower has subordinated paper outstanding at all -- which is the
    -- warning that matters when a holding resolved by name rather than by
    -- ISIN might be any instrument in the programme.
    COUNT(*) FILTER (WHERE tier = 'subordinated') AS subordinated_count,
    SUM(issue_size)                               AS total_issue_size,
    BOOL_OR(not_cooperating)                      AS any_not_cooperating
FROM india.debt_security_current
GROUP BY issuer_key;
