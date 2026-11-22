-- Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
-- See LICENSE in the project root for license information.
--
-- india_fund_schema.sql -- what each fund holds, and what that is worth.
--
-- THE HOLDINGS MODEL IS A SNAPSHOT PER MONTH, NOT A CHANGE LOG, AND THAT IS
-- THE OPPOSITE OF india.debt_security ON PURPOSE. The two sources behave
-- differently and copying the NSDL pattern here would be wrong:
--
--   NSDL publishes a CURRENT picture. An instrument's rating changes rarely
--   and history has to be constructed by recording each change, which is why
--   that table is keyed (isin, last_updated_date) and writes only on movement.
--
--   ETMoney publishes the HISTORY ITSELF -- twenty-five months of month-end
--   holdings in one page. So history is not constructed here, it is received,
--   and the natural key is (fund_id, as_on, company_id, instrument_code).
--
-- WHICH IS ALSO HOW A HOLDING BEING EXTINGUISHED IS RECORDED: it simply has no
-- row in the following month. There is no delete, no tombstone and no is_active
-- flag to keep consistent, and "what did this fund hold in March" is answered
-- by selecting one as_on rather than by reconstructing anything.
--
-- RESTATEMENTS ARE THE CASE THAT NEEDS REAL WORK, and they are why
-- fund_holding_snapshot exists. ETMoney can republish a past month with
-- different numbers. Each (fund_id, as_on) therefore carries a content hash of
-- its own rows; when the hash differs from what is stored, that month's rows
-- are deleted and reinserted as a unit. A month is replaced whole or not at
-- all, because a half-updated month sums to something other than 100%.
--
-- TWICE-WEEKLY FETCHES WRITE NOTHING MOST OF THE TIME. Month-end holdings
-- change once a month; the hash comparison means eight fetches a month produce
-- one write.

CREATE SCHEMA IF NOT EXISTS india;

-- ── the funds themselves ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS india.fund (
    fund_id             INTEGER      PRIMARY KEY,      -- ETMoney's id
    label               TEXT         NOT NULL,
    amfi_name           TEXT,                          -- as AMFI writes it
    -- FROM THE NAVAll SECTION HEADER, which is the only place it appears.
    -- fund_score ranks by percentile WITHIN a category, so without this every
    -- fund lands in one bucket and the ranking says nothing.
    category            TEXT,
    scheme_type         TEXT,                          -- Open/Close Ended
    amc                 TEXT,
    amfi_scheme_code    INTEGER,
    seo_web_url         TEXT,
    match_score         NUMERIC(6, 2),                 -- how it was resolved
    first_seen_date     DATE         NOT NULL,
    last_fetched_date   DATE         NOT NULL
);

-- ── holdings ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS india.fund_holding (
    fund_id             INTEGER      NOT NULL
                          REFERENCES india.fund (fund_id) ON DELETE CASCADE,
    as_on               DATE         NOT NULL,
    company_id          INTEGER      NOT NULL,
    instrument_code     INTEGER      NOT NULL,
    -- ONE ISSUER CAN HOLD TWO LINES OF THE SAME INSTRUMENT TYPE IN ONE MONTH.
    -- HUDCO appears twice in July 2026 under Debenture, at 6.74% and 2.78% --
    -- two different bonds that ETMoney distinguishes only by its own row id.
    -- So (fund, month, company, instrument) is NOT unique, and an ordinal is
    -- needed. It is assigned deterministically from the row contents rather
    -- than from arrival order, so the same month reloaded produces the same
    -- keys and the snapshot hash stays meaningful.
    line_no             SMALLINT     NOT NULL,
    source_id           BIGINT,

    company             TEXT,
    sector              TEXT,
    instrument_type     TEXT,
    asset_class         TEXT         NOT NULL,   -- equity | debt | other
    percent             NUMERIC(9, 4),
    value               NUMERIC(24, 2),

    is_issuer           BOOLEAN      NOT NULL DEFAULT TRUE,
    expected_in_nsdl    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_securitised      BOOLEAN      NOT NULL DEFAULT FALSE,
    seniority           TEXT,                    -- from the instrument type

    -- Resolved against india.debt_security at load time. Nullable throughout:
    -- a money market holding has a borrower and no rating, and an equity
    -- holding has neither.
    rating              TEXT,
    rating_band         TEXT,                    -- AAA | AA | Other | NULL
    rating_confidence   NUMERIC(6, 4),
    -- WHERE THE RATING CAME FROM, and it exists because leaving it out cost a
    -- run. Overrides were applied first and re-enrichment last, so the rules
    -- wrote their answer back over every hand-entered rating -- Karur Vysya,
    -- Titan, DBS and Julius Baer all returned to the review queue the same
    -- afternoon they were cleared. Ordering alone would fix it today and
    -- break again the next time someone reorders; a source column lets
    -- re-enrichment SKIP what a person decided.
    rating_source       TEXT,
    borrower_category   TEXT,
    gap                 TEXT,                    -- why there is no rating

    last_updated_date   DATE         NOT NULL,

    PRIMARY KEY (fund_id, as_on, company_id, instrument_code, line_no)
);

