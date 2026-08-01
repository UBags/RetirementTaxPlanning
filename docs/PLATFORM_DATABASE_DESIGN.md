# Platform Database — Design and Build Plan

31 July 2026. PostgreSQL. Multi-country from the first line of DDL.

This describes the data platform that will sit under the retirement tax
planner: fund reference data, daily NAVs, users, KYC, needs assessment, folios,
and a transaction ledger from which FIFO lots can be reconstructed.

---

## 0. Principles, and why each one is non-negotiable

**Money is never a float.** `NUMERIC(20,4)` for amounts, `NUMERIC(20,6)` for
units, `NUMERIC(16,6)` for NAV. The tax engine already quantises inputs to one
paisa to kill non-determinism; a float column would put it straight back.
Postgres `NUMERIC` is exact decimal arithmetic — use it everywhere money or
units appear, with no exceptions for "just a report".

**The transaction ledger is append-only and is the only source of truth.**
Positions, lots and valuations are all derived. Never update a position in
place. Every financial record gets `created_at`, `created_by`, and a
correction mechanism that writes a reversing entry rather than a `DELETE`.

**Anything that changes over time gets bitemporal treatment.** Fund category,
tax classification, slab rates, exemption limits — all of these change, and
some change *retroactively*. You need to answer both "what was the category on
1 April 2023?" and "what did we *believe* the category was when we ran the
plan last March?". Without the second, a past calculation cannot be reproduced
and any dispute is unwinnable.

**The database is versioned like code.** Alembic or Sqitch, migrations in git,
never a manual `psql` change against anything but a scratch database. With
database-per-country, every migration runs N times, so this discipline is the
difference between a platform and a mess.

---

## 1. Multi-country: database per country

Three options, and the choice matters more than it looks.

| approach | isolation | cross-country reporting | migration cost | residency compliance |
|---|---|---|---|---|
| **database per country** | strongest | needs FDW or app-level | N runs | clean |
| schema per country | good | trivial (single connection) | 1 run | co-located |
| `country_id` column everywhere | weakest | trivial | 1 run | co-located |

**Take database-per-country.** Not for the isolation — for the law. India's
DPDP Act 2023, the RBI's payment-data localisation directive, and GDPR for any
European expansion all constrain where personal data may physically sit. A
`country_id` column cannot satisfy a residency requirement; a separate database
on a separate host can. Getting this wrong later means a migration under legal
deadline, which is the worst possible time.

```
platform          -- shared, tiny, no personal data
  country
  currency
  data_source_kind
  schema_version

rtp_in            -- India
rtp_sg            -- Singapore, later
rtp_ae            -- UAE, later
```

`platform` holds only what is genuinely universal and non-personal. Everything
about a user, a fund or a transaction lives in that user's country database.

Cross-country queries — "how many users across all countries" — go through the
application, or `postgres_fdw` if a reporting layer is ever needed. Do not
build the FDW until something actually needs it.

**Migration discipline.** One `migrations/` tree, applied to every country
database in sequence by a runner that records `schema_version` per database and
refuses to skip. A country database that has drifted is a defect, not a
variant.

### 1.1 Roles

`postgres/postgres` is fine for local development and must not survive contact
with anything else. Even now, create the roles the application will use, so the
permission model is exercised from day one:

```sql
CREATE ROLE rtp_owner    LOGIN PASSWORD '...';   -- owns DDL, runs migrations
CREATE ROLE rtp_app      LOGIN PASSWORD '...';   -- the application; DML only
CREATE ROLE rtp_ingest   LOGIN PASSWORD '...';   -- feed writers; INSERT on
                                                 -- reference tables only
CREATE ROLE rtp_readonly LOGIN PASSWORD '...';   -- analytics, probes
```

`rtp_app` gets no `DELETE` on ledger tables. That is a schema-level guarantee,
not a code convention, and it is worth more than any amount of review.

---

## 2. The `platform` database

```sql
CREATE TABLE country (
    country_id      SMALLINT PRIMARY KEY,
    iso2            CHAR(2)  NOT NULL UNIQUE,      -- 'IN'
    iso3            CHAR(3)  NOT NULL UNIQUE,      -- 'IND'
    name            TEXT     NOT NULL,
    database_name   TEXT     NOT NULL,             -- 'rtp_in'
    base_currency   CHAR(3)  NOT NULL,             -- 'INR'
    fiscal_year_start_month SMALLINT NOT NULL,     -- 4 for India
    fiscal_year_start_day   SMALLINT NOT NULL,     -- 1
    is_active       BOOLEAN  NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE currency (
    code            CHAR(3) PRIMARY KEY,
    name            TEXT NOT NULL,
    minor_units     SMALLINT NOT NULL DEFAULT 2
);
```

`fiscal_year_start_month` is doing real work: India runs April–March, most of
the world runs January–December, and every tax calculation in the engine keys
off the fiscal year. Hard-coding April is how the second country becomes a
rewrite.

---

## 3. Per-country schema

Each country database carries these schemas:

```
ref       fund and market reference data
party     users, KYC, brokers, AMCs
plan      needs assessment, planning inputs
book      folios, positions, transactions, lots
tax       slabs, exemptions, classifications
audit     immutable change log
```

### 3.1 `ref` — data sources

Configuration lives in the database, not in code, so that adding a country is
a data change. The India rows come from the existing codebase.

```sql
CREATE TABLE ref.data_source (
    source_id       SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,   -- 'amfi_navall'
    purpose         TEXT NOT NULL,          -- 'nav_daily' | 'scheme_master' |
                                            -- 'performance' | 'holdings'
    url_template    TEXT NOT NULL,
    http_method     TEXT NOT NULL DEFAULT 'GET',
    headers         JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_format TEXT NOT NULL,          -- 'json' | 'csv' | 'fixed_text'
    rate_limit_ms   INTEGER NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    notes           TEXT
);
```

Seed for India, taken from what the codebase already calls:

| code | purpose | url_template |
|---|---|---|
| `amfi_navall` | `nav_daily` | `https://portal.amfiindia.com/spages/NAVAll.txt` |
| `amfi_nav_history` | `nav_history` | `https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt={from}&todt={to}&Sch={scheme_code}` |
| `mfapi_scheme_list` | `scheme_master` | `https://api.mfapi.in/mf` |
| `mfapi_scheme_nav` | `nav_history` | `https://api.mfapi.in/mf/{scheme_code}` |
| `amfi_performance` | `performance` | `https://www.amfiindia.com/gateway/pollingsebi/api/amfi/fundperformance` |
| `groww_scheme_page` | `enrichment` | `https://groww.in/mutual-funds/{slug}-direct-growth` |
| `amfi_holdings` | `holdings` | **none — see §7** |

Every fetch is recorded, because a silently stale feed is the failure mode that
produces confident wrong numbers:

```sql
CREATE TABLE ref.ingest_run (
    run_id          BIGSERIAL PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES ref.data_source,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',  -- running|ok|failed|partial
    rows_seen       INTEGER,
    rows_inserted   INTEGER,
    rows_updated    INTEGER,
    rows_rejected   INTEGER,
    request_url     TEXT,
    http_status     INTEGER,
    payload_sha256  CHAR(64),      -- detects "the feed did not change today"
    error_text      TEXT
);
CREATE INDEX ON ref.ingest_run (source_id, started_at DESC);
```

`payload_sha256` earns its place: an unchanged hash on a trading day means the
feed is stale, not that nothing moved.

### 3.2 `party` — AMCs, brokers, distributors

```sql
CREATE TABLE party.amc (
    amc_id          SERIAL PRIMARY KEY,
    code            TEXT UNIQUE,            -- internal short code
    name            TEXT NOT NULL,
    legal_name      TEXT,
    sebi_reg_no     TEXT,
    address_line1   TEXT,
    address_line2   TEXT,
    city            TEXT,
    state           TEXT,
    postal_code     TEXT,
    contact_phone   TEXT,
    contact_email   TEXT,
    website         TEXT,
    rta_name        TEXT,                   -- CAMS / KFintech
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE party.broker (
    broker_id       SERIAL PRIMARY KEY,
    code            TEXT UNIQUE,
    name            TEXT NOT NULL,
    legal_name      TEXT,
    broker_type     TEXT NOT NULL,          -- 'broker'|'distributor'|'ria'|'direct'
    sebi_reg_no     TEXT,
    arn_code        TEXT,                   -- AMFI Registration Number
    address_line1   TEXT,
    address_line2   TEXT,
    city            TEXT,
    state           TEXT,
    postal_code     TEXT,
    contact_phone   TEXT,
    contact_email   TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE party.depository_participant (
    dp_id           SERIAL PRIMARY KEY,
    dp_code         TEXT NOT NULL UNIQUE,   -- the DP ID proper
    depository      TEXT NOT NULL,          -- 'NSDL' | 'CDSL'
    name            TEXT NOT NULL,
    broker_id       INTEGER REFERENCES party.broker,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);
```

**Why a DP is its own table rather than a column on the folio.** A DP ID is
issued by NSDL or CDSL to a participant, and a folio held in demat form
references it. It is not a property of the folio and it is not the same thing
as the broker, even though one broker usually operates as one DP. Modelling it
as a text column on the folio guarantees inconsistent spellings within a year.

### 3.3 `ref` — the fund master, and why categorisation is separate

```sql
CREATE TABLE ref.fund (
    fund_id         BIGSERIAL PRIMARY KEY,
    amfi_scheme_code TEXT UNIQUE,           -- '119551'
    isin_growth     CHAR(12),
    isin_div        CHAR(12),
    amc_id          INTEGER NOT NULL REFERENCES party.amc,
    scheme_name     TEXT NOT NULL,
    plan_type       TEXT,                   -- 'direct' | 'regular'
    option_type     TEXT,                   -- 'growth' | 'idcw'
    launch_date     DATE,
    close_date      DATE,                   -- non-null once wound up
    status          TEXT NOT NULL DEFAULT 'active',  -- active|merged|closed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON ref.fund (isin_growth) WHERE isin_growth IS NOT NULL;
```