CREATE INDEX IF NOT EXISTS ix_fund_holding_company
    ON india.fund_holding (company);
CREATE INDEX IF NOT EXISTS ix_fund_holding_as_on
    ON india.fund_holding (as_on DESC);
CREATE INDEX IF NOT EXISTS ix_fund_holding_gap
    ON india.fund_holding (gap) WHERE gap IS NOT NULL;

-- One row per (fund, month). The hash is over the month's holdings, so a
-- restatement is detectable without comparing row by row.
CREATE TABLE IF NOT EXISTS india.fund_holding_snapshot (
    fund_id             INTEGER      NOT NULL
                          REFERENCES india.fund (fund_id) ON DELETE CASCADE,
    as_on               DATE         NOT NULL,
    content_hash        CHAR(64)     NOT NULL,
    holding_count       INTEGER      NOT NULL,
    percent_total       NUMERIC(9, 4),
    first_loaded_date   DATE         NOT NULL,
    last_updated_date   DATE         NOT NULL,
    restatements        INTEGER      NOT NULL DEFAULT 0,
    PRIMARY KEY (fund_id, as_on)
);

-- ── asset allocation, which is a TAX fact ───────────────────────────────────
--
-- The equity share decides the bucket, not merely the risk profile:
--   >= 65% domestic equity   equity bucket, 20% STCG / 12.5% LTCG
--   35% to < 65%             old debt bucket, 12.5% LTCG beyond 24 months
--   < 35%                    s.50AA specified debt fund, slab rate, always
-- A fund drifting across 65% changes its own tax treatment, and this table is
-- the series that shows it happening.
--
-- equity_percent IS AN UPPER BOUND ON THE TAX-RELEVANT SHARE, not the share
-- itself: the statutory test is on DOMESTIC equity and the source does not
-- separate overseas holdings. Recorded as a column comment rather than left
-- for someone to rediscover.

CREATE TABLE IF NOT EXISTS india.fund_asset_allocation (
    fund_id             INTEGER      NOT NULL
                          REFERENCES india.fund (fund_id) ON DELETE CASCADE,
    as_on               DATE         NOT NULL,
    equity_percent      NUMERIC(9, 4) NOT NULL,
    debt_percent        NUMERIC(9, 4) NOT NULL,
    other_percent       NUMERIC(9, 4) NOT NULL,
    tax_bucket          TEXT         NOT NULL,
    last_updated_date   DATE         NOT NULL,
    PRIMARY KEY (fund_id, as_on)
);

COMMENT ON COLUMN india.fund_asset_allocation.equity_percent IS
  'Upper bound on the tax-relevant share: the s.50AA/equity tests are on '
  'DOMESTIC equity and the source does not separate overseas holdings.';

-- ── rating allocation ───────────────────────────────────────────────────────
--
-- BOTH SIDES ARE STORED, WHICH IS THE POINT. `source` is 'derived' for what
-- our own per-holding lookups produce and 'etmoney' for what the fund page
-- publishes. Keeping them in one table with a source column means the
-- comparison is a query rather than a report, and a fund where the two drift
-- apart is findable across the whole universe instead of one at a time.

CREATE TABLE IF NOT EXISTS india.fund_rating_allocation (
    fund_id             INTEGER      NOT NULL
                          REFERENCES india.fund (fund_id) ON DELETE CASCADE,
    as_on               DATE         NOT NULL,
    source              TEXT         NOT NULL,   -- derived | etmoney
    rating_band         TEXT         NOT NULL,   -- AAA | AA | Other | Unrated
    percent             NUMERIC(9, 4) NOT NULL,
    last_updated_date   DATE         NOT NULL,
    PRIMARY KEY (fund_id, as_on, source, rating_band)
);

-- ── the manual queue ────────────────────────────────────────────────────────
--
-- WHAT NSDL CANNOT ANSWER. Commercial paper and certificates of deposit carry
-- SHORT-TERM ratings on a different scale; state development loans and
-- treasury bills are government paper; securitisation trusts -- Shivshakti,
-- Siddhivinayak, India Universal Trust AL1 -- have no issuer to look up at
-- all. None of these is a failure of the matcher and no amount of retrying
-- fixes them. They need a person with a rating agency's website open.
--
-- KEYED ON THE NORMALISED NAME, NOT ON THE FUND. An override entered once for
-- "India Universal Trust AL1" answers for every fund holding it, which is the
-- difference between a queue that shrinks and one that repeats.
--
-- EVERY OVERRIDE EXPIRES. A rating looked up by hand in August is not evidence
-- about the following August, and an override with no expiry silently becomes
-- permanent. review_by defaults to a year out; the pending view surfaces both
-- the never-answered and the gone-stale.

CREATE TABLE IF NOT EXISTS india.issuer_rating_override (
    issuer_key          TEXT         PRIMARY KEY,   -- normalised name
    display_name        TEXT         NOT NULL,      -- as the source wrote it
    rating              TEXT,
    rating_band         TEXT,
    borrower_category   TEXT,
    seniority           TEXT,
    source_note         TEXT         NOT NULL,      -- where it was found
    entered_by          TEXT         NOT NULL,
    entered_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    review_by           DATE         NOT NULL,
    is_unratable        BOOLEAN      NOT NULL DEFAULT FALSE
);

COMMENT ON COLUMN india.issuer_rating_override.is_unratable IS
  'Set when a person looked and there is genuinely no rating to find. Stops '
  'the same name returning to the queue every week.';

-- What a person has to look at: unresolved holdings with no override, biggest
-- exposure first. Aggregated across funds, because one answer clears them all.
CREATE OR REPLACE VIEW india.pending_rating_review AS
SELECT
    h.company,
    MAX(h.instrument_type)                     AS instrument_type,
    MAX(h.gap)                                 AS gap,
    COUNT(DISTINCT h.fund_id)                  AS funds_affected,
    ROUND(MAX(h.percent), 4)                   AS largest_percent,
    ROUND(SUM(h.percent), 4)                   AS total_percent,
    MAX(h.as_on)                               AS latest_as_on
FROM india.fund_holding h
LEFT JOIN india.issuer_rating_override o
       ON o.issuer_key = UPPER(TRIM(h.company))
WHERE h.gap IN ('unresolved', 'securitised', 'not_in_nsdl')
  AND h.is_issuer
  AND h.company IS NOT NULL
  AND o.issuer_key IS NULL
  AND h.as_on = (SELECT MAX(as_on) FROM india.fund_holding
                 WHERE fund_id = h.fund_id)
GROUP BY h.company
ORDER BY MAX(h.percent) DESC;

-- Overrides that have gone stale. Separate from never-answered, because they
-- are a different job: confirming a number rather than finding one.
CREATE OR REPLACE VIEW india.stale_rating_override AS
SELECT * FROM india.issuer_rating_override
WHERE review_by < CURRENT_DATE
ORDER BY review_by;


-- ── the batch log ───────────────────────────────────────────────────────────
--
-- WHY A TABLE AND NOT A PROGRESS BAR. Fifteen hundred funds at one request a
-- second is half an hour, and half an hour is long enough that something will
-- interrupt it -- a laptop lid, a dropped connection, an ETMoney hiccup. The
-- log makes the run RESUMABLE: a fund that succeeded today is skipped, so
-- restarting costs nothing and finishing is a matter of running it again.
--
-- FAILURES ARE ROWS, NOT EXCEPTIONS. One fund that will not resolve must not
-- lose the fourteen hundred that did, so every outcome is recorded and the
-- run continues. `status` separates the kinds that need different responses:
-- 'no_match' is a fund ETMoney does not list, 'error' is something that may
-- work on the next attempt, and the difference decides whether retrying is
-- worth anything.

CREATE TABLE IF NOT EXISTS india.fund_load_log (
    amfi_name           TEXT         NOT NULL,
    attempted_on        DATE         NOT NULL,
    status              TEXT         NOT NULL,   -- ok|no_match|error|skipped
    fund_id             INTEGER,
    match_score         NUMERIC(6, 2),
    months_written      INTEGER      NOT NULL DEFAULT 0,
    reason              TEXT,
    attempted_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (amfi_name, attempted_on)
);

CREATE INDEX IF NOT EXISTS ix_fund_load_log_status
    ON india.fund_load_log (attempted_on DESC, status);