**Scheme lineage — funds merge and get renamed, and AMFI codes get retired.**
Without this, a merger silently orphans a user's holding and the FIFO chain
breaks at exactly the point where the gain matters most.

```sql
CREATE TABLE ref.fund_lineage (
    lineage_id      BIGSERIAL PRIMARY KEY,
    predecessor_fund_id BIGINT NOT NULL REFERENCES ref.fund,
    successor_fund_id   BIGINT NOT NULL REFERENCES ref.fund,
    event_type      TEXT NOT NULL,          -- 'merger'|'rename'|'reclassify'
    effective_date  DATE NOT NULL,
    unit_ratio      NUMERIC(20,10),         -- successor units per predecessor unit
    notes           TEXT,
    CHECK (predecessor_fund_id <> successor_fund_id)
);
```

**Categorisation is bitemporal and lives in its own table.**

```sql
CREATE TABLE ref.fund_category (
    category_id     BIGSERIAL PRIMARY KEY,
    fund_id         BIGINT NOT NULL REFERENCES ref.fund,
    scheme_class    TEXT NOT NULL,   -- 'Debt Scheme - Liquid Fund'
    asset_class     TEXT NOT NULL,   -- 'debt'|'equity'|'hybrid'|'other'
    sub_class       TEXT,
    valid_from      DATE NOT NULL,   -- when it applied in the world
    valid_to        DATE,            -- NULL = still current
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),   -- when we learned it
    source_id       INTEGER REFERENCES ref.data_source,
    EXCLUDE USING gist (
        fund_id WITH =,
        daterange(valid_from, COALESCE(valid_to, 'infinity'::date)) WITH &&
    )
);
```

The `EXCLUDE` constraint makes overlapping validity periods **impossible**
rather than merely discouraged. This is the single most valuable constraint in
the schema: it means "what category was this fund on date D" always has exactly
one answer, enforced by the database.

`recorded_at` is what lets you reproduce a plan run from six months ago using
the categories you believed then, which you will need the first time a number
is disputed.

### 3.4 `tax` — classification is NOT the SEBI category

This distinction has bitten the existing engine already and deserves its own
table.

SEBI says "Debt Scheme — Liquid Fund". The Income Tax Act says "specified
mutual fund", defined by equity exposure thresholds, and the definition
**changed on 1 April 2023 and again on 23 July 2024**. A fund's tax treatment
is a function of its portfolio composition and the law on the transaction date
— not of its marketing category.

```sql
CREATE TABLE tax.fund_tax_class (
    tax_class_id    BIGSERIAL PRIMARY KEY,
    fund_id         BIGINT NOT NULL REFERENCES ref.fund,
    tax_treatment   TEXT NOT NULL,   -- 'equity_112a'|'specified_debt'|'other'
    equity_pct      NUMERIC(6,3),    -- what drove the classification
    stcg_months     SMALLINT NOT NULL,
    ltcg_rate       NUMERIC(6,4),
    stcg_rate       NUMERIC(6,4),
    ltcg_exemption  NUMERIC(20,4),   -- s.112A's annual exemption, per assessee
    indexation      BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    statute_ref     TEXT,            -- 'Finance Act 2024 s.50AA'
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    EXCLUDE USING gist (
        fund_id WITH =,
        daterange(valid_from, COALESCE(valid_to, 'infinity'::date)) WITH &&
    )
);
```

Slab and exemption schedules are country-level and equally time-varying:

```sql
CREATE TABLE tax.regime (
    regime_id       SERIAL PRIMARY KEY,
    code            TEXT NOT NULL,          -- 'new_regime'|'old_regime'
    entity_type     TEXT NOT NULL,          -- 'individual'|'huf'
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    basic_exemption NUMERIC(20,4) NOT NULL,
    rebate_ceiling  NUMERIC(20,4),          -- s.87A
    rebate_has_marginal_relief BOOLEAN NOT NULL DEFAULT TRUE,
    cess_rate       NUMERIC(6,4) NOT NULL,
    statute_ref     TEXT
);

CREATE TABLE tax.slab (
    slab_id         BIGSERIAL PRIMARY KEY,
    regime_id       INTEGER NOT NULL REFERENCES tax.regime,
    lower_bound     NUMERIC(20,4) NOT NULL,
    upper_bound     NUMERIC(20,4),          -- NULL = infinity
    rate            NUMERIC(6,4) NOT NULL,
    sort_order      SMALLINT NOT NULL
);

CREATE TABLE tax.surcharge (
    surcharge_id    BIGSERIAL PRIMARY KEY,
    regime_id       INTEGER NOT NULL REFERENCES tax.regime,
    income_over     NUMERIC(20,4) NOT NULL,
    rate            NUMERIC(6,4) NOT NULL,
    applies_to      TEXT NOT NULL DEFAULT 'all'  -- 'all'|'special_rate_only'
);
```

`rebate_has_marginal_relief` is not decoration. The s.87A claw-back creates a
**104% marginal band** just above the ceiling, which the planning engine must
steer around; storing it as a flag means the engine can ask rather than assume.