-- ── issuer concentration, computed and stored ───────────────────────────────
--
-- WHY A TABLE AND NOT A VIEW. The aggregation itself is one GROUP BY, so a
-- view would do -- but "which funds breach 3.25% on one borrower" is a
-- question asked across 2,347 funds and 25 months each, and a view recomputes
-- the whole join every time it is read. More importantly, the cap that was
-- applied and the rating band that applied at the time are FACTS ABOUT THE
-- RUN: change the cap next year and a view silently restates history, while a
-- table records what was actually flagged when.
--
-- ROWS ARE PER INSTRUMENT LINE UPSTREAM. NABARD is three lines in one month
-- under Bonds and Debenture; HUDCO is two. A 3.25% cap breached across three
-- lines is still breached, so this sums them.
--
-- NON-ISSUERS ARE EXCLUDED: cash, repo and net receivables are not borrowers,
-- and a concentration screen that ranks "Net Receivables" as a top exposure is
-- measuring the fund's plumbing.

CREATE TABLE IF NOT EXISTS india.fund_issuer_exposure (
    fund_id             INTEGER      NOT NULL
                          REFERENCES india.fund (fund_id) ON DELETE CASCADE,
    as_on               DATE         NOT NULL,
    company             TEXT         NOT NULL,
    percent             NUMERIC(9, 4) NOT NULL,
    value               NUMERIC(24, 2),
    lines               SMALLINT     NOT NULL,
    asset_class         TEXT,
    borrower_category   TEXT,
    rating              TEXT,
    rating_band         TEXT,
    worst_seniority     TEXT,
    cap_applied         NUMERIC(6, 4) NOT NULL,
    -- EXEMPT ROWS ARE STILL STORED. A fund with 9.45% in NABARD is still a
    -- fund with 9.45% in NABARD; exemption clears the flag, it does not hide
    -- the exposure. exempt_reason names WHICH rule fired -- a category
    -- exemption follows NSDL and updates itself, a name exemption is a list
    -- someone maintains, and six months from now that decides whether to
    -- check the data or the list.
    cap_exempt          BOOLEAN      NOT NULL DEFAULT FALSE,
    exempt_reason       TEXT,
    over_cap            BOOLEAN      NOT NULL,
    excess              NUMERIC(9, 4) NOT NULL,
    last_updated_date   DATE         NOT NULL,
    PRIMARY KEY (fund_id, as_on, company)
);

CREATE INDEX IF NOT EXISTS ix_fund_issuer_exposure_company
    ON india.fund_issuer_exposure (company, as_on DESC);
CREATE INDEX IF NOT EXISTS ix_fund_issuer_exposure_breach
    ON india.fund_issuer_exposure (as_on DESC, excess DESC)
    WHERE over_cap;

-- ── scores ──────────────────────────────────────────────────────────────────
--
-- ONE ROW PER FUND PER REPORTING MONTH. Funds publish at different times, so
-- as_on is the latest month THAT fund has rather than a single date across the
-- universe; forcing one date would silently drop whoever had not reported yet.
--
-- peer_group IS NOT ALWAYS THE CATEGORY. A category with fewer than five funds
-- cannot support a percentile -- three funds score 0, 50 and 100 whatever
-- their merits -- so it is rolled up to its scheme type and this column
-- records which field the rank was taken against. A fund ranked among all Debt
-- Scheme funds is not comparable to one ranked within Liquid Fund, and the
-- column is what lets a reader tell.
--
-- has_credit MATTERS AS MUCH AS THE SCORE. A fund with no stored holdings gets
-- no credit multiplier, so it is scored on performance alone -- which would
-- otherwise rank it above funds whose paper was actually checked.

CREATE TABLE IF NOT EXISTS india.fund_score (
    fund_id             INTEGER      NOT NULL
                          REFERENCES india.fund (fund_id) ON DELETE CASCADE,
    as_on               DATE         NOT NULL,
    peer_group          TEXT         NOT NULL,
    peers               INTEGER      NOT NULL,
    rank                INTEGER      NOT NULL,
    percentile          NUMERIC(6, 2),
    base_score          NUMERIC(8, 2),
    credit_multiplier   NUMERIC(6, 4),
    composite           NUMERIC(8, 2),
    credit_quality      NUMERIC(6, 2),
    breach_score        NUMERIC(6, 2),
    worst_issuer        NUMERIC(9, 4),
    over_cap            SMALLINT,
    metrics_window      TEXT,
    history_years       NUMERIC(6, 2),
    has_credit          BOOLEAN      NOT NULL DEFAULT FALSE,
    joined_by           TEXT,
    last_updated_date   DATE         NOT NULL,
    PRIMARY KEY (fund_id, as_on)
);

CREATE INDEX IF NOT EXISTS ix_fund_score_group
    ON india.fund_score (peer_group, rank);

-- ── migration for tables that already exist ─────────────────────────────────
--
-- CREATE TABLE IF NOT EXISTS SILENTLY SKIPS AN EXISTING TABLE, so every column
-- added to this file after the first run is missing from any database that ran
-- an earlier version. That is not a hypothetical: a batch over 2,314 funds
-- died on its first fund because india.fund had no `category` column, and the
-- error it reported was about an aborted transaction rather than the missing
-- column.
--
-- EVERY STATEMENT HERE IS IDEMPOTENT. ADD COLUMN IF NOT EXISTS is a no-op on a
-- fresh database and on a second run, so this block can sit in the same file
-- as the CREATE statements and be applied every time.
--
-- DEFAULTS ARE CHOSEN SO EXISTING ROWS STAY VALID. line_no defaults to 0,
-- which is correct for every row already stored: the OLD primary key forbade
-- two lines of the same instrument for one issuer, so no existing row can
-- collide with another at 0.

ALTER TABLE india.fund
    ADD COLUMN IF NOT EXISTS category         TEXT,
    ADD COLUMN IF NOT EXISTS scheme_type      TEXT,
    ADD COLUMN IF NOT EXISTS amc              TEXT,
    ADD COLUMN IF NOT EXISTS amfi_scheme_code INTEGER;

ALTER TABLE india.fund_holding
    ADD COLUMN IF NOT EXISTS line_no       SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source_id     BIGINT,
    ADD COLUMN IF NOT EXISTS rating_source TEXT;

ALTER TABLE india.fund_issuer_exposure
    ADD COLUMN IF NOT EXISTS cap_exempt    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS exempt_reason TEXT;

-- The holdings primary key gained line_no, because one issuer can hold two
-- lines of the same instrument type in one month -- HUDCO does, at 6.74% and
-- 2.78%. Rebuilt only when the existing key lacks it, so a fresh database is
-- untouched and a second run does nothing.
DO $$
DECLARE
    key_cols TEXT;
BEGIN
    SELECT string_agg(a.attname, ',' ORDER BY k.ord)
      INTO key_cols
      FROM pg_constraint c
      JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attnum = k.attnum
     WHERE c.conrelid = 'india.fund_holding'::regclass
       AND c.contype = 'p';

    IF key_cols IS NOT NULL AND key_cols NOT LIKE '%line_no%' THEN
        EXECUTE 'ALTER TABLE india.fund_holding DROP CONSTRAINT '
             || (SELECT conname FROM pg_constraint
                  WHERE conrelid = 'india.fund_holding'::regclass
                    AND contype = 'p');
        ALTER TABLE india.fund_holding
            ADD PRIMARY KEY (fund_id, as_on, company_id, instrument_code,
                             line_no);
        RAISE NOTICE 'india.fund_holding primary key rebuilt with line_no';
    END IF;
END $$;

-- ── views over the migrated tables ──────────────────────────────────────────
--
-- LAST, AND DROPPED FIRST. Both use SELECT e.*, which PostgreSQL expands at
-- CREATE time -- so a view defined before the migration would freeze the
-- pre-migration column list and never show cap_exempt however often the file
-- is re-run. Dropping and recreating after the ALTERs is what keeps them in
-- step.
DROP VIEW IF EXISTS india.issuer_cap_breach;
DROP VIEW IF EXISTS india.issuer_across_funds;

-- Every breach in the latest month of every fund, worst first. The question
-- that prompted the table, answerable without writing the join.
CREATE VIEW india.issuer_cap_breach AS
SELECT e.*
FROM india.fund_issuer_exposure e
WHERE e.over_cap
  AND e.as_on = (SELECT MAX(as_on) FROM india.fund_issuer_exposure
                 WHERE fund_id = e.fund_id)
ORDER BY e.excess DESC;

-- The other direction: one borrower, every fund exposed to it. What you read
-- the morning after a downgrade.
CREATE VIEW india.issuer_across_funds AS
SELECT company,
       COUNT(DISTINCT fund_id)  AS funds,
       ROUND(MAX(percent), 4)   AS largest_percent,
       ROUND(AVG(percent), 4)   AS mean_percent,
       SUM(value)               AS total_value,
       MAX(rating_band)         AS rating_band,
       BOOL_OR(over_cap)        AS any_over_cap
FROM india.fund_issuer_exposure e
WHERE as_on = (SELECT MAX(as_on) FROM india.fund_issuer_exposure
               WHERE fund_id = e.fund_id)
GROUP BY company
ORDER BY COUNT(DISTINCT fund_id) DESC, MAX(percent) DESC;