### 3.5 `ref` — daily NAV, the big table

Roughly 10,000 schemes × 250 trading days × 25 years ≈ **60 million rows**, and
it only grows. Partition it from the start; retrofitting partitioning onto a
live 60M-row table is a maintenance window nobody wants.

```sql
CREATE TABLE ref.nav_daily (
    fund_id     BIGINT      NOT NULL REFERENCES ref.fund,
    nav_date    DATE        NOT NULL,
    nav         NUMERIC(16,6) NOT NULL,
    repurchase  NUMERIC(16,6),
    sale        NUMERIC(16,6),
    source_id   INTEGER     REFERENCES ref.data_source,
    run_id      BIGINT      REFERENCES ref.ingest_run,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fund_id, nav_date)
) PARTITION BY RANGE (nav_date);

CREATE TABLE ref.nav_daily_2026 PARTITION OF ref.nav_daily
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
-- one per year, created ahead by a maintenance job

CREATE INDEX ON ref.nav_daily (nav_date);
```

Partition by `nav_date` rather than `fund_id`: the dominant query is "give me
this fund's NAVs over a date range", which prunes well by date, and it lets old
years be detached and archived whole.

**Derived rolling returns.** The FD benchmark needs six-month rolling returns
across liquid funds, which the current codebase cannot produce because it has
no NAV series. Once `nav_daily` exists, that becomes a materialised view:

```sql
CREATE MATERIALIZED VIEW ref.fund_return_6m AS
SELECT  n.fund_id,
        n.nav_date AS as_of,
        n.nav / p.nav - 1                       AS ret_6m,
        (n.nav / p.nav - 1) * 2                 AS ret_6m_annualised_simple
FROM    ref.nav_daily n
JOIN LATERAL (
        SELECT nav FROM ref.nav_daily x
        WHERE  x.fund_id = n.fund_id
          AND  x.nav_date <= n.nav_date - INTERVAL '6 months'
        ORDER BY x.nav_date DESC LIMIT 1
) p ON TRUE;
CREATE UNIQUE INDEX ON ref.fund_return_6m (fund_id, as_of);
```

`ret_6m_annualised_simple` doubles rather than compounds, exactly as the FD
benchmark specification asks. Refresh concurrently, nightly.

### 3.6 `ref` — AUM and metrics history

Metrics are point-in-time and get revised, so they are a time series, not
columns on `fund`.

```sql
CREATE TABLE ref.fund_metrics (
    fund_id     BIGINT NOT NULL REFERENCES ref.fund,
    as_of       DATE   NOT NULL,
    aum         NUMERIC(20,4),
    expense_ratio NUMERIC(8,5),
    cagr_1y     NUMERIC(10,6),
    cagr_3y     NUMERIC(10,6),
    cagr_5y     NUMERIC(10,6),
    cagr_10y    NUMERIC(10,6),
    std_dev     NUMERIC(10,6),
    sharpe      NUMERIC(10,6),
    sortino     NUMERIC(10,6),
    calmar      NUMERIC(10,6),
    alpha       NUMERIC(10,6),
    beta        NUMERIC(10,6),
    treynor     NUMERIC(10,6),
    max_drawdown NUMERIC(10,6),
    modified_duration NUMERIC(10,6),   -- debt funds
    ytm         NUMERIC(10,6),
    avg_maturity_years NUMERIC(10,4),
    credit_quality JSONB,
    source_id   INTEGER REFERENCES ref.data_source,
    run_id      BIGINT REFERENCES ref.ingest_run,
    PRIMARY KEY (fund_id, as_of)
);
```

`modified_duration`, `ytm` and `avg_maturity_years` are not in the current
codebase and belong here: an interest-rate regime shift is the stated principal
risk, and duration is the quantity that determines how much a debt fund is hurt
by it.

### 3.7 `party` — users and KYC

```sql
CREATE TABLE party.user_account (
    user_id         BIGSERIAL PRIMARY KEY,
    external_ref    UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    email           CITEXT NOT NULL UNIQUE,
    phone_e164      TEXT,
    full_name       TEXT NOT NULL,
    date_of_birth   DATE,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|active|suspended|closed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE party.tax_entity (
    entity_id       BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES party.user_account,
    entity_type     TEXT NOT NULL,          -- 'individual'|'huf'
    display_name    TEXT NOT NULL,
    tax_id_hash     BYTEA,                  -- PAN, hashed; never stored raw
    tax_id_last4    CHAR(4),                -- for display only
    residency_status TEXT,                  -- 'resident'|'nri'|'rnor'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, entity_type, display_name)
);
```

**`tax_entity` is the load-bearing abstraction.** The whole planning engine
works across two purses — personal and HUF — with separate slab tables and
separate s.112A exemptions. Modelling that as a first-class entity rather than
a boolean on the user is what makes the model portable to a country with a
different entity structure.

**Never store a raw PAN.** Hash it with a per-deployment salt for uniqueness
checks, keep the last four for display. Raw identifiers are a liability with no
compensating benefit.

```sql
CREATE TABLE party.kyc_case (
    kyc_case_id     BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES party.user_account,
    entity_id       BIGINT REFERENCES party.tax_entity,
    provider        TEXT NOT NULL,          -- 'stub'|'ckyc'|'kra_cvl'|'ballerine'
    provider_ref    TEXT,
    status          TEXT NOT NULL DEFAULT 'not_started',
                    -- not_started|in_progress|verified|rejected|expired|manual_review
    risk_level      TEXT,                   -- 'low'|'medium'|'high'
    verified_at     TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE party.kyc_event (
    kyc_event_id    BIGSERIAL PRIMARY KEY,
    kyc_case_id     BIGINT NOT NULL REFERENCES party.kyc_case,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type      TEXT NOT NULL,
    request_payload JSONB,
    response_payload JSONB,
    http_status     INTEGER,
    actor           TEXT
);

CREATE TABLE party.kyc_document (
    document_id     BIGSERIAL PRIMARY KEY,
    kyc_case_id     BIGINT NOT NULL REFERENCES party.kyc_case,
    doc_type        TEXT NOT NULL,          -- 'pan'|'aadhaar'|'passport'|'selfie'
    storage_uri     TEXT NOT NULL,          -- object store; NOT in the database
    sha256          CHAR(64) NOT NULL,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    redacted_at     TIMESTAMPTZ
);
```

**KYC is stubbed, and the stub is the point.** Indian KYC runs through CKYC
(CERSAI) or a KRA — CVL, NDML, CAMS, Karvy — and requires regulated
intermediary access. No open-source project implements it and none can. The
schema above is provider-agnostic: `provider` and `provider_ref` mean a real
integration later is a new value in a column, not a migration.

Search findings, recorded so they are not re-searched:

- **[Ballerine](https://github.com/ballerine-io/ballerine)** — the only serious
  self-hostable option. Postgres-backed, multi-country vendor orchestration,
  case-management UI, rule engine. Heavy: a TypeScript monorepo, and it
  orchestrates vendors rather than verifying anything itself. Worth adopting
  only if manual review workflow becomes a real need.
- The rest — `ai-kyc-platform`, `Self-Hosted-KYC-Verification-Platform`,
  `Simple_KYC` — are student or hobby projects doing OCR, face match and
  liveness. None does Indian CKYC.

**Decision: stub the provider behind an interface, record every call in
`kyc_event`, and mark real integration TBD.** Documents go to object storage
with only a URI and hash in the database.

### 3.8 `plan` — needs assessment

Nothing off the shelf. GitHub has retirement *calculators* — FIRE, corpus,
Monte Carlo — but no structured needs-capture instrument for Indian retirees.
Build our own, and make it **versioned**, because the questions will change and
you must be able to say which version a given user answered.

```sql
CREATE TABLE plan.questionnaire (
    questionnaire_id SERIAL PRIMARY KEY,
    code            TEXT NOT NULL,
    version         INTEGER NOT NULL,
    title           TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    published_at    TIMESTAMPTZ,
    UNIQUE (code, version)
);

CREATE TABLE plan.question (
    question_id     BIGSERIAL PRIMARY KEY,
    questionnaire_id INTEGER NOT NULL REFERENCES plan.questionnaire,
    section         TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    code            TEXT NOT NULL,      -- stable key, e.g. 'annual_need_today'
    prompt          TEXT NOT NULL,
    answer_type     TEXT NOT NULL,      -- 'money'|'int'|'date'|'bool'|'enum'|'text'
    unit            TEXT,               -- 'INR_lakh'|'years'|'percent'
    enum_options    JSONB,
    is_required     BOOLEAN NOT NULL DEFAULT TRUE,
    validation      JSONB,              -- {"min":0,"max":1000}
    help_text       TEXT,
    UNIQUE (questionnaire_id, code)
);

CREATE TABLE plan.assessment (
    assessment_id   BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES party.user_account,
    questionnaire_id INTEGER NOT NULL REFERENCES plan.questionnaire,
    status          TEXT NOT NULL DEFAULT 'draft',   -- draft|submitted|superseded
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at    TIMESTAMPTZ
);

CREATE TABLE plan.answer (
    answer_id       BIGSERIAL PRIMARY KEY,
    assessment_id   BIGINT NOT NULL REFERENCES plan.assessment,
    question_id     BIGINT NOT NULL REFERENCES plan.question,
    value_numeric   NUMERIC(20,4),
    value_text      TEXT,
    value_date      DATE,
    value_bool      BOOLEAN,
    answered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (assessment_id, question_id)
);
```

The assessment then projects into the planning inputs the engine already
consumes — horizon, requirement ladder, other income by head, corpus,
risk posture:

```sql
CREATE TABLE plan.planning_input (
    planning_input_id BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES party.user_account,
    assessment_id   BIGINT REFERENCES plan.assessment,
    horizon_years   SMALLINT NOT NULL CHECK (horizon_years >= 20),
    start_fy        SMALLINT NOT NULL,
    requirement_schedule JSONB NOT NULL,   -- [{"from_year":1,"to_year":5,"amount":35.0}]
    income_taxable_other NUMERIC(20,4) NOT NULL DEFAULT 0,
    income_taxfree_other NUMERIC(20,4) NOT NULL DEFAULT 0,
    income_salary        NUMERIC(20,4) NOT NULL DEFAULT 0,
    income_rental        NUMERIC(20,4) NOT NULL DEFAULT 0,
    income_step_per_year NUMERIC(20,4) NOT NULL DEFAULT 0,
    debt_floor_multiples NUMERIC(8,2)[] NOT NULL DEFAULT '{25,22,18}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (horizon_years >= 20)
);
```

The `CHECK (horizon_years >= 20)` mirrors `MIN_HORIZON` in the model. Business
rules that the engine enforces should be enforced here too — a database that
accepts data the engine will reject is a support ticket waiting to happen.

### 3.9 `book` — folios

```sql
CREATE TABLE book.folio (
    folio_id        BIGSERIAL PRIMARY KEY,
    entity_id       BIGINT NOT NULL REFERENCES party.tax_entity,
    folio_number    TEXT NOT NULL,          -- as printed by the AMC/RTA
    holder_kind     TEXT NOT NULL,          -- 'amc'|'broker'
    amc_id          INTEGER REFERENCES party.amc,
    broker_id       INTEGER REFERENCES party.broker,
    dp_id           INTEGER REFERENCES party.depository_participant,
    demat_account   TEXT,
    holding_mode    TEXT NOT NULL DEFAULT 'statement',  -- 'statement'|'demat'
    opened_on       DATE NOT NULL,
    closed_on       DATE,
    purpose         TEXT,                   -- 'vintage_2027' etc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ( (holder_kind = 'amc'    AND amc_id    IS NOT NULL)
         OR (holder_kind = 'broker' AND broker_id IS NOT NULL) ),
    UNIQUE (entity_id, folio_number, amc_id)
);
```

The `CHECK` enforces your "Holder ID is either a Broker ID or an AMC ID" as a
constraint rather than a convention. `purpose` is there because the strategy
deliberately opens vintage folios and a human needs to know which is which.

**Folio uniqueness is per AMC, not global.** Two AMCs can and do issue the same
folio number.

### 3.10 `book` — transactions, the ledger

Append-only. Everything else is derived from this.

```sql
CREATE TABLE book.transaction (
    txn_id          BIGSERIAL PRIMARY KEY,
    folio_id        BIGINT NOT NULL REFERENCES book.folio,
    fund_id         BIGINT NOT NULL REFERENCES ref.fund,
    txn_type        TEXT NOT NULL,
        -- purchase|sip|switch_in|switch_out|redemption|swp
        -- |dividend_payout|dividend_reinvest|bonus|split
        -- |segregation|merger_in|merger_out|reversal
    txn_date        DATE NOT NULL,          -- the transaction date
    nav_date        DATE NOT NULL,          -- the NAV applied (may differ)
    posted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    units           NUMERIC(20,6) NOT NULL, -- signed: + in, − out
    nav             NUMERIC(16,6) NOT NULL,
    gross_amount    NUMERIC(20,4) NOT NULL,
    stamp_duty      NUMERIC(20,4) NOT NULL DEFAULT 0,
    exit_load       NUMERIC(20,4) NOT NULL DEFAULT 0,
    stt             NUMERIC(20,4) NOT NULL DEFAULT 0,
    tds             NUMERIC(20,4) NOT NULL DEFAULT 0,
    net_amount      NUMERIC(20,4) NOT NULL, -- what hit the bank
    reverses_txn_id BIGINT REFERENCES book.transaction,
    source          TEXT NOT NULL DEFAULT 'manual',  -- manual|cas_import|broker_api
    source_ref      TEXT,
    created_by      TEXT NOT NULL DEFAULT current_user,
    CHECK (units <> 0 OR txn_type IN ('dividend_payout')),
    CHECK (nav > 0)
);
CREATE INDEX ON book.transaction (folio_id, fund_id, txn_date);
CREATE INDEX ON book.transaction (fund_id, txn_date);

REVOKE DELETE, UPDATE ON book.transaction FROM rtp_app;
```

**Corporate actions are in the type list deliberately, and they are the thing
most likely to be forgotten.** Bonus units, splits, IDCW reinvestment,
side-pocketing (`segregation`) and scheme mergers all change unit counts
without a purchase. Each creates or alters FIFO lots with its own cost basis
rule. Omit them and the lot chain silently produces wrong gains — which is
exactly the failure mode this platform exists to avoid.

Corrections are **reversing entries**, never updates. `reverses_txn_id` links
them, and `REVOKE UPDATE, DELETE` makes it the only available route.

### 3.11 `book` — lots and positions, both derived

FIFO lots drive every tax number in the engine. They are derived from the
ledger, materialised for speed, and rebuildable from scratch.

```sql
CREATE TABLE book.lot (
    lot_id          BIGSERIAL PRIMARY KEY,
    folio_id        BIGINT NOT NULL REFERENCES book.folio,
    fund_id         BIGINT NOT NULL REFERENCES ref.fund,
    acquired_on     DATE NOT NULL,
    acquire_txn_id  BIGINT NOT NULL REFERENCES book.transaction,
    units_acquired  NUMERIC(20,6) NOT NULL CHECK (units_acquired > 0),
    units_remaining NUMERIC(20,6) NOT NULL CHECK (units_remaining >= 0),
    cost_per_unit   NUMERIC(16,6) NOT NULL,
    cost_total      NUMERIC(20,4) NOT NULL,
    is_grandfathered BOOLEAN NOT NULL DEFAULT FALSE,   -- 31-Jan-2018 s.112A
    grandfathered_nav NUMERIC(16,6),
    CHECK (units_remaining <= units_acquired)
);
CREATE INDEX ON book.lot (folio_id, fund_id, acquired_on)
    WHERE units_remaining > 0;

CREATE TABLE book.lot_consumption (
    consumption_id  BIGSERIAL PRIMARY KEY,
    lot_id          BIGINT NOT NULL REFERENCES book.lot,
    redeem_txn_id   BIGINT NOT NULL REFERENCES book.transaction,
    units           NUMERIC(20,6) NOT NULL CHECK (units > 0),
    sale_nav        NUMERIC(16,6) NOT NULL,
    proceeds        NUMERIC(20,4) NOT NULL,
    cost_basis      NUMERIC(20,4) NOT NULL,
    gain            NUMERIC(20,4) NOT NULL,
    holding_days    INTEGER NOT NULL,
    gain_type       TEXT NOT NULL,          -- 'stcg'|'ltcg'
    tax_class_id    BIGINT REFERENCES tax.fund_tax_class,
    fy              SMALLINT NOT NULL
);
CREATE INDEX ON book.lot_consumption (redeem_txn_id);
CREATE INDEX ON book.lot_consumption (fy, gain_type);
```

**`is_grandfathered` and `grandfathered_nav`** cover s.112A's 31 January 2018
cost-step-up for equity acquired before that date. Retirees hold pre-2018
units; omitting this overstates their gains substantially.

**`tax_class_id` on the consumption row, not looked up at query time.** The tax
classification in force on the *redemption date* determines the treatment, and
that classification can change later. Recording which one was applied makes the
calculation reproducible.

Positions are a materialised view:

```sql
CREATE MATERIALIZED VIEW book.position_current AS
SELECT  l.folio_id, l.fund_id,
        SUM(l.units_remaining)                          AS units,
        SUM(l.units_remaining * l.cost_per_unit)        AS cost_basis,
        MIN(l.acquired_on)                              AS oldest_lot_date
FROM    book.lot l
WHERE   l.units_remaining > 0
GROUP BY l.folio_id, l.fund_id;
CREATE UNIQUE INDEX ON book.position_current (folio_id, fund_id);
```

Your (g) — "starting position, additions, redemptions, ending position" — is a
**report over the ledger**, not a stored table:

```sql
CREATE VIEW book.fund_movement AS
SELECT  folio_id, fund_id, txn_date,
        SUM(CASE WHEN units > 0 THEN units ELSE 0 END)  AS units_in,
        SUM(CASE WHEN units < 0 THEN -units ELSE 0 END) AS units_out,
        SUM(units) OVER (PARTITION BY folio_id, fund_id
                         ORDER BY txn_date, txn_id)     AS units_closing
FROM    book.transaction
GROUP BY folio_id, fund_id, txn_date, txn_id, units;
```

Storing a running balance on each row instead means every backdated correction
rewrites history, and the two representations drift.

### 3.12 `audit`

```sql
CREATE TABLE audit.change_log (
    change_id   BIGSERIAL PRIMARY KEY,
    table_name  TEXT NOT NULL,
    row_pk      TEXT NOT NULL,
    operation   CHAR(1) NOT NULL,           -- I|U|D
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by  TEXT NOT NULL DEFAULT current_user,
    old_row     JSONB,
    new_row     JSONB
);
```

Attach a generic trigger to every table in `party`, `book` and `tax`. Reference
data in `ref` is covered by `ingest_run` and does not need row-level audit.

---

## 4. Build plan

Each phase ends with a demonstrable capability and its own tests. **No phase
depends on a later one.**

### Phase 0 — foundations (½ day)

Cluster, roles, `platform` database, migration runner, `pytest` fixtures that
spin up a throwaway database per test session.

*Test:* migrations apply from empty to head and roll back cleanly. `rtp_app`
cannot `DELETE` from a ledger table — assert the failure.

### Phase 1 — India reference data and NAVs (2–3 days)

`ref.data_source` seeded from the codebase's existing URLs. `ref.fund`,
`ref.nav_daily` partitioned, `ref.ingest_run`. Loaders for AMFI `NAVAll.txt`
(daily) and `mfapi.in` (backfill).

*Test:* load a frozen `NAVAll.txt` fixture and assert row counts, idempotency
on re-run, and that a duplicate `(fund_id, nav_date)` updates rather than
duplicates. Assert an unchanged `payload_sha256` on a trading day raises.

**This phase alone unblocks the FD benchmark's specified rate calculation**,
which cannot be computed today for want of a NAV series.

### Phase 2 — categorisation and tax classification (2 days)

`ref.fund_category` and `tax.fund_tax_class` with their `EXCLUDE` constraints.
`tax.regime`, `tax.slab`, `tax.surcharge` seeded with the real Indian schedule
including the 2023 and 2024 changes.

*Test:* attempt to insert an overlapping validity period and assert the
database rejects it. Assert `category_on(fund, date)` returns exactly one row
for every fund across a 30-year date sweep. Reconcile the seeded slabs against
the engine's current hard-coded ones — **any difference is a finding**.

### Phase 3 — parties (1–2 days)

`party.amc`, `party.broker`, `party.depository_participant`. Seed AMCs from the
distinct issuers already in the fund master.

*Test:* referential integrity, and that a folio cannot be created with neither
an AMC nor a broker.

### Phase 4 — users, entities, KYC stub (2–3 days)

`party.user_account`, `party.tax_entity`, the `kyc_*` tables, and a
`KycProvider` interface with a `StubProvider` that records requests and returns
a configurable verdict.

*Test:* full lifecycle to `verified` and to `rejected`; every transition
appears in `kyc_event`; no raw tax identifier is ever persisted — assert by
scanning the table for PAN-shaped strings.

### Phase 5 — folios, transactions, lots (4–5 days, the hard one)

`book.folio`, `book.transaction`, `book.lot`, `book.lot_consumption`, and the
lot engine that projects the ledger into lots.

*Test:* this is where the effort goes.
- A purchase then a partial redemption consumes the oldest lot first and leaves
  the right remainder.
- Bonus units create a zero-cost lot without disturbing existing ones.
- A merger maps units at `unit_ratio` and **preserves acquisition dates** —
  holding period must survive the merger.
- A reversing entry restores the prior lot state exactly.
- Rebuilding lots from scratch reproduces the incremental result **byte for
  byte**. This is the single most important test in the platform.
- Property test: for any random transaction sequence, `SUM(units_remaining)`
  equals `SUM(units)` from the ledger.

### Phase 6 — needs assessment (2–3 days)

The questionnaire tables, a seeded v1 instrument for Indian retirees, and the
projection into `plan.planning_input`.

*Test:* a completed assessment projects to a `planning_input` the existing
engine accepts unchanged. Assert the `horizon_years >= 20` check fires.

### Phase 7 — engine integration (3–4 days)

Point the planner at the database instead of `golden_projects.py`. Fund
metadata, NAVs, tax schedules and planning inputs all come from Postgres.

*Test:* **the golden fixtures, loaded into a test database, must reproduce the
existing snapshot CSVs exactly.** If they do not, the port has changed
behaviour and the difference must be explained before proceeding.

### Phase 8 — holdings (open-ended, lowest priority)

No clean feed exists. AMFI does not publish portfolio holdings via API; monthly
disclosures are per-AMC XLS and PDF in inconsistent formats. This is a scraper
per AMC and a normalisation layer, and it should not block anything else.

*Test:* schema and loader against two AMCs' formats; treat wide coverage as a
long tail.

### Phase 9 — second country (2 days, once)

Add a `country` row, create `rtp_sg`, run the same migrations, seed that
country's `data_source` rows.

*Test:* the migration runner brings a brand-new country database to head with
no manual step. **If this phase needs a code change, the multi-country design
has failed and it is far cheaper to learn that here than later.**

---

## 5. Open questions

- **Fiscal-year boundary in the database.** The engine works in fiscal years;
  the ledger works in dates. `fy` is stored on `lot_consumption` as a
  denormalised convenience — decide whether it is generated or written, and
  from which country's fiscal calendar.
- **Object storage for KYC documents.** Local filesystem for development, S3 or
  equivalent later. Encryption at rest is required either way.
- **Retention.** DPDP requires deletion on request; financial records require
  retention. These conflict and need a policy: probably pseudonymise the party
  and keep the ledger.
- **The `is_grandfathered` 31-Jan-2018 NAV** needs a source. AMFI published it
  once; it is not in any live feed.
- **Whether `platform` should hold user identity** for cross-country users.
  Currently assumed no — a person is a user of one country's deployment.

---

## 6. What this unblocks

Beyond the obvious, three things the current codebase cannot do at all:

1. **The FD benchmark's specified rate** — six-month rolling returns across
   liquid funds. Needs `ref.nav_daily`. Phase 1.
2. **Monte Carlo over real return paths** rather than a deterministic CAGR.
   Needs NAV history. Phase 1.
3. **Duration-aware interest-rate stress testing** — the stated principal risk
   is a shift from a high-rate to a low-rate regime, and `modified_duration` is
   the quantity that determines how much each debt fund is hurt. Needs
   `ref.fund_metrics`. Phase 1.

All three fall out of the same phase, which is why Phase 1 should be first even
though the user-facing work looks more urgent.
