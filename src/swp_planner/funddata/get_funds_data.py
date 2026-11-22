# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
fund_analyser_v4.py
===================
Indian Mutual Fund Metrics Analyser – Version 4

New in v4 (over v3):
  1. Three explicit lookback windows – all five metrics computed for each:
       - 10-Year  (last 120 months, or all available when fund is younger):
           Std_Dev_10Y, Sharpe_10Y, Sortino_10Y, Max_DD_10Y, Calmar_10Y
       - 5-Year   (last 60 months):
           Std_Dev_5Y,  Sharpe_5Y,  Sortino_5Y,  Max_DD_5Y,  Calmar_5Y
       - 3-Year   (last 36 months):
           Std_Dev_3Y,  Sharpe_3Y,  Sortino_3Y,  Max_DD_3Y,  Calmar_3Y
       Total: 15 per-window columns.  Minimum required history: 12 months
           (lowered from 36; see MIN_HISTORY for what that costs).

  2. Three Combined_Ratio columns – sqrt(Sortino_XY × Calmar_XY):
       Combined_Ratio_10Y, Combined_Ratio_5Y, Combined_Ratio_3Y
       NaN when either component ≤ 0.

  3. Output sorted by Combined_Ratio_10Y desc → 5Y desc → 3Y desc (NaN last).

  4. Calmar numerator corrected to true CAGR (nav_end/nav_start)^(1/years) − 1,
     replacing the earlier arithmetic-mean × 12 approximation.

  5. Two new output columns inserted after Match Quality:
       Allocation_L     – placeholder column, always 0; populate manually
                          once fund selection is finalised.
       Worst_Exp_Ret_%  – min(Avg_CAGR_3Y, Avg_CAGR_5Y, Avg_CAGR_7Y,
                          Avg_CAGR_10Y) adjusted for a −0.40% STT hit on
                          Arbitrage / Tax Efficient Income funds.  1Y is
                          deliberately excluded.  The point-in-time
                          3Y/5Y/7Y/10Y_CAGR columns are a fallback only, used
                          when no Avg_CAGR_* column is present at all.
                          (This header previously read "min(1Y, 3Y, 5Y, 10Y
                          CAGR)", which never matched the computation.)

  6. KNOWN_CODE_OVERRIDES dict – hard-pins exact AMFI codes for funds where
     auto-resolution collides (highest priority over all other resolution paths).
     Current pins: Axis Short Duration, UTI Short Duration, and the four
     Income-Plus-Arbitrage FoF variants.

  7. Alpha/Beta/Treynor computed once on the longest available window (≤ 10Y)
     for regression stability; written to Alpha_10Y / Beta_10Y / Treynor_10Y.

Carried over from v3:
  - Dynamic Risk-Free Rate from overnight fund NAVs (RBI repo fallback pre-2019).
  - Nifty Composite Debt Index benchmark for debt/hybrid Alpha/Beta/Treynor.
  - AMFI NAVAll.txt resolution with Direct-Growth plan priority picker.
  - Pre-start overnight fund code trimming (OVERNIGHT_MIN_START guard).

Dependencies
------------
  pip install pandas numpy scipy yfinance requests tqdm

Usage
-----
  python fund_analyser_v4.py                          # reads Fund_Details.csv
  python fund_analyser_v4.py --input MyFunds.csv
  python fund_analyser_v4.py --workers 8
  python fund_analyser_v4.py --amfi-map amfi_map.csv

Building / reviewing the AMFI code map
---------------------------------------
  python fund_analyser_v4.py --build-map-only
"""

import argparse
import io
import logging
import re
import sys
import time

# Suppress numpy/pandas RuntimeWarnings from degenerate statistical computations
# (e.g. std/var on single-element or near-constant series like overnight fund returns).
# These are expected and handled by NaN guards in the code.
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from scipy import stats
from tqdm import tqdm

from swp_planner.paths import FUNDS_DIR, LOGS_DIR, ensure_parent

warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"(numpy|pandas|scipy).*")

# ── optional: yfinance ────────────────────────────────────────────────────────
try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

NIFTY_TICKER       = "^NSEI"
HYBRID_EQ_WEIGHT   = 0.65
HYBRID_DEBT_WEIGHT = 0.35

# ── Bond index mode ───────────────────────────────────────────────────────────
# True  → fetch Nifty Composite Debt Index from NSE public API (recommended).
#         Falls back to the synthetic 5Y bond index if the API is unavailable.
# False → always use the synthetic 5Y bond index derived from the Rf series.
USE_ACTUAL_BOND_INDEX = True

# CLI defaults, all under data/funds/ (see swp_planner.paths). The GUI always
# passes explicit --input/--output paths inside the per-user output folder.
INPUT_FILE         = str(FUNDS_DIR / "Fund Details.csv")
OUTPUT_FILE        = str(FUNDS_DIR / "Fund_Metrics_Output.csv")
ERROR_FILE         = str(FUNDS_DIR / "Fund_Metrics_Errors.csv")
AMFI_MAP_FILE      = str(FUNDS_DIR / "amfi_map.csv")

MFAPI_BASE         = "https://api.mfapi.in/mf"

# AMFI NAVAll.txt – daily updated master file with ALL scheme codes
AMFI_NAV_ALL_URL   = "https://www.amfiindia.com/spages/NAVAll.txt?t=1"

# ── Hard-coded AMFI code overrides ───────────────────────────────────────────
# Use this dict to pin specific fund names to exact AMFI codes when auto-
# resolution produces the wrong match (e.g. two similarly-named funds).
# Keys must match the "Fund Name" column in Fund Details.csv exactly.
# Priority: KNOWN_CODE_OVERRIDES > pre-supplied CSV column > amfi_map.csv > auto-resolve.
KNOWN_CODE_OVERRIDES: dict[str, str] = {
    # Short Duration collision – two funds resolve to same code without this
    "Axis Short Duration Fund":                          "120510",
    "UTI Short Duration Fund":                           "120718",
    # Income-Plus-Arbitrage FoF series – correct codes confirmed manually
    "Axis Income Plus Arbitrage Active FoF":             "147889",
    "DSP Income Plus Arbitrage Omni FoF":                "130493",
    "HDFC Income Plus Arbitrage Active FoF":             "130543",
    "ICICI Prudential Income Plus Arbitrage Omni FoF":   "120313",
}

# ── Fund types that receive a −0.40% STT adjustment on their CAGR ────────────
# (post-budget Securities Transaction Tax increase on equity-arbitrage strategies)
# Used when computing Worst_Exp_Ret_% in the output.
STT_HIT_TYPES: set[str] = {"arbitrage", "tax efficient income"}

REQUEST_TIMEOUT    = (8, 30)   # (connect_timeout, read_timeout) in seconds
                               # Fast-fail on connection; allow 30s for data transfer
MAX_RETRIES        = 2         # Reduced: fewer retries on unreachable hosts
RETRY_BACKOFF      = 1.5

# Lookback windows
WINDOW_5Y   = 60    # months for the 5-year window  (Sharpe, Sortino, Std_Dev)
WINDOW_7Y   = 84    # months for the 7-year window
WINDOW_10Y  = 120   # months for the 10-year window (Alpha, Beta, Treynor, Max_DD, Calmar)
MIN_HISTORY = 12    # minimum months required to attempt any computation
# LOWERED FROM 36 ON 2026-08-07. At 36 the floor rejected 393 funds of 1,379 --
# every fund launched inside three years, which in this market is most of the
# thematic, momentum, multi-asset and arbitrage universe, plus whole categories
# where the newest funds are the only ones anyone is choosing between.
#
# NOTHING DOWNSTREAM BREAKS, because _metrics_for_window already returns {} for
# any window under 12 months and each window slices iloc[-N:], which for a
# short fund is simply all of its data. A 20-month fund therefore reports the
# SAME numbers under 3Y, 5Y, 7Y and 10Y.
#
# WHICH IS THE THING TO BE CAREFUL ABOUT. Those columns are labelled by window
# and computed on whatever exists, so a "Sharpe_5Y" on a 20-month fund is a
# 20-month Sharpe wearing a five-year label. History_Months is the column that
# says so, and any ranking that treats the two as comparable is comparing a
# fund through one market to a fund through three.

# ── Overnight fund Direct-Growth AMFI codes (source of dynamic Rf) ───────────
# SEBI notified the Overnight Fund category in Oct 2018; most funds launched
# Jan-Jun 2019. Any code whose NAV history starts before OVERNIGHT_MIN_START
# is automatically rejected in get_rf_monthly() to guard against stale codes.
#
# CORRECTED from earlier wrong codes:
#   UTI  120785  → wrong (2013 history, was a liquid fund);   correct: 147960
#   HDFC 119110  → wrong (2013 history, was a liquid fund);   correct: 147586
#   SBI  119833  → wrong (2013 history, was a liquid fund);   correct: 147623
#   ICICI 145536 → wrong (equity fund, 67% CAGR);             correct: 145479
OVERNIGHT_CODES = {
    "Axis Overnight Fund":                  146675,
    "DSP Overnight Fund":                   146062,
    "Nippon India Overnight Fund":          145810,
    "Tata Overnight Fund":                  146980,
    "Kotak Overnight Fund":                 146141,
    "HSBC Overnight Fund":                  147287,
    "Aditya Birla Sun Life Overnight Fund": 145486,
    "UTI Overnight Fund":                   147960,   # corrected from 120785
    "HDFC Overnight Fund":                  147586,   # corrected from 119110
    "SBI Overnight Fund":                   147623,   # corrected from 119833
    "ICICI Prudential Overnight Fund":      145479,   # corrected from 145536
}

# Safety guard: reject any overnight fund series starting before this date
OVERNIGHT_MIN_START = pd.Timestamp("2018-06-01")

# ── RBI repo-rate fallback Rf for periods before overnight fund data ──────────
# Monthly decimal rates (= annual_rate / 12).
# Each entry is (start_year_month, monthly_rate); applies until the next entry.
# Source: RBI monetary policy history.
RBI_REPO_FALLBACK = [
    ("2000-01", 0.090 / 12),   # ~9% repo 2000-02
    ("2002-06", 0.075 / 12),   # easing to 7.5%
    ("2004-10", 0.060 / 12),   # 6% trough
    ("2006-06", 0.075 / 12),   # hiking cycle
    ("2008-10", 0.070 / 12),   # GFC easing begins (cut from 8%)
    ("2009-04", 0.050 / 12),   # post-GFC low ~5%
    ("2010-03", 0.050 / 12),
    ("2010-07", 0.065 / 12),   # tightening cycle
    ("2011-10", 0.085 / 12),   # 8.5% peak
    ("2012-04", 0.080 / 12),
    ("2013-05", 0.075 / 12),
    ("2014-01", 0.080 / 12),   # Rajan hike
    ("2015-01", 0.075 / 12),
    ("2016-04", 0.065 / 12),
    ("2017-08", 0.060 / 12),
    ("2019-02", 0.0625 / 12),
    ("2019-06", 0.0575 / 12),
    ("2019-08", 0.054 / 12),
    ("2019-10", 0.0515 / 12),
    ("2020-03", 0.044 / 12),   # COVID emergency cut
    ("2020-05", 0.040 / 12),   # held at 4% through 2021
    ("2022-05", 0.044 / 12),   # hiking cycle begins
    ("2022-06", 0.049 / 12),
    ("2022-08", 0.059 / 12),
    ("2023-02", 0.065 / 12),   # 6.5% – held; overnight fund data takes over
]

# ── Fund-type → category keywords for validation ─────────────────────────────
CATEGORY_KEYWORDS = {
    "Overnight":                    ["overnight"],
    "Liquid":                       ["liquid"],
    "Ultra Short Duration":         ["ultra short"],
    "Low Duration":                 ["low duration"],
    "Money Market":                 ["money market"],
    "Short Duration":               ["short duration"],
    "Medium Duration":              ["medium duration"],
    "Medium to Long Duration":      ["medium to long", "medium & long"],
    "Long Duration":                ["long duration"],
    "Dynamic Bond":                 ["dynamic bond"],
    "Gilt":                         ["gilt"],
    "Gilt with 10 year Constant Duration": ["10 year constant", "10yr constant"],
    "Banking and PSU":              ["banking and psu", "banking & psu"],
    "Corporate Bond":               ["corporate bond"],
    "Credit Risk":                  ["credit risk"],
    "Floater":                      ["floater", "floating rate"],
    "Target Maturity":              ["target maturity", "fixed maturity"],
    "Tax Efficient Income":         ["tax", "arbitrage", "savings"],
    "Aggressive Hybrid":            ["aggressive hybrid"],
    "Conservative Hybrid":          ["conservative hybrid"],
    "Dynamic Asset Allocation":     ["dynamic asset allocation", "balanced advantage",
                                     "dynamic asset alloc"],
    "Multi Asset Allocation":       ["multi asset"],
    "Equity Savings":               ["equity savings"],
    "Retirement Solutions":         ["retirement"],
    "Arbitrage":                    ["arbitrage"],
}

# ── Fund types that use debt/hybrid benchmark ─────────────────────────────────
DEBT_TYPES = {
    "overnight", "liquid", "ultra short duration", "low duration",
    "money market", "short duration", "medium duration",
    "medium to long duration", "long duration", "dynamic bond",
    "gilt", "gilt with 10 year constant duration",
    "banking and psu", "corporate bond", "credit risk", "floater",
    "target maturity", "tax efficient income",
}
EQUITY_TYPES   = {"equity",}
HYBRID_TYPES   = {
    "hybrid", "dynamic asset allocation", "multi asset allocation",
    "aggressive hybrid", "conservative hybrid",
    "retirement solutions", "equity savings", "arbitrage",
}

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

log = logging.getLogger(__name__)


def configure_logging(logfile: str = str(LOGS_DIR / "fund_analyser.log")) -> None:
    """Install console + file logging.  Called from main(), never on import.

    This ran at module level until it was found to break any importer.
    `io.TextIOWrapper(sys.stdout.buffer, ...)` takes OWNERSHIP of the buffer
    and closes it when garbage-collected; under pytest, sys.stdout is a
    per-test capture object whose .buffer is a temp file pytest still needs,
    so `import get_funds_data` closed pytest's capture and every subsequent
    test errored with "ValueError: I/O operation on closed file".

    The trigger was building the handlers list, not configuring logging:
    basicConfig() is a no-op once the root logger has handlers, but the
    wrapper is constructed regardless, and constructing it is what seizes the
    buffer.  Importing also reconfigured root logging process-wide and wrote
    fund_analyser.log into whatever the cwd happened to be.  It now defaults
    to data/logs/fund_analyser.log.

    monte_carlo._fetch_debt_index_annual_returns() imports this module to
    reach the embedded debt-index table, so "anyone who imports it" includes
    the Monte Carlo path.

    Apart from the log file's location, the body below is unchanged from the
    original.
    """
    ensure_parent(logfile)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(
                io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
                if hasattr(sys.stdout, "buffer") else sys.stdout
            ),
            logging.FileHandler(logfile, mode="w", encoding="utf-8"),
        ],
        force=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP HELPER
# ═══════════════════════════════════════════════════════════════════════════════

_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
})


def _get(url: str) -> requests.Response:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = _session.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = RETRY_BACKOFF ** attempt
            log.debug('Attempt %d failed for %s: %s — retrying in %.1fs',
                      attempt, url, exc, wait)
            time.sleep(wait)


def _get_json(url: str):
    return _get(url).json()


# ═══════════════════════════════════════════════════════════════════════════════
# AMFI NAVAll.txt PARSER
# ═══════════════════════════════════════════════════════════════════════════════

_amfi_master: Optional[pd.DataFrame] = None   # loaded once


def _normalise(text: str) -> str:
    """Lowercase, remove punctuation/extra spaces for fuzzy matching."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _load_amfi_master() -> pd.DataFrame:
    """
    Download and parse AMFI NAVAll.txt into a DataFrame with columns:
      scheme_code, scheme_name, nav, date, amc, category,
      is_direct, is_growth, norm_base  (normalised base name without plan/option)
    """
    global _amfi_master
    if _amfi_master is not None:
        return _amfi_master

    log.info("Downloading AMFI NAVAll.txt master file...")
    try:
        resp = _get(AMFI_NAV_ALL_URL)
        content = resp.text
    except Exception as exc:
        log.error("Failed to download AMFI master file: %s", exc)
        _amfi_master = pd.DataFrame()
        return _amfi_master

    rows = []
    current_category = ""
    current_amc = ""

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        # Category header lines
        if line.startswith("Open Ended") or line.startswith("Close Ended") or line.startswith("Interval"):
            current_category = line
            current_amc = ""
            continue

        # AMC name lines (no semicolons, not a header)
        if ";" not in line and not line.startswith("Scheme Code"):
            current_amc = line
            continue

        # Data lines
        parts = line.split(";")
        if len(parts) < 6:
            continue
        try:
            code = int(parts[0].strip())
        except ValueError:
            continue

        scheme_name = parts[3].strip()
        name_lower  = scheme_name.lower()

        is_direct = ("direct" in name_lower)
        is_growth = (
            "growth" in name_lower
            and "idcw" not in name_lower
            and "dividend" not in name_lower
            and "bonus" not in name_lower
            and "payout" not in name_lower
            and "reinvest" not in name_lower
        )

        # Strip plan/option suffixes to get base fund name
        base = re.sub(
            r"\s*[-–]\s*(direct|regular|retail|monthly|daily|weekly|quarterly|"
            r"annual|growth|idcw|dividend|bonus|payout|reinvest|plan|option|"
            r"series|sr|tranche|yr|year|fortnightly|half.?yearly|standard|"
            r"instant\s*access|deposit|sweep).*$",
            "", name_lower, flags=re.IGNORECASE
        ).strip()

        rows.append({
            "scheme_code": code,
            "scheme_name": scheme_name,
            "amc":         current_amc,
            "category":    current_category,
            "is_direct":   is_direct,
            "is_growth":   is_growth,
            "norm_base":   _normalise(base),
        })

    _amfi_master = pd.DataFrame(rows)
    log.info("AMFI master loaded: %d scheme rows, %d unique codes",
             len(_amfi_master), _amfi_master["scheme_code"].nunique())
    return _amfi_master


# ── Plan-priority picker ──────────────────────────────────────────────────────

def _pick_best_scheme(candidates: pd.DataFrame) -> Optional[pd.Series]:
    """
    From a set of matching rows, pick in priority:
      Direct-Growth > Direct-only > Regular-Growth > Regular-only
    """
    if candidates.empty:
        return None

    for is_direct, is_growth in [(True, True), (True, False), (False, True), (False, False)]:
        sub = candidates[
            (candidates["is_direct"] == is_direct) &
            (candidates["is_growth"] == is_growth)
        ]
        if not sub.empty:
            return sub.iloc[0]

    return candidates.iloc[0]


# ═══════════════════════════════════════════════════════════════════════════════
# CODE RESOLUTION  (replaces old _search_amfi_code)
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_amfi_code(fund_name: str, fund_type: str) -> tuple[Optional[str], str, str]:
    """
    Resolve fund_name to an AMFI scheme code using the NAVAll.txt master.

    Returns
    -------
    (amfi_code, matched_scheme_name, match_quality)
    match_quality: "exact" | "fuzzy" | "none"
    """
    master = _load_amfi_master()
    if master.empty:
        return None, "", "none"

    norm_query = _normalise(fund_name)

    # ── Stage 1: exact normalised base-name match ─────────────────────────────
    exact = master[master["norm_base"] == norm_query]
    if not exact.empty:
        row = _pick_best_scheme(exact)
        if row is not None:
            return str(row["scheme_code"]), row["scheme_name"], "exact"

    # ── Stage 2: query words subset match (all words present in base name) ────
    stop = {"fund", "the", "a", "an", "of", "and", "india", "indian",
            "plan", "direct", "regular", "growth", "option"}
    q_words = [w for w in norm_query.split() if w not in stop and len(w) > 2]

    if q_words:
        # All query words must appear in base name
        mask = master["norm_base"].apply(
            lambda b: all(w in b for w in q_words)
        )
        subset = master[mask]
        if not subset.empty:
            row = _pick_best_scheme(subset)
            if row is not None:
                return str(row["scheme_code"]), row["scheme_name"], "fuzzy"

    # ── Stage 3: partial match – most words overlap ───────────────────────────
    if q_words:
        def word_overlap(base):
            base_words = set(base.split())
            return sum(1 for w in q_words if w in base_words)

        master_scored = master.copy()
        master_scored["_score"] = master_scored["norm_base"].apply(word_overlap)
        best_score = master_scored["_score"].max()
        if best_score >= max(2, len(q_words) - 2):
            top = master_scored[master_scored["_score"] == best_score]
            row = _pick_best_scheme(top)
            if row is not None:
                return str(row["scheme_code"]), row["scheme_name"], "partial"

    return None, "", "none"


# ═══════════════════════════════════════════════════════════════════════════════
# MAP BUILDER  (standalone utility)
# ═══════════════════════════════════════════════════════════════════════════════

def build_amfi_map(input_csv: str, output_csv: str = AMFI_MAP_FILE):
    """
    Build amfi_map.csv with (Fund Name, AMFI Code, Scheme Name, Match Quality).
    Review and fix this before running main analysis.
    """
    raw = pd.read_csv(input_csv)
    rows = []
    for _, r in raw.iterrows():
        fund_name = str(r["Fund Name"]).strip()
        fund_type = str(r.get("Fund Type", "")).strip()
        code, matched_name, quality = _resolve_amfi_code(fund_name, fund_type)
        rows.append({
            "Fund Name":    fund_name,
            "Fund Type":    fund_type,
            "AMFI Code":    code or "",
            "Matched Scheme Name": matched_name,
            "Match Quality": quality,
        })
        log.info("[%s] %s -> %s (%s)", quality.upper(), fund_name, code, matched_name)

    df = pd.DataFrame(rows)
    df.to_csv(output_csv, index=False)
    log.info("AMFI map saved: %s", output_csv)

    # Summary
    counts = df["Match Quality"].value_counts()
    log.info("Match summary: %s", dict(counts))
    log.info("REVIEW the map CSV, correct wrong codes, then re-run without --build-map-only")
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# NAV FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def _nav_history_frame(amfi_code: str) -> pd.DataFrame:
    """Download NAV history and return the corrected DAILY frame.

    Extracted from fetch_nav so fetch_nav and fetch_nav_daily can share it.
    That logic is subtle, it carries a hand-maintained table of scheme codes,
    and a second copy would drift the moment either was edited.
    """
    data    = _get_json(f"{MFAPI_BASE}/{amfi_code}")
    records = data.get("data", [])
    if not records:
        raise ValueError(f"No NAV data for AMFI code {amfi_code}")

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], dayfirst=True)
    df["nav"]  = pd.to_numeric(df["nav"], errors="coerce")
    df = df.dropna(subset=["nav"]).sort_values("date")

    # A NAV OF ZERO IS A FEED ARTEFACT, NOT A PRICE, and dropna does not
    # remove it. Left in, it does double damage: the split detector below sees
    # a ratio of 0.000, reads it as a 100:1 split, rescales all the GOOD
    # history before it by 1/100 -- and then breaks, spending the fund's one
    # permitted fix so that a genuine consolidation later is never repaired.
    #
    # SBI Overnight (101206) is exactly this. A single zero on 2006-07-29
    # shielded a real 100x consolidation on 2012-01-13, and the fund came out
    # of the corpus at 34% CAGR with 2226% volatility.
    df = df[df["nav"] > 0]

    if df.empty:
        raise ValueError(f"NAV series empty after cleaning for code {amfi_code}")

    # ── NAV base adjustments for known unit-split discontinuities ─────────
    # Some funds underwent unit consolidation/split where the MFAPI NAV series
    # has a discontinuity (e.g. NAV jumps from ~24 to ~2455 overnight).
    # We normalise pre-split NAVs to the post-split base so that the entire
    # series is continuous and return calculations are correct.
    # Format: amfi_code → (cutoff_date, multiplier_for_navs_before_cutoff)
    _NAV_BASE_FIXES: dict[str, tuple[str, float]] = {
        # ── Overnight / Liquid fund consolidations (low→high NAV) ─────
        # Pre-split NAVs must be scaled UP to match post-consolidation base
        "120785": ("2018-05-03", 100),    # UTI Overnight Fund
        "140196": ("2017-07-02", 100),    # Edelweiss Liquid Fund
        "145536": ("2022-08-17",  10),    # ICICI Prudential Overnight Fund
        # ── Gold ETF face-value splits (high→low NAV) ────────────────
        # Pre-split NAVs must be scaled DOWN to match post-split base
        "113049": ("2021-02-22", 1/100),  # HDFC Gold ETF
        "105463": ("2021-03-30", 1/100),  # UTI Gold Exchange Traded Fund
        "111954": ("2022-01-10", 1/100),  # SBI Gold ETF
        "115127": ("2021-11-29", 1/100),  # Aditya Birla Sun Life Gold ETF
        "106193": ("2021-07-23", 1/10),   # Kotak Gold ETF
        "113434": ("2020-07-27", 1/100),  # Axis Gold ETF
        # ── Index ETF face-value splits (high→low NAV) ───────────────
        "112351": ("2017-07-28", 1/10),   # Kotak Nifty 50 ETF
        "135320": ("2023-09-26", 1/10),   # UTI Nifty 50 ETF
        "135853": ("2021-02-22", 1/10),   # HDFC Nifty 50 ETF
        "115512": ("2021-11-29", 1/10),   # ABSL Nifty 50 ETF
    }
    code_str = str(amfi_code)
    if code_str in _NAV_BASE_FIXES:
        cutoff_str, multiplier = _NAV_BASE_FIXES[code_str]
        cutoff = pd.Timestamp(cutoff_str)
        mask = df["date"] < cutoff
        df.loc[mask, "nav"] = df.loc[mask, "nav"] * multiplier
        log.debug("NAV base fix applied for code %s: ×%.4f before %s (%d rows adjusted)",
                  code_str, multiplier, cutoff_str, mask.sum())

    # ── Auto-detect remaining splits: single-day NAV change > 60% ───────
    # Safety net for ETFs not yet in the manual table.
    # Handles both splits (high→low, ratio < 0.40) and consolidations
    # (low→high, ratio > 2.5).
    # Skip auto-detect if a manual fix was already applied.
    # RESCANNED UNTIL CLEAN, not once. The loop used to break after the first
    # correction, so a fund with two discontinuities kept the second. Each pass
    # fixes the earliest remaining one and rescans, because scaling the rows
    # before a cutoff changes every ratio at and before it. Capped so that a
    # pathological series cannot spin.
    _MAX_AUTO_FIXES = 6
    for _auto_pass in range(_MAX_AUTO_FIXES if code_str not in _NAV_BASE_FIXES
                            else 0):
        navs = df["nav"].tolist()
        dates = df["date"].tolist()
        _fixed_this_pass = False
        for i in range(1, len(navs)):
            if navs[i - 1] > 0:
                ratio = navs[i] / navs[i - 1]
                if ratio < 0.40:                          # Split: NAV fell > 60%
                    if ratio < 0.02:
                        multiplier = 1 / 100
                    elif ratio < 0.15:
                        multiplier = 1 / 10
                    else:
                        multiplier = ratio
                    cutoff = pd.Timestamp(dates[i])
                    pre = df["date"] < cutoff
                    df.loc[pre, "nav"] = df.loc[pre, "nav"] * multiplier
                    log.warning(
                        "AUTO split-fix for code %s: NAV dropped %.1f%% on %s "
                        "→ applied ×%.4f to %d pre-split rows",
                        code_str, (1 - ratio) * 100,
                        str(cutoff.date()), multiplier, pre.sum()
                    )
                    _fixed_this_pass = True
                    break                        # rescan from the top
                elif ratio > 2.5:                         # Consolidation
                    if ratio > 50:
                        multiplier = 100
                    elif ratio > 5:
                        multiplier = 10
                    else:
                        multiplier = ratio
                    cutoff = pd.Timestamp(dates[i])
                    pre = df["date"] < cutoff
                    df.loc[pre, "nav"] = df.loc[pre, "nav"] * multiplier
                    log.warning(
                        "AUTO consolidation-fix for code %s: NAV jumped %.0fx "
                        "on %s → applied ×%.1f to %d pre-split rows",
                        code_str, ratio, str(cutoff.date()),
                        multiplier, pre.sum()
                    )
                    _fixed_this_pass = True
                    break                        # rescan from the top

        if not _fixed_this_pass:
            break

    return df


def fetch_nav(amfi_code: str) -> pd.DataFrame:
    """Download NAV history; return monthly EOM DataFrame with [nav, ret]."""
    df = _nav_history_frame(amfi_code)
    eom = df.set_index("date")["nav"].resample("ME").last().dropna()
    ret = eom.pct_change().dropna()
    return pd.DataFrame({"nav": eom, "ret": ret}).dropna()


def fetch_nav_daily(amfi_code: str) -> pd.DataFrame:
    """Download NAV history; return the DAILY series as [date, nav].

    NEW, and additive: nothing that existed calls it. Same corrections as
    fetch_nav, because both go through _nav_history_frame -- the only
    difference is that this one does not resample to month end.
    """
    return _nav_history_frame(amfi_code)[["date", "nav"]].reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# DYNAMIC RISK-FREE RATE  (overnight fund NAV-based)
# ═══════════════════════════════════════════════════════════════════════════════

_rf_cache: Optional[pd.Series] = None


def _build_rbi_fallback(end: pd.Timestamp) -> pd.Series:
    """
    Construct a monthly Rf series from 2000-01 up to (but not including) `end`,
    using the stepped RBI repo-rate table.
    """
    idx  = pd.date_range("2000-01-31", end, freq="ME")
    steps = sorted(
        [(pd.Period(m, "M"), r) for m, r in RBI_REPO_FALLBACK],
        key=lambda x: x[0],
    )
    vals = []
    for dt in idx:
        p    = dt.to_period("M")
        rate = steps[0][1]
        for sp, sr in steps:
            if p >= sp:
                rate = sr
        vals.append(rate)
    return pd.Series(vals, index=idx)


def get_rf_monthly() -> pd.Series:
    """
    Return a complete monthly Rf series from 2000-01 to today.

    Method:
      1. Download NAV history for every overnight fund in OVERNIGHT_CODES.
      2. Convert to EOM monthly returns; take the cross-sectional mean each
         month → overnight_rf(t).  This gives ~2019-onward.
      3. Stitch with RBI repo-rate fallback for months before overnight data.
    """
    global _rf_cache
    if _rf_cache is not None:
        return _rf_cache

    log.info("Building dynamic Rf from %d overnight funds ...", len(OVERNIGHT_CODES))

    series_list = []
    for fname, code in OVERNIGHT_CODES.items():
        try:
            data    = _get_json(f"{MFAPI_BASE}/{code}")
            records = data.get("data", [])
            if not records:
                continue
            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(df["date"], dayfirst=True)
            df["nav"]  = pd.to_numeric(df["nav"], errors="coerce")
            df = df.dropna(subset=["nav"]).sort_values("date")
            eom = df.set_index("date")["nav"].resample("ME").last().dropna()

            # Guard: reject if data starts before overnight category existed
            if eom.index.min() < OVERNIGHT_MIN_START:
                log.warning(
                    "Rf: REJECTED %-45s (code %d) – data starts %s, "
                    "predates overnight category. Wrong code?",
                    fname, code, eom.index.min().strftime("%Y-%m")
                )
                continue

            ret = eom.pct_change().dropna()
            # Drop any inf values caused by zero-NAV data errors in the source
            ret = ret.replace([np.inf, -np.inf], np.nan).dropna()
            if ret.empty:
                log.warning("Rf: skipping %s (code %d) – no valid returns after cleaning", fname, code)
                continue
            series_list.append(ret.rename(fname))
            log.debug("Rf: loaded %-45s  (%d months from %s)",
                      fname, len(ret), ret.index.min().strftime("%Y-%m"))
        except Exception as exc:
            log.warning("Rf: could not load %s (code %d): %s", fname, code, exc)

    if series_list:
        overnight_rf  = pd.concat(series_list, axis=1, sort=True).mean(axis=1).sort_index()
        # Sanitize: cross-sectional mean can still produce inf if any fund had an inf month
        overnight_rf  = overnight_rf.replace([np.inf, -np.inf], np.nan).dropna()
        cutover       = overnight_rf.index.min()
        log.info("Overnight Rf: %d months, %s → %s",
                 len(overnight_rf),
                 cutover.strftime("%Y-%m"),
                 overnight_rf.index.max().strftime("%Y-%m"))
    else:
        log.warning("No overnight fund data – Rf will use RBI fallback only")
        overnight_rf  = pd.Series(dtype=float)
        cutover       = pd.Timestamp.today()

    # Fallback covers everything before first overnight month
    fallback_end = cutover - pd.offsets.MonthEnd(1)
    if fallback_end >= pd.Timestamp("2000-01-31"):
        fallback = _build_rbi_fallback(fallback_end)
    else:
        fallback = pd.Series(dtype=float)

    # Exclude empty entries before concatenating. pandas is deprecating the
    # behaviour where empties are ignored when working out the result dtype,
    # and either side is legitimately empty here -- no overnight fund data,
    # or a cutover earlier than 2000. Concatenating only the non-empty parts
    # is what the deprecation notice asks for and is exactly equivalent:
    # pd.concat of two empty float Series and pd.Series(dtype=float) are the
    # same object for every use below.
    _parts    = [s for s in (fallback, overnight_rf) if not s.empty]
    rf_full   = (pd.concat(_parts) if _parts
                 else pd.Series(dtype=float)).sort_index()
    rf_full   = rf_full[~rf_full.index.duplicated(keep="last")]
    # Final safety: ensure no inf values remain (would corrupt all downstream Sharpe/Sortino)
    n_bad = np.isinf(rf_full).sum()
    if n_bad:
        log.warning("Rf: dropping %d inf values from rf_full before caching", n_bad)
        rf_full = rf_full.replace([np.inf, -np.inf], np.nan).dropna()
    _rf_cache = rf_full
    log.info("Full Rf series: %d months (%s → %s)",
             len(rf_full),
             rf_full.index.min().strftime("%Y-%m"),
             rf_full.index.max().strftime("%Y-%m"))
    return _rf_cache


# ═══════════════════════════════════════════════════════════════════════════════
# EQUITY / DEBT BENCHMARKS  (for Alpha, Beta, Treynor)
# ═══════════════════════════════════════════════════════════════════════════════

_nifty_cache:   Optional[pd.Series] = None
_debt_bm_cache: Optional[pd.Series] = None


def get_nifty_monthly(start_date: pd.Timestamp) -> pd.Series:
    global _nifty_cache
    if not HAS_YF:
        return pd.Series(dtype=float)
    if _nifty_cache is None:
        try:
            raw = yf.download(NIFTY_TICKER, start="2000-01-01",
                              interval="1mo", auto_adjust=True, progress=False)
            close = raw["Close"].squeeze()
            _nifty_cache = close.pct_change().dropna()
            _nifty_cache.index = _nifty_cache.index.to_period("M").to_timestamp("M")
        except Exception as exc:
            log.warning("Nifty download failed: %s", exc)
            _nifty_cache = pd.Series(dtype=float)
    return _nifty_cache.loc[_nifty_cache.index >= start_date]


# ── Nifty Composite Debt Index – weekly data from investing.com ───────────────
# Source: Nifty Composite Debt Total Return Index (investing.com), weekly
# Sunday closing prices, DD-MM-YYYY format.
# Coverage: 10-Apr-2016 → 15-Feb-2026 (515 weekly rows, resample to 119 month-ends).
# For months before Apr-2016 the script falls back to the synthetic 5Y bond index.
#
# TO UPDATE: download fresh data from investing.com → paste the Date + Price
# columns as tab-separated lines into the triple-quoted block below.
# Keep the header line ("Date\tPrice") – the parser skips non-numeric rows.
# Nifty Composite Debt Index – weekly data from investing.com
# Format: (DD-MM-YYYY, price)  |  515 weekly Sundays, Apr-2016 → Feb-2026
# Parser: _parse_nifty_debt_embedded() → month-end → pct_change
# TO UPDATE: append new tuples before running.
_NIFTY_DEBT_DATA = [
    ("15-02-2026", 3056.65),
    ("08-02-2026", 3054.99),
    ("01-02-2026", 3040.73),
    ("25-01-2026", 3048.86),
    ("18-01-2026", 3046.94),
    ("11-01-2026", 3039.79),
    ("04-01-2026", 3046.56),
    ("28-12-2025", 3053.09),
    ("21-12-2025", 3050.97),
    ("14-12-2025", 3042.73),
    ("07-12-2025", 3035.64),
    ("30-11-2025", 3053.84),
    ("23-11-2025", 3043.92),
    ("16-11-2025", 3039.90),
    ("09-11-2025", 3039.81),
    ("02-11-2025", 3041.63),
    ("26-10-2025", 3035.63),
    ("19-10-2025", 3039.37),
    ("12-10-2025", 3045.40),
    ("05-10-2025", 3039.97),
    ("28-09-2025", 3034.56),
    ("21-09-2025", 3021.01),
    ("14-09-2025", 3024.00),
    ("07-09-2025", 3016.61),
    ("31-08-2025", 3009.51),
    ("24-08-2025", 2985.62),
    ("17-08-2025", 2993.46),
    ("10-08-2025", 3015.65),
    ("03-08-2025", 3022.83),
    ("27-07-2025", 3029.00),
    ("20-07-2025", 3030.84),
    ("13-07-2025", 3036.65),
    ("06-07-2025", 3029.05),
    ("29-06-2025", 3026.03),
    ("22-06-2025", 3011.88),
    ("15-06-2025", 3012.78),
    ("08-06-2025", 3011.93),
    ("01-06-2025", 3036.60),
    ("25-05-2025", 3036.31),
    ("18-05-2025", 3041.03),
    ("11-05-2025", 3036.15),
    ("04-05-2025", 3007.46),
    ("27-04-2025", 3014.88),
    ("20-04-2025", 3007.04),
    ("13-04-2025", 2996.36),
    ("06-04-2025", 2981.12),
    ("30-03-2025", 2971.19),
    ("23-03-2025", 2945.23),
    ("16-03-2025", 2929.31),
    ("09-03-2025", 2909.72),
    ("02-03-2025", 2902.82),
    ("23-02-2025", 2890.39),
    ("16-02-2025", 2891.50),
    ("09-02-2025", 2889.61),
    ("02-02-2025", 2887.75),
    ("26-01-2025", 2887.20),
    ("19-01-2025", 2882.05),
    ("12-01-2025", 2866.94),
    ("05-01-2025", 2864.66),
    ("29-12-2024", 2859.84),
    ("22-12-2024", 2853.98),
    ("15-12-2024", 2852.10),
    ("08-12-2024", 2859.47),
    ("01-12-2024", 2851.68),
    ("24-11-2024", 2848.40),
    ("17-11-2024", 2827.13),
    ("10-11-2024", 2824.90),
    ("03-11-2024", 2833.08),
    ("27-10-2024", 2827.26),
    ("20-10-2024", 2823.80),
    ("13-10-2024", 2826.65),
    ("06-10-2024", 2825.66),
    ("29-09-2024", 2816.38),
    ("22-09-2024", 2825.76),
    ("15-09-2024", 2818.48),
    ("08-09-2024", 2810.16),
    ("01-09-2024", 2793.78),
    ("25-08-2024", 2787.84),
    ("18-08-2024", 2786.09),
    ("11-08-2024", 2777.38),
    ("04-08-2024", 2771.15),
    ("28-07-2024", 2763.07),
    ("21-07-2024", 2758.59),
    ("14-07-2024", 2749.20),
    ("07-07-2024", 2739.87),
    ("30-06-2024", 2734.96),
    ("23-06-2024", 2729.88),
    ("16-06-2024", 2732.12),
    ("09-06-2024", 2722.69),
    ("02-06-2024", 2712.15),
    ("26-05-2024", 2708.87),
    ("19-05-2024", 2707.47),
    ("12-05-2024", 2694.54),
    ("05-05-2024", 2684.70),
    ("28-04-2024", 2673.82),
    ("21-04-2024", 2661.74),
    ("14-04-2024", 2655.50),
    ("07-04-2024", 2658.69),
    ("31-03-2024", 2669.30),
    ("24-03-2024", 2677.92),
    ("17-03-2024", 2669.29),
    ("10-03-2024", 2667.69),
    ("03-03-2024", 2669.07),
    ("25-02-2024", 2659.14),
    ("18-02-2024", 2655.33),
    ("11-02-2024", 2650.52),
    ("04-02-2024", 2643.56),
    ("28-01-2024", 2644.05),
    ("21-01-2024", 2619.55),
    ("14-01-2024", 2613.84),
    ("07-01-2024", 2610.04),
    ("31-12-2023", 2597.05),
    ("24-12-2023", 2600.81),
    ("17-12-2023", 2596.66),
    ("10-12-2023", 2596.94),
    ("03-12-2023", 2576.52),
    ("26-11-2023", 2566.24),
    ("19-11-2023", 2565.68),
    ("12-11-2023", 2573.09),
    ("05-11-2023", 2555.18),
    ("29-10-2023", 2549.10),
    ("22-10-2023", 2540.92),
    ("15-10-2023", 2533.80),
    ("08-10-2023", 2537.18),
    ("01-10-2023", 2530.47),
    ("24-09-2023", 2548.25),
    ("17-09-2023", 2556.90),
    ("10-09-2023", 2553.01),
    ("03-09-2023", 2546.44),
    ("27-08-2023", 2545.00),
    ("20-08-2023", 2537.94),
    ("13-08-2023", 2529.32),
    ("06-08-2023", 2530.82),
    ("30-07-2023", 2525.24),
    ("23-07-2023", 2530.55),
    ("16-07-2023", 2533.63),
    ("09-07-2023", 2532.78),
    ("02-07-2023", 2517.41),
    ("25-06-2023", 2520.45),
    ("18-06-2023", 2523.79),
    ("11-06-2023", 2526.04),
    ("04-06-2023", 2526.24),
    ("28-05-2023", 2531.06),
    ("21-05-2023", 2527.38),
    ("14-05-2023", 2528.38),
    ("07-05-2023", 2515.46),
    ("30-04-2023", 2515.40),
    ("23-04-2023", 2496.71),
    ("16-04-2023", 2487.69),
    ("09-04-2023", 2476.69),
    ("02-04-2023", 2475.34),
    ("26-03-2023", 2458.62),
    ("19-03-2023", 2456.06),
    ("12-03-2023", 2444.66),
    ("05-03-2023", 2430.53),
    ("26-02-2023", 2429.76),
    ("19-02-2023", 2426.62),
    ("12-02-2023", 2425.99),
    ("05-02-2023", 2425.88),
    ("29-01-2023", 2436.61),
    ("22-01-2023", 2420.37),
    ("15-01-2023", 2424.06),
    ("08-01-2023", 2425.77),
    ("01-01-2023", 2408.25),
    ("25-12-2022", 2409.66),
    ("18-12-2022", 2409.73),
    ("11-12-2022", 2411.98),
    ("04-12-2022", 2403.83),
    ("27-11-2022", 2412.88),
    ("20-11-2022", 2398.30),
    ("13-11-2022", 2392.69),
    ("06-11-2022", 2388.22),
    ("30-10-2022", 2360.24),
    ("23-10-2022", 2368.18),
    ("16-10-2022", 2351.05),
    ("09-10-2022", 2351.08),
    ("02-10-2022", 2351.11),
    ("25-09-2022", 2361.41),
    ("18-09-2022", 2353.39),
    ("11-09-2022", 2374.50),
    ("04-09-2022", 2385.46),
    ("28-08-2022", 2369.88),
    ("21-08-2022", 2369.23),
    ("14-08-2022", 2358.92),
    ("07-08-2022", 2351.10),
    ("31-07-2022", 2345.99),
    ("24-07-2022", 2337.99),
    ("17-07-2022", 2322.96),
    ("10-07-2022", 2320.97),
    ("03-07-2022", 2317.40),
    ("26-06-2022", 2310.49),
    ("19-06-2022", 2304.19),
    ("12-06-2022", 2284.52),
    ("05-06-2022", 2285.15),
    ("29-05-2022", 2288.53),
    ("22-05-2022", 2301.33),
    ("15-05-2022", 2297.39),
    ("08-05-2022", 2301.65),
    ("01-05-2022", 2276.42),
    ("24-04-2022", 2326.20),
    ("17-04-2022", 2320.88),
    ("10-04-2022", 2308.04),
    ("03-04-2022", 2331.44),
    ("27-03-2022", 2360.70),
    ("20-03-2022", 2357.73),
    ("13-03-2022", 2357.53),
    ("06-03-2022", 2344.18),
    ("27-02-2022", 2347.71),
    ("20-02-2022", 2357.26),
    ("13-02-2022", 2363.76),
    ("06-02-2022", 2357.32),
    ("30-01-2022", 2314.21),
    ("23-01-2022", 2333.30),
    ("16-01-2022", 2345.24),
    ("09-01-2022", 2348.47),
    ("02-01-2022", 2354.26),
    ("26-12-2021", 2364.47),
    ("19-12-2021", 2361.17),
    ("12-12-2021", 2372.65),
    ("05-12-2021", 2376.00),
    ("28-11-2021", 2372.88),
    ("21-11-2021", 2373.62),
    ("14-11-2021", 2368.27),
    ("07-11-2021", 2360.55),
    ("31-10-2021", 2353.73),
    ("24-10-2021", 2349.54),
    ("17-10-2021", 2344.85),
    ("10-10-2021", 2346.35),
    ("03-10-2021", 2345.63),
    ("26-09-2021", 2352.46),
    ("19-09-2021", 2356.31),
    ("12-09-2021", 2357.19),
    ("05-09-2021", 2350.19),
    ("29-08-2021", 2350.16),
    ("22-08-2021", 2332.62),
    ("15-08-2021", 2327.55),
    ("08-08-2021", 2319.51),
    ("01-08-2021", 2312.30),
    ("25-07-2021", 2313.58),
    ("18-07-2021", 2315.82),
    ("11-07-2021", 2311.48),
    ("04-07-2021", 2309.08),
    ("27-06-2021", 2311.53),
    ("20-06-2021", 2311.53),
    ("13-06-2021", 2314.98),
    ("06-06-2021", 2324.05),
    ("30-05-2021", 2312.00),
    ("23-05-2021", 2316.97),
    ("16-05-2021", 2316.51),
    ("09-05-2021", 2306.32),
    ("02-05-2021", 2306.02),
    ("25-04-2021", 2298.59),
    ("18-04-2021", 2290.56),
    ("11-04-2021", 2281.31),
    ("04-04-2021", 2291.45),
    ("28-03-2021", 2275.00),
    ("21-03-2021", 2274.14),
    ("14-03-2021", 2258.79),
    ("07-03-2021", 2246.87),
    ("28-02-2021", 2251.06),
    ("21-02-2021", 2250.64),
    ("14-02-2021", 2266.06),
    ("07-02-2021", 2278.98),
    ("31-01-2021", 2267.51),
    ("24-01-2021", 2297.17),
    ("17-01-2021", 2294.59),
    ("10-01-2021", 2292.26),
    ("03-01-2021", 2306.24),
    ("27-12-2020", 2303.06),
    ("20-12-2020", 2290.69),
    ("13-12-2020", 2291.65),
    ("06-12-2020", 2288.94),
    ("29-11-2020", 2293.71),
    ("22-11-2020", 2288.28),
    ("15-11-2020", 2288.24),
    ("08-11-2020", 2282.72),
    ("01-11-2020", 2281.07),
    ("25-10-2020", 2277.41),
    ("18-10-2020", 2277.94),
    ("11-10-2020", 2262.94),
    ("04-10-2020", 2262.02),
    ("27-09-2020", 2244.96),
    ("20-09-2020", 2236.20),
    ("13-09-2020", 2234.90),
    ("06-09-2020", 2231.02),
    ("30-08-2020", 2246.00),
    ("23-08-2020", 2212.93),
    ("16-08-2020", 2220.02),
    ("09-08-2020", 2238.37),
    ("02-08-2020", 2242.49),
    ("26-07-2020", 2248.52),
    ("19-07-2020", 2252.27),
    ("12-07-2020", 2251.41),
    ("05-07-2020", 2252.41),
    ("28-06-2020", 2233.55),
    ("21-06-2020", 2222.17),
    ("14-06-2020", 2222.23),
    ("07-06-2020", 2217.52),
    ("31-05-2020", 2212.24),
    ("24-05-2020", 2212.01),
    ("17-05-2020", 2213.76),
    ("10-05-2020", 2195.15),
    ("03-05-2020", 2208.68),
    ("26-04-2020", 2181.04),
    ("19-04-2020", 2173.54),
    ("12-04-2020", 2131.22),
    ("05-04-2020", 2113.34),
    ("29-03-2020", 2129.44),
    ("22-03-2020", 2139.04),
    ("15-03-2020", 2115.07),
    ("08-03-2020", 2120.46),
    ("01-03-2020", 2140.56),
    ("23-02-2020", 2116.26),
    ("16-02-2020", 2110.85),
    ("09-02-2020", 2108.26),
    ("02-02-2020", 2100.57),
    ("26-01-2020", 2069.21),
    ("19-01-2020", 2067.64),
    ("12-01-2020", 2059.88),
    ("05-01-2020", 2057.16),
    ("29-12-2019", 2067.00),
    ("22-12-2019", 2059.99),
    ("15-12-2019", 2053.92),
    ("08-12-2019", 2025.61),
    ("01-12-2019", 2036.82),
    ("24-11-2019", 2060.65),
    ("17-11-2019", 2056.11),
    ("10-11-2019", 2049.51),
    ("03-11-2019", 2041.56),
    ("27-10-2019", 2050.31),
    ("20-10-2019", 2042.06),
    ("13-10-2019", 2037.63),
    ("06-10-2019", 2033.79),
    ("29-09-2019", 2042.60),
    ("22-09-2019", 2024.73),
    ("15-09-2019", 2016.19),
    ("08-09-2019", 2035.27),
    ("01-09-2019", 2038.61),
    ("25-08-2019", 2034.35),
    ("18-08-2019", 2032.68),
    ("11-08-2019", 2028.14),
    ("04-08-2019", 2031.48),
    ("28-07-2019", 2040.07),
    ("21-07-2019", 2018.12),
    ("14-07-2019", 2039.78),
    ("07-07-2019", 2027.77),
    ("30-06-2019", 2003.15),
    ("23-06-2019", 1975.46),
    ("16-06-2019", 1975.60),
    ("09-06-2019", 1965.08),
    ("02-06-2019", 1955.76),
    ("26-05-2019", 1945.83),
    ("19-05-2019", 1923.63),
    ("12-05-2019", 1908.14),
    ("05-05-2019", 1899.56),
    ("28-04-2019", 1894.82),
    ("21-04-2019", 1888.27),
    ("14-04-2019", 1884.31),
    ("07-04-2019", 1882.87),
    ("31-03-2019", 1890.41),
    ("24-03-2019", 1892.74),
    ("17-03-2019", 1885.81),
    ("10-03-2019", 1878.67),
    ("03-03-2019", 1872.99),
    ("24-02-2019", 1868.80),
    ("17-02-2019", 1865.02),
    ("10-02-2019", 1864.27),
    ("03-02-2019", 1866.78),
    ("27-01-2019", 1852.69),
    ("20-01-2019", 1855.50),
    ("13-01-2019", 1850.71),
    ("06-01-2019", 1858.18),
    ("30-12-2018", 1854.02),
    ("23-12-2018", 1866.44),
    ("16-12-2018", 1863.85),
    ("09-12-2018", 1848.31),
    ("02-12-2018", 1843.38),
    ("25-11-2018", 1827.09),
    ("18-11-2018", 1814.34),
    ("11-11-2018", 1808.61),
    ("04-11-2018", 1806.96),
    ("28-10-2018", 1803.89),
    ("21-10-2018", 1789.86),
    ("14-10-2018", 1782.21),
    ("07-10-2018", 1773.91),
    ("30-09-2018", 1760.73),
    ("23-09-2018", 1760.54),
    ("16-09-2018", 1754.25),
    ("09-09-2018", 1746.03),
    ("02-09-2018", 1751.75),
    ("26-08-2018", 1745.44),
    ("19-08-2018", 1753.30),
    ("12-08-2018", 1758.26),
    ("05-08-2018", 1768.09),
    ("29-07-2018", 1764.40),
    ("22-07-2018", 1755.80),
    ("15-07-2018", 1752.33),
    ("08-07-2018", 1750.11),
    ("01-07-2018", 1739.18),
    ("24-06-2018", 1735.29),
    ("17-06-2018", 1741.31),
    ("10-06-2018", 1729.85),
    ("03-06-2018", 1724.47),
    ("27-05-2018", 1731.44),
    ("20-05-2018", 1730.99),
    ("13-05-2018", 1730.47),
    ("06-05-2018", 1732.67),
    ("29-04-2018", 1730.38),
    ("22-04-2018", 1725.86),
    ("15-04-2018", 1732.73),
    ("08-04-2018", 1748.38),
    ("01-04-2018", 1775.73),
    ("25-03-2018", 1753.39),
    ("18-03-2018", 1734.40),
    ("11-03-2018", 1727.06),
    ("04-03-2018", 1718.50),
    ("25-02-2018", 1709.20),
    ("18-02-2018", 1711.91),
    ("11-02-2018", 1719.56),
    ("04-02-2018", 1723.88),
    ("28-01-2018", 1712.42),
    ("21-01-2018", 1737.49),
    ("14-01-2018", 1734.77),
    ("07-01-2018", 1736.85),
    ("31-12-2017", 1751.03),
    ("24-12-2017", 1746.30),
    ("17-12-2017", 1749.46),
    ("10-12-2017", 1757.25),
    ("03-12-2017", 1760.42),
    ("26-11-2017", 1761.43),
    ("19-11-2017", 1762.42),
    ("12-11-2017", 1754.37),
    ("05-11-2017", 1761.10),
    ("29-10-2017", 1766.98),
    ("22-10-2017", 1768.09),
    ("15-10-2017", 1770.84),
    ("08-10-2017", 1768.20),
    ("01-10-2017", 1761.63),
    ("24-09-2017", 1768.26),
    ("17-09-2017", 1764.19),
    ("10-09-2017", 1771.17),
    ("03-09-2017", 1779.47),
    ("27-08-2017", 1778.71),
    ("20-08-2017", 1771.10),
    ("13-08-2017", 1771.15),
    ("06-08-2017", 1770.57),
    ("30-07-2017", 1777.00),
    ("23-07-2017", 1769.77),
    ("16-07-2017", 1772.68),
    ("09-07-2017", 1763.19),
    ("02-07-2017", 1752.96),
    ("25-06-2017", 1756.64),
    ("18-06-2017", 1761.48),
    ("11-06-2017", 1756.61),
    ("04-06-2017", 1759.24),
    ("28-05-2017", 1738.55),
    ("21-05-2017", 1731.37),
    ("14-05-2017", 1720.27),
    ("07-05-2017", 1712.27),
    ("30-04-2017", 1706.73),
    ("23-04-2017", 1703.90),
    ("16-04-2017", 1705.51),
    ("09-04-2017", 1710.85),
    ("02-04-2017", 1698.77),
    ("26-03-2017", 1707.72),
    ("19-03-2017", 1701.79),
    ("12-03-2017", 1689.46),
    ("05-03-2017", 1680.33),
    ("26-02-2017", 1689.68),
    ("19-02-2017", 1681.63),
    ("12-02-2017", 1686.06),
    ("05-02-2017", 1688.49),
    ("29-01-2017", 1732.43),
    ("22-01-2017", 1730.02),
    ("15-01-2017", 1722.51),
    ("08-01-2017", 1727.08),
    ("01-01-2017", 1726.29),
    ("25-12-2016", 1711.19),
    ("18-12-2016", 1707.40),
    ("11-12-2016", 1709.82),
    ("04-12-2016", 1716.34),
    ("27-11-2016", 1742.32),
    ("20-11-2016", 1740.75),
    ("13-11-2016", 1713.15),
    ("06-11-2016", 1686.86),
    ("30-10-2016", 1671.02),
    ("23-10-2016", 1673.23),
    ("16-10-2016", 1673.85),
    ("09-10-2016", 1670.52),
    ("02-10-2016", 1670.62),
    ("25-09-2016", 1658.53),
    ("18-09-2016", 1655.18),
    ("11-09-2016", 1645.36),
    ("04-09-2016", 1644.14),
    ("28-08-2016", 1636.28),
    ("21-08-2016", 1633.90),
    ("14-08-2016", 1631.11),
    ("07-08-2016", 1630.77),
    ("31-07-2016", 1620.75),
    ("24-07-2016", 1616.46),
    ("17-07-2016", 1606.47),
    ("10-07-2016", 1597.78),
    ("03-07-2016", 1586.15),
    ("26-06-2016", 1578.18),
    ("19-06-2016", 1567.30),
    ("12-06-2016", 1561.69),
    ("05-06-2016", 1566.51),
    ("29-05-2016", 1554.51),
    ("22-05-2016", 1556.56),
    ("15-05-2016", 1554.25),
    ("08-05-2016", 1554.21),
    ("01-05-2016", 1551.36),
    ("24-04-2016", 1549.97),
    ("17-04-2016", 1545.94),
    ("10-04-2016", 1545.28),
]


def _parse_nifty_debt_embedded() -> pd.Series:
    """
    Build a monthly Nifty Composite Debt return series from the embedded
    weekly price list, resampled to month-end closing prices.
    """
    if not _NIFTY_DEBT_DATA:
        return pd.Series(dtype=float)

    df  = pd.DataFrame(_NIFTY_DEBT_DATA, columns=["date", "price"])
    df["date"]  = pd.to_datetime(df["date"], dayfirst=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df  = df.dropna().set_index("date").sort_index()
    eom = df["price"].resample("ME").last().dropna()
    ret = eom.pct_change().dropna()
    ret = ret.replace([np.inf, -np.inf], np.nan).dropna()
    return ret


def _build_synthetic_debt_benchmark(rf: pd.Series) -> pd.Series:
    """
    Construct a synthetic 5Y bond index from the Rf series (fallback only).

    bond_return(t) = Rf(t) + term_spread - ModDur × ΔRf(t)

    Parameters: modified duration ≈ 4.5 (5-year G-Sec), term spread ≈ 65 bps p.a.
    """
    MOD_DUR     = 4.5
    TERM_SPREAD = 0.0065 / 12

    carry    = rf + TERM_SPREAD
    d_rf     = rf.diff().fillna(0)
    dur_loss = MOD_DUR * d_rf
    return (carry - dur_loss).dropna()


def get_debt_benchmark_monthly(start_date: pd.Timestamp) -> pd.Series:
    """
    Return monthly debt benchmark returns from start_date onward.

    Strategy (controlled by USE_ACTUAL_BOND_INDEX):
      True  → parse the embedded Nifty Composite Debt Index data (investing.com,
               Apr-2016 onward); stitch to the synthetic 5Y bond index for months
               before Apr-2016 so the full 2000-onward window is always available.
      False → always use the synthetic 5Y bond index.

    The stitching is done by scaling the synthetic tail so its last value
    aligns with the first actual return, preserving relative return dynamics
    in the pre-2016 window while using the real index from 2016 onward.
    """
    global _debt_bm_cache
    if _debt_bm_cache is not None:
        return _debt_bm_cache.loc[_debt_bm_cache.index >= start_date]

    rf = get_rf_monthly()

    if USE_ACTUAL_BOND_INDEX:
        actual = _parse_nifty_debt_embedded()
        if len(actual) >= 12:
            # Stitch: use synthetic for months before actual data starts,
            # then switch to actual.  No scaling needed — both are return series.
            cutover    = actual.index.min()
            synthetic  = _build_synthetic_debt_benchmark(rf)
            pre_actual = synthetic[synthetic.index < cutover]

            if not pre_actual.empty:
                combined = pd.concat([pre_actual, actual]).sort_index()
                combined = combined[~combined.index.duplicated(keep='last')]
                log.info(
                    "Debt benchmark: Nifty Composite Debt (investing.com) %s→%s "
                    "stitched to synthetic for %s→%s (%d months total)",
                    actual.index.min().strftime('%Y-%m'),
                    actual.index.max().strftime('%Y-%m'),
                    pre_actual.index.min().strftime('%Y-%m'),
                    pre_actual.index.max().strftime('%Y-%m'),
                    len(combined),
                )
                _debt_bm_cache = combined
            else:
                log.info(
                    "Debt benchmark: Nifty Composite Debt (investing.com), "
                    "%d months (%s → %s)",
                    len(actual),
                    actual.index.min().strftime('%Y-%m'),
                    actual.index.max().strftime('%Y-%m'),
                )
                _debt_bm_cache = actual
        else:
            log.warning("Debt benchmark: embedded data parse failed; using synthetic fallback")
            _debt_bm_cache = _build_synthetic_debt_benchmark(rf)
    else:
        _debt_bm_cache = _build_synthetic_debt_benchmark(rf)
        log.info(
            "Debt benchmark: synthetic 5Y bond index (USE_ACTUAL_BOND_INDEX=False), "
            "%d months (%s → %s)",
            len(_debt_bm_cache),
            _debt_bm_cache.index.min().strftime('%Y-%m'),
            _debt_bm_cache.index.max().strftime('%Y-%m'),
        )

    return _debt_bm_cache.loc[_debt_bm_cache.index >= start_date]


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def _rolling_stats(nav: pd.Series, ret: pd.Series, window_months: int) -> dict:
    """
    Compute statistics across all overlapping rolling windows of `window_months`.

    For each window of length window_months sliding one month at a time:
      - CAGR   : annualised compounded return over the window
      - Std    : annualised std dev of monthly returns within the window
      - Max_DD : maximum drawdown of the NAV within the window

    Returns a dict with keys:
      avg_cagr     : mean of all rolling CAGRs  (expected return)
      min_cagr     : worst (lowest) rolling CAGR  (worst-case return)
      avg_std_dev  : mean of all rolling std devs
      max_std_dev  : worst (highest) rolling std dev  (worst-case volatility)
      worst_max_dd : worst (most negative) max DD across all windows

    Returns an empty dict if the NAV series is shorter than window_months.
    """
    years = window_months / 12.0
    if len(ret) < window_months or len(nav) < window_months:
        return {}

    cagrs    = []
    std_devs = []
    max_dds  = []

    # Align nav onto the ret index so we can slice by integer position
    nav_aligned = nav.reindex(ret.index, method="nearest").values
    ret_arr     = ret.values
    n           = len(ret_arr)

    # Clip monthly returns to ±50% to eliminate NAV split/consolidation
    # artefacts (e.g. a unit-consolidation makes NAV jump 100× in one month,
    # producing a single +9900% monthly return that corrupts every rolling
    # window containing that month).  Real maximum monthly returns:
    #   debt ≈ ±5%,  equity ≈ ±35%,  gold ≈ ±20%
    # ±50% is a safe universal ceiling that catches splits without
    # clipping any genuine extreme market move.
    CLIP = 0.50
    ret_arr = np.clip(ret_arr, -CLIP, CLIP)

    # Rebuild a clean nav from clipped returns so CAGR and drawdown
    # are also free of the split artefact.
    nav_clean = np.ones(n)
    for _i in range(1, n):
        nav_clean[_i] = nav_clean[_i - 1] * (1 + ret_arr[_i])
    nav_aligned = nav_clean

    # First pass: collect raw values
    # One record per window, NaNs kept in place, so the four series stay
    # positionally aligned.  They used to be appended conditionally, which
    # desynced them whenever a window had a finite std but a non-finite CAGR
    # -- see patch_percentile_wiring.py for the full description.
    raw_cagrs = []
    raw_stds  = []
    raw_dds   = []
    raw_dates = []

    # Window end dates, for aligning this fund's series against other funds'
    # by calendar month.  ret.index is a monthly DatetimeIndex.
    _idx = list(ret.index)

    for start in range(0, n - window_months + 1):
        end   = start + window_months      # exclusive
        r_win = ret_arr[start:end]
        n_win = nav_aligned[start:end]

        # --- CAGR ---
        nav_s = nav_aligned[start]
        nav_e = nav_aligned[end - 1]
        if nav_s > 0 and np.isfinite(nav_s) and np.isfinite(nav_e):
            cagr = float((nav_e / nav_s) ** (1.0 / years) - 1)
        else:
            cagr = np.nan

        # --- annualised std dev ---
        sd = float(np.nanstd(r_win, ddof=1) * np.sqrt(12)) if len(r_win) >= 12 else np.nan

        # --- max drawdown ---
        if len(n_win) >= 2:
            cum_max = np.maximum.accumulate(n_win)
            safe    = np.where(cum_max > 0, cum_max, 1.0)
            dd      = float(np.min(n_win / safe - 1))
        else:
            dd = np.nan

        # Unconditional: position i is the same window in all four lists.
        raw_cagrs.append(cagr)
        raw_stds.append(sd)
        raw_dds.append(dd)
        raw_dates.append(_idx[end - 1] if end - 1 < len(_idx) else None)

    # Second pass: remove outlier windows caused by NAV split artefacts.
    # A single bad NAV data point inflates the std of every window
    # containing that month.  We identify outlier windows as those with
    # std > STD_OUTLIER_FACTOR x median(std) and discard them before
    # computing aggregates.  This is the correct fix: the ±50% return
    # clip (above) removes the artefact from nav/CAGR, but std is still
    # inflated in windows where multiple nearby months show elevated
    # volatility as the fund re-rated around the consolidation date.
    STD_OUTLIER_FACTOR = 5.0
    finite_stds = [s for s in raw_stds if np.isfinite(s)]
    if len(finite_stds) >= 4:
        med_std = float(np.median(finite_stds))
        std_ceil = STD_OUTLIER_FACTOR * med_std if med_std > 1e-9 else 0.50
        # Indices now index the SAME window in every list, so a window
        # rejected on its std really is the window whose CAGR is dropped.
        clean_idx = [i for i, s in enumerate(raw_stds)
                     if np.isfinite(s) and s <= std_ceil]
    else:
        clean_idx = list(range(len(raw_stds)))

    cagr_pairs = [(raw_dates[i], raw_cagrs[i]) for i in clean_idx
                  if np.isfinite(raw_cagrs[i])]
    cagrs      = [c for _d, c in cagr_pairs]
    cagr_dates = [d for d, _c in cagr_pairs]
    std_devs   = [raw_stds[i] for i in clean_idx if np.isfinite(raw_stds[i])]
    # Keep all DDs (drawdown less affected by the single bad point)
    max_dds    = [d for d in raw_dds if np.isfinite(d)]

    if not cagrs:
        return {}

    # Round series to 4dp and convert to % for compact JSON storage
    cagrs_pct  = [round(v * 100, 4) for v in cagrs]
    stds_pct   = [round(v * 100, 4) for v in std_devs]
    dds_pct    = [round(v * 100, 4) for v in max_dds]
    # Percentile of the rolling series: between the mean (too kind, it
    # averages the good windows in) and the minimum (one window, and one bad
    # month in the NAV history can own it).
    try:
        from swp_planner.config.configuration import config
        _pct = float(config.fund_return_percentile)
    except Exception:
        _pct = 33.0

    # Reading-count guard (HANDOVER 2.5).  A window with too few rolling
    # samples gets no percentile at all rather than a noisy one: FundEntry
    # stores four scalars and no counts, so a consumer cannot apply this rule
    # later, and expected_return() takes a MIN -- an under-resolved 10Y value
    # is selected exactly when its noise happens to be low.
    try:
        from swp_planner.simulation.percentile_metrics import MIN_READINGS as _MIN_READINGS
    except Exception:
        _MIN_READINGS = {"3Y": 24, "5Y": 18, "7Y": 12, "10Y": 12}
    _label = {36: "3Y", 60: "5Y", 84: "7Y", 120: "10Y"}.get(window_months)
    _floor = _MIN_READINGS.get(_label, 12)
    _pctile = float(np.percentile(cagrs, _pct)) if len(cagrs) >= _floor else None

    return {
        "avg_cagr":     float(np.mean(cagrs)),
        "min_cagr":     float(np.min(cagrs)),
        "pctile_cagr":  _pctile,
        "pctile_used":  _pct,
        # Window end dates, aligned 1:1 with _cagrs below.
        "_cagr_dates":  [str(d.date()) if hasattr(d, "date") else str(d)
                         for d in cagr_dates],
        "avg_std_dev":  float(np.mean(std_devs)) if std_devs else np.nan,
        "max_std_dev":  float(np.max(std_devs)) if std_devs else np.nan,
        "worst_max_dd": float(np.min(max_dds))  if max_dds  else np.nan,
        # Raw series for chart popup (in %, stored in rolling_data.json only)
        "_cagrs": cagrs_pct,
        "_stds":  stds_pct,
        "_dds":   dds_pct,
    }


def _cagr(nav: pd.Series, years: int) -> float:
    months = years * 12
    if len(nav) < months + 1:
        return np.nan
    start = nav.iloc[-(months + 1)]
    end   = nav.iloc[-1]
    if start <= 0:
        return np.nan
    return (end / start) ** (1.0 / years) - 1


def _max_drawdown(nav: pd.Series) -> float:
    roll_max = nav.cummax()
    return float(((nav / roll_max) - 1.0).min())


def _metrics_for_window(
    ret: pd.Series,         # monthly returns for this window
    nav: pd.Series,         # monthly EOM NAV for this window (for MDD)
    rf:  pd.Series,         # full Rf series (will be aligned internally)
    bm:  pd.Series,         # benchmark returns (full; aligned internally)
) -> dict:
    """
    Compute Sharpe, Sortino, Alpha, Beta, Treynor, Std_Dev, Max_DD, Calmar
    for a single time window.  Returns a dict of scalars (NaN on failure).
    """
    n = len(ret)
    if n < 12:
        return {}

    # ── Align Rf and benchmark to this window's index ─────────────────────────
    rf_w  = rf.reindex(ret.index).ffill()
    # If still any NaN (window older than Rf series start), fill with series mean
    if rf_w.isna().any():
        rf_w = rf_w.fillna(rf.mean())

    bm_w  = bm.reindex(ret.index).ffill()
    # Fallback: use Rf as benchmark if benchmark has gaps
    bm_w  = bm_w.fillna(rf_w)

    # ── Core stats ────────────────────────────────────────────────────────────
    ann_rf  = float(rf_w.mean() * 12)
    ann_ret = float(ret.mean() * 12)
    std_dev = float(ret.std() * np.sqrt(12))

    # ── Sharpe ────────────────────────────────────────────────────────────────
    sharpe = (ann_ret - ann_rf) / std_dev if std_dev > 1e-9 else np.nan

    # ── Sortino (downside = monthly returns below that month's Rf) ────────────
    # IMPORTANT: downside deviation is computed on raw returns that fall below
    # their corresponding Rf, NOT on excess returns that are negative.
    # Using excess_monthly < 0 as the threshold but computing std on the raw
    # ret values in those months avoids the Sharpe > Sortino inversion that
    # occurs for near-flat funds where excess_monthly has higher variance than
    # raw returns (because Rf subtraction adds noise to a near-constant series).
    # Epsilon floor of 0.01% annualised prevents hyper-inflation for funds with
    # prolonged bull runs where downside variance approaches zero.
    SORTINO_DS_STD_FLOOR = 0.0001   # 0.01% annualised
    downside_mask = (ret - rf_w) < 0          # months where fund underperformed Rf
    downside_ret  = ret[downside_mask]         # raw returns in those months
    ds_std_raw = float(downside_ret.std() * np.sqrt(12)) if len(downside_ret) > 1 else np.nan
    ds_std     = max(ds_std_raw, SORTINO_DS_STD_FLOOR) if (ds_std_raw is not None and np.isfinite(ds_std_raw)) else np.nan
    sortino    = (ann_ret - ann_rf) / ds_std if (ds_std and np.isfinite(ds_std)) else np.nan

    # ── Beta & Alpha ──────────────────────────────────────────────────────────
    # Guard against degenerate benchmark (e.g. Rf used as proxy → near-constant)
    # Suppress numpy RuntimeWarnings that arise before the variance check fires.
    bm_var = float(np.nanvar(bm_w.values))
    if bm_var < 1e-12 or not np.isfinite(bm_var):
        beta, alpha = np.nan, np.nan
    else:
        # Additional NaN guard: linregress emits RuntimeWarnings if inputs contain
        # NaN or all-equal values; check explicitly before calling.
        bm_vals  = bm_w.values
        ret_vals = ret.values
        valid    = np.isfinite(bm_vals) & np.isfinite(ret_vals)
        if valid.sum() < 12 or np.nanvar(bm_vals[valid]) < 1e-12:
            beta, alpha = np.nan, np.nan
        else:
            try:
                slope, *_ = stats.linregress(bm_vals[valid], ret_vals[valid])
                beta   = float(slope)
                bm_ann = float(np.nanmean(bm_vals[valid]) * 12)
                alpha  = ann_ret - (ann_rf + beta * (bm_ann - ann_rf))
            except Exception:
                beta, alpha = np.nan, np.nan

    # ── Treynor ───────────────────────────────────────────────────────────────
    treynor = (
        (ann_ret - ann_rf) / beta
        if (not np.isnan(beta) and abs(beta) > 1e-9) else np.nan
    )

    # ── Max Drawdown & Calmar ─────────────────────────────────────────────────
    # Calmar = CAGR / abs(Max_Drawdown)
    # We use true CAGR (compounded) over the window, NOT arithmetic mean × 12.
    # For arbitrage / near-zero-volatility funds the difference is negligible,
    # but for equity/credit funds arithmetic mean can overstate annualised return
    # significantly, inflating Calmar.
    mdd = _max_drawdown(nav)

    # Floor: if |Max_DD| < 0.0001 (0.01%), treat as -0.0001 for Calmar.
    # Near-zero drawdowns (e.g. -0.0001 for arbitrage funds) produce
    # extreme Calmar values (800+) that distort Combined_Ratio.
    # 0.01% matches the Sortino denominator floor for consistency.
    mdd_for_calmar = mdd if abs(mdd) >= 0.0001 else -0.0001

    n_years = len(nav) / 12.0
    if len(nav) >= 2 and nav.iloc[0] > 0 and n_years > 0:
        cagr_window = float((nav.iloc[-1] / nav.iloc[0]) ** (1.0 / n_years) - 1)
    else:
        cagr_window = np.nan
    calmar = (abs(cagr_window) / abs(mdd_for_calmar)
              if (mdd_for_calmar < -1e-9 and np.isfinite(cagr_window)) else np.nan)

    def _r(v):
        if v is None: return np.nan
        f = float(v)
        return round(f, 4) if (np.isfinite(f)) else np.nan

    return {
        "Std_Dev": _r(std_dev),
        "Sharpe":  _r(sharpe),
        "Sortino": _r(sortino),
        "Alpha":   _r(alpha),
        "Beta":    _r(beta),
        "Treynor": _r(treynor),
        "Max_DD":  _r(mdd),
        "Calmar":  _r(calmar),
    }


def compute_metrics(eom: pd.DataFrame, fund_type: str) -> dict:
    """
    Compute all metrics across three explicit lookback windows and return a flat dict.

    Window design
    -------------
    Three windows are always attempted: 10Y (120 months), 5Y (60 months), 3Y (36 months).
    Each window only produces output if the fund has at least that much history.
    A window that falls back to fewer months than requested still uses whatever
    data is available (e.g. a 90-month fund fills the 5Y window with 60 months
    and the 10Y window with all 90 months it has).

    Per-window columns (5 metrics × 3 windows = 15 columns):
        Std_Dev_10Y, Sharpe_10Y, Sortino_10Y, Max_DD_10Y, Calmar_10Y
        Std_Dev_5Y,  Sharpe_5Y,  Sortino_5Y,  Max_DD_5Y,  Calmar_5Y
        Std_Dev_3Y,  Sharpe_3Y,  Sortino_3Y,  Max_DD_3Y,  Calmar_3Y
    A metric is NaN for a given window when the fund does not yet have enough
    history to populate that window (minimum 12 months for any window).  A
    fund shorter than a window reports that window computed on all the data it
    has, so a 20-month fund shows identical 3Y, 5Y, 7Y and 10Y figures --
    History_Months is what distinguishes them.

    Alpha, Beta, Treynor are computed once on the longest available window
    (up to 10Y) for regression stability.

    Combined_Ratio_XY = sqrt(Sortino_XY × Calmar_XY), only when both > 0.

    Minimum history: MIN_HISTORY months (12). Funds below this raise ValueError.
    """
    nav = eom["nav"]
    ret = eom["ret"].dropna()

    n_months = len(ret)
    if n_months < MIN_HISTORY:
        raise ValueError(
            f"Only {n_months} monthly return(s) – need at least "
            f"{MIN_HISTORY} ({MIN_HISTORY / 12:.0f} year(s))"
        )

    ft         = fund_type.strip().lower()
    start_date = ret.index.min()

    # ── Get Rf series ─────────────────────────────────────────────────────────
    rf_all = get_rf_monthly()

    # ── Select benchmark ──────────────────────────────────────────────────────
    if ft in EQUITY_TYPES:
        bm_raw = get_nifty_monthly(start_date)
        if bm_raw.empty:
            bm_raw = rf_all
    elif ft in HYBRID_TYPES:
        nifty = get_nifty_monthly(start_date)
        debt  = get_debt_benchmark_monthly(start_date)
        if nifty.empty:
            bm_raw = rf_all
        else:
            n = nifty.reindex(ret.index).ffill().fillna(0)
            d = debt.reindex(ret.index).ffill().fillna(
                rf_all.reindex(ret.index).ffill().fillna(rf_all.mean()))
            bm_raw = HYBRID_EQ_WEIGHT * n + HYBRID_DEBT_WEIGHT * d
    else:
        bm_raw = get_debt_benchmark_monthly(start_date)

    # ── Compute the three windows ─────────────────────────────────────────────
    # Each window slices the last N months.  If the fund is shorter than N,
    # the slice naturally returns all available data (pandas iloc[-N:] behaviour).
    # We still run the window; the result is based on whatever data exists.
    # Windows with fewer than 12 months return empty dicts (handled in
    # _metrics_for_window).

    WINDOW_3Y = 36

    windows = {}
    for label, n_win in [("10Y", WINDOW_10Y), ("7Y", WINDOW_7Y),
                         ("5Y", WINDOW_5Y),  ("3Y", WINDOW_3Y)]:
        ret_w = ret.iloc[-n_win:]
        nav_w = nav.loc[nav.index >= ret_w.index.min()]
        windows[label] = _metrics_for_window(ret_w, nav_w, rf_all, bm_raw)

    # Rolling stats for worst-case scenario modelling
    rolling = {}
    for label, n_win in [("10Y", WINDOW_10Y), ("7Y", WINDOW_7Y),
                         ("5Y", WINDOW_5Y),  ("3Y", WINDOW_3Y)]:
        rolling[label] = _rolling_stats(nav, ret, n_win)

    # ── CAGR (always from full history) ──────────────────────────────────────
    cagr_1  = _cagr(nav, 1)
    cagr_3  = _cagr(nav, 3)
    cagr_5  = _cagr(nav, 5)
    cagr_7  = _cagr(nav, 7)
    cagr_10 = _cagr(nav, 10)

    def _r(v):
        if v is None: return np.nan
        f = float(v)
        return round(f, 4) if np.isfinite(f) else np.nan

    def _combined(w: dict) -> float:
        s = w.get("Sortino", np.nan)
        c = w.get("Calmar",  np.nan)
        if (s is not None and c is not None
                and np.isfinite(s) and np.isfinite(c)
                and s > 0 and c > 0):
            return round(float(np.sqrt(s * c)), 4)
        # Sortino <= 0 or missing → zero quality credit (not NaN)
        if s is not None and np.isfinite(s) and s <= 0:
            return 0.0
        return np.nan

    # ── Assemble output dict ──────────────────────────────────────────────────
    out = {}

    # Per-window metrics (5 metrics x 4 windows: 10Y, 7Y, 5Y, 3Y)
    for label in ("10Y", "7Y", "5Y", "3Y"):
        w = windows[label]
        out[f"Std_Dev_{label}"] = w.get("Std_Dev", np.nan)
        out[f"Sharpe_{label}"]  = w.get("Sharpe",  np.nan)
        out[f"Sortino_{label}"] = w.get("Sortino", np.nan)
        out[f"Max_DD_{label}"]  = w.get("Max_DD",  np.nan)
        out[f"Calmar_{label}"]  = w.get("Calmar",  np.nan)

    # Rolling worst-case columns (5 stats x 4 windows)
    for label in ("10Y", "7Y", "5Y", "3Y"):
        r = rolling[label]
        out[f"Avg_CAGR_{label}"]     = _r(r.get("avg_cagr"))
        out[f"Min_CAGR_{label}"]     = _r(r.get("min_cagr"))
        out[f"Pctile_CAGR_{label}"]  = _r(r.get("pctile_cagr"))
        # Self-describing: the percentile is configurable, so record which one
        # produced these columns rather than encoding it in the column name.
        if r.get("pctile_used") is not None:
            out["Return_Pctile"] = r.get("pctile_used")
        out[f"Avg_Std_Dev_{label}"]  = _r(r.get("avg_std_dev"))
        out[f"Max_Std_Dev_{label}"]  = _r(r.get("max_std_dev"))
        out[f"Worst_Max_DD_{label}"] = _r(r.get("worst_max_dd"))

    # Side-channel: raw rolling series for chart popup.
    # Stored under "_rolling_series" key — stripped before CSV export.
    out["_rolling_series"] = {
        label: {
            "cagrs": rolling[label].get("_cagrs", []),
            "cagr_dates": rolling[label].get("_cagr_dates", []),
            "stds":  rolling[label].get("_stds",  []),
            "dds":   rolling[label].get("_dds",   []),
        }
        for label in ("3Y", "5Y", "7Y", "10Y")
        if rolling.get(label)
    }

    # Combined_Ratio columns (10Y, 7Y, 5Y, 3Y)
    # Cross-window safety: negative Sortino in 5Y or 10Y zeroes all windows.
    sort_5  = windows["5Y"].get("Sortino", np.nan)
    sort_10 = windows["10Y"].get("Sortino", np.nan)
    any_negative_sortino = (
        (sort_5  is not None and np.isfinite(sort_5)  and sort_5  < 0) or
        (sort_10 is not None and np.isfinite(sort_10) and sort_10 < 0)
    )
    for label in ("10Y", "7Y", "5Y", "3Y"):
        if any_negative_sortino:
            out[f"Combined_Ratio_{label}"] = 0.0
        else:
            out[f"Combined_Ratio_{label}"] = _combined(windows[label])

    # Alpha / Beta / Treynor on the longest available window (up to 10Y)
    w_long = windows["10Y"]
    out["Alpha_10Y"]   = w_long.get("Alpha",   np.nan)
    out["Beta_10Y"]    = w_long.get("Beta",    np.nan)
    out["Treynor_10Y"] = w_long.get("Treynor", np.nan)

    # History metadata
    out["History_Months"] = n_months

    # Point-in-time CAGR columns (trailing from today)
    out["1Y_CAGR"]  = _r(cagr_1)
    out["3Y_CAGR"]  = _r(cagr_3)
    out["5Y_CAGR"]  = _r(cagr_5)
    out["7Y_CAGR"]  = _r(cagr_7)
    out["10Y_CAGR"] = _r(cagr_10)

    return out



# ═══════════════════════════════════════════════════════════════════════════════
# PER-FUND WORKER
# ═══════════════════════════════════════════════════════════════════════════════

def process_fund(row: dict, amfi_map: dict) -> tuple[dict, Optional[str]]:
    fund_type = str(row.get("Fund Type", "")).strip()
    fund_name = str(row.get("Fund Name", "")).strip()
    fund_size = row.get("Fund Size", np.nan)

    base = {"Fund Type": fund_type, "Fund Name": fund_name, "Fund Size": fund_size}

    # ── Resolve AMFI code ─────────────────────────────────────────────────────
    # Priority: KNOWN_CODE_OVERRIDES → pre-supplied in row CSV → map file → auto-resolve
    amfi_code = str(row.get("AMFI Code", "")).strip()
    match_quality = "pre-supplied"

    if fund_name in KNOWN_CODE_OVERRIDES:
        amfi_code     = KNOWN_CODE_OVERRIDES[fund_name]
        match_quality = "hardcoded"
    elif not amfi_code or amfi_code in ("nan", ""):
        if fund_name in amfi_map:
            amfi_code     = amfi_map[fund_name]["code"]
            match_quality = amfi_map[fund_name]["quality"]
        else:
            amfi_code, matched_name, match_quality = _resolve_amfi_code(fund_name, fund_type)

    if not amfi_code:
        return base, f"Could not resolve AMFI code for '{fund_name}'"

    base["AMFI Code"]     = amfi_code
    base["Match Quality"] = match_quality

    # ── Fetch & compute ───────────────────────────────────────────────────────
    try:
        eom     = fetch_nav(str(amfi_code))
        metrics = compute_metrics(eom, fund_type)
        return {**base, **metrics}, None
    except Exception as exc:
        return base, str(exc)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _write_risk_free_rate(results: list, df_out) -> None:
    """Measure the liquid-fund risk-free rate and write it to the config file.

    Refuses rather than guesses.  Too few funds, too few months, or an
    implausible result and nothing is written -- overwriting a working
    configuration with a number built from four funds and six months is worse
    than leaving it alone.
    """
    from swp_planner.config.configuration import config as _config
    from swp_planner.config.configuration import set_value as _set_value
    from swp_planner.simulation import percentile_metrics as _pm

    tunables = _pm.configured()
    pct       = tunables["risk_free_rate_percentile"]
    pct_mid   = tunables["risk_free_rate_mid_percentile"]
    pct_low   = tunables["risk_free_rate_low_percentile"]
    min_funds = tunables["risk_free_min_funds"]

    # Fund Type -> is this a liquid fund?  Type first: it is the AMFI
    # regulatory category and unambiguous.  Name is the fallback.
    type_by_name = {}
    if df_out is not None and "Fund Name" in df_out.columns:
        for _n, _t in zip(df_out["Fund Name"], df_out.get("Fund Type", [])):
            type_by_name[str(_n)] = str(_t)

    per_fund = {}
    for rec in results:
        name = str(rec.get("Fund Name", ""))
        if not name or not _pm.is_liquid_fund(type_by_name.get(name), name):
            continue
        series = (rec.get("_rolling_series") or {}).get("3Y") or {}
        cagrs, dates = series.get("cagrs") or [], series.get("cagr_dates") or []
        if len(cagrs) != len(dates) or not cagrs:
            continue
        # _cagrs is stored in percent; the rate is a fraction.
        per_fund[name] = [(d, c / 100.0) for d, c in zip(dates, cagrs)]

    if len(per_fund) < min_funds:
        log.warning(
            "risk_free_rate not updated: %d liquid fund(s) with usable rolling "
            "3Y data, need at least %d", len(per_fund), min_funds)
        return

    monthly = _pm.cross_fund_monthly_average(per_fund, min_funds=min_funds)

    # All three rungs from one series, computed once -- see
    # percentile_metrics.risk_free_ladder() for why not three calls.
    try:
        ladder = _pm.risk_free_ladder(per_fund, (pct, pct_mid, pct_low),
                                      min_funds=min_funds)
    except ValueError as _exc:
        log.warning("risk_free rates not updated: %s", _exc)
        return
    rate = ladder.get(float(pct)) if ladder else None
    if rate is None:
        log.warning(
            "risk_free_rate not updated: only %d calendar month(s) had at "
            "least %d liquid funds reporting", len(monthly), min_funds)
        return

    if not 0.01 < rate < 0.15:
        log.warning(
            "risk_free_rate not updated: computed %.4f, outside the plausible "
            "1%%-15%% band. Check the liquid-fund set and the date alignment "
            "before the percentile.", rate)
        return

    # Each rung is written independently: a run that leaves one unchanged and
    # moves another is normal, and reporting "unchanged" for the whole set
    # because the base happened not to move would hide the others.
    _written = []
    for _key, _pct in (("risk_free_rate", pct),
                       ("risk_free_rate_mid", pct_mid),
                       ("risk_free_rate_low", pct_low)):
        _new = round(ladder[float(_pct)], 4)
        _old = _config.get(_key)
        if _old is not None and abs(float(_old) - _new) < 1e-9:
            log.info("%s unchanged at %.4f (P%.0f of %d months across %d "
                     "liquid funds)", _key, _new, _pct, len(monthly),
                     len(per_fund))
            continue
        _set_value(_key, _new)
        _written.append(_key)
        log.info(
            "%s updated: %s -> %.4f  (P%.0f of the cross-fund monthly "
            "average, %d months, %d liquid funds)", _key,
            f"{float(_old):.4f}" if _old is not None else "unset",
            _new, _pct, len(monthly), len(per_fund))

    if "risk_free_rate" in _written:
        log.info(
            "  every chunk min_return moves with it: min_return = "
            "risk_free_rate + spread (see chunk_defaults.py)")
    if _written:
        log.info(
            "  the FD tax benchmark ladder moves with these: each default "
            "chunk rate = its rung + fd_benchmark_spread")


def main():
    parser = argparse.ArgumentParser(description="Indian MF Fund Metrics Analyser v4")
    parser.add_argument("--input",          default=INPUT_FILE,    help="Input CSV")
    parser.add_argument("--output",         default=OUTPUT_FILE,   help="Output CSV")
    parser.add_argument("--amfi-map",       default=AMFI_MAP_FILE, help="Pre-built AMFI code map CSV")
    parser.add_argument("--workers",        default=4, type=int,   help="Parallel threads")
    parser.add_argument("--build-map-only", action="store_true",
                        help="Only build the AMFI code map, then exit")
    args = parser.parse_args()

    # ── Ensure output directory exists ────────────────────────────────────────
    # The --output path is supplied by main.py as an absolute path inside the
    # per-user output_dir (e.g. .../Uddipan/Fund_Metrics_Output.csv).
    # Derive error_file from the same directory as args.output (not the
    # module-level ERROR_FILE default) so both files always land together.
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    error_file  = str(output_path.parent / "Fund_Metrics_Errors.csv")

    if not Path(args.input).exists():
        log.error("Input file not found: %s", args.input)
        sys.exit(1)

    if args.build_map_only:
        build_amfi_map(args.input, args.amfi_map)
        sys.exit(0)

    # ── Load AMFI code map (if exists) ────────────────────────────────────────
    amfi_map = {}
    if Path(args.amfi_map).exists():
        map_df = pd.read_csv(args.amfi_map)
        for _, r in map_df.iterrows():
            code = str(r.get("AMFI Code", "")).strip()
            if code and code != "nan":
                amfi_map[str(r["Fund Name"]).strip()] = {
                    "code":    code,
                    "quality": str(r.get("Match Quality", "map")),
                }
        log.info("Loaded %d entries from AMFI map: %s", len(amfi_map), args.amfi_map)
    else:
        log.info("No AMFI map file found (%s) – will auto-resolve codes", args.amfi_map)

    # ── Load input ────────────────────────────────────────────────────────────
    raw = pd.read_csv(args.input)
    for col in ("Fund Size", "AMFI Code"):
        if col not in raw.columns:
            raw[col] = np.nan

    funds = raw[["Fund Type", "Fund Name", "Fund Size", "AMFI Code"]].copy()
    log.info("Loaded %d funds from %s", len(funds), args.input)

    # ── Drop legacy non-SEBI fund types ───────────────────────────────────────
    # AMFI still carries a handful of entries under pre-2018 scheme categories
    # ("Income", "Growth", "ELSS") that don't map to any current SEBI category.
    # These are stale entries from reclassification — the same underlying fund
    # usually also appears under its correct SEBI type.  Drop them to avoid
    # duplicate rows and ambiguous type classification in the output.
    _LEGACY_TYPES = {"Income", "Growth", "ELSS"}
    legacy_mask = funds["Fund Type"].isin(_LEGACY_TYPES)
    if legacy_mask.any():
        n_legacy = legacy_mask.sum()
        log.info("Dropping %d rows with legacy fund types: %s",
                 n_legacy, ", ".join(sorted(funds.loc[legacy_mask, "Fund Type"].unique())))
        funds = funds[~legacy_mask].reset_index(drop=True)

    # ── Pre-load shared data (single download, used across all fund threads) ───
    _load_amfi_master()
    get_rf_monthly()                                      # overnight Rf + RBI fallback
    log.info("Bond index mode: %s",
             "Nifty Composite Debt (NSE API)" if USE_ACTUAL_BOND_INDEX else "Synthetic 5Y bond")
    get_debt_benchmark_monthly(pd.Timestamp("2000-01-01"))  # CRISIL/Nifty or synthetic
    get_nifty_monthly(pd.Timestamp("2000-01-01"))         # Nifty (equity benchmark)

    # ── Process ───────────────────────────────────────────────────────────────
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_fund, row, amfi_map): row["Fund Name"]
            for row in funds.to_dict("records")
        }
        with tqdm(total=len(futures), desc="Processing funds", unit="fund") as pbar:
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    result, err = fut.result()
                except Exception as exc:
                    result = {"Fund Name": name}
                    err    = f"Unexpected: {exc}"

                if err:
                    log.warning("FAIL  %-60s | %s", name, err)
                    errors.append({"Fund Name": name, "Error": err})
                else:
                    log.info("OK    %s [%s]", name, result.get("AMFI Code", "?"))
                    results.append(result)
                pbar.update(1)

    # ── Export ────────────────────────────────────────────────────────────────
    # Column order:
    #   identity → Allocation_L → Worst_Exp_Ret_% →
    #   10Y metrics → 5Y metrics → 3Y metrics →
    #   Combined_Ratio (3 cols) → Alpha/Beta/Treynor → History → CAGR
    cols = [
        "Fund Type", "Fund Name", "Fund Size", "AMFI Code", "Match Quality",
        "Allocation_L", "Worst_Exp_Ret_%", "Return_Pctile",
        "Std_Dev_10Y", "Sharpe_10Y", "Sortino_10Y", "Max_DD_10Y", "Calmar_10Y",
        "Std_Dev_7Y",  "Sharpe_7Y",  "Sortino_7Y",  "Max_DD_7Y",  "Calmar_7Y",
        "Std_Dev_5Y",  "Sharpe_5Y",  "Sortino_5Y",  "Max_DD_5Y",  "Calmar_5Y",
        "Std_Dev_3Y",  "Sharpe_3Y",  "Sortino_3Y",  "Max_DD_3Y",  "Calmar_3Y",
        "Avg_CAGR_10Y",    "Min_CAGR_10Y",    "Pctile_CAGR_10Y",
        "Avg_Std_Dev_10Y",
        "Max_Std_Dev_10Y", "Worst_Max_DD_10Y",
        "Avg_CAGR_7Y",     "Min_CAGR_7Y",     "Pctile_CAGR_7Y",
        "Avg_Std_Dev_7Y",
        "Max_Std_Dev_7Y",  "Worst_Max_DD_7Y",
        "Avg_CAGR_5Y",     "Min_CAGR_5Y",     "Pctile_CAGR_5Y",
        "Avg_Std_Dev_5Y",
        "Max_Std_Dev_5Y",  "Worst_Max_DD_5Y",
        "Avg_CAGR_3Y",     "Min_CAGR_3Y",     "Pctile_CAGR_3Y",
        "Avg_Std_Dev_3Y",
        "Max_Std_Dev_3Y",  "Worst_Max_DD_3Y",
        "Combined_Ratio_10Y", "Combined_Ratio_7Y",
        "Combined_Ratio_5Y",  "Combined_Ratio_3Y",
        "Alpha_10Y", "Beta_10Y", "Treynor_10Y",
        "History_Months",
        "1Y_CAGR", "3Y_CAGR", "5Y_CAGR", "7Y_CAGR", "10Y_CAGR",
    ]

    if results:
        df_out = pd.DataFrame(results).reindex(columns=cols)

        # ── Sort: Combined_Ratio_10Y desc → 5Y desc → 3Y desc ────────────────
        # Funds with NaN in a sort key go to the bottom of that tier.
        # We achieve stable multi-key NaN-last sorting by replacing NaN with -inf
        # for sorting purposes only, then restoring the original values.
        for cr_col in ("Combined_Ratio_10Y", "Combined_Ratio_5Y", "Combined_Ratio_3Y"):
            df_out[f"_sort_{cr_col}"] = df_out[cr_col].fillna(-np.inf)

        df_out = df_out.sort_values(
            ["_sort_Combined_Ratio_10Y", "_sort_Combined_Ratio_5Y", "_sort_Combined_Ratio_3Y"],
            ascending=[False, False, False],
        ).drop(columns=[c for c in df_out.columns if c.startswith("_sort_")])

        # ── Allocation_L ──────────────────────────────────────────────────────
        # Placeholder column; populate manually or via a separate allocation
        # script once fund selection is finalised.  All funds default to 0.
        df_out["Allocation_L"] = 0.0

        # ── Worst_Exp_Ret_% ───────────────────────────────────────────────────
        # Minimum of all available positive CAGR columns, then subtract 0.40%
        # STT hit for Arbitrage / Tax Efficient Income fund types.
        # Use rolling Avg_CAGR columns (no 1Y) for a stable pessimistic estimate.
        avg_cagr_cols = [c for c in
                         ["Avg_CAGR_3Y", "Avg_CAGR_5Y", "Avg_CAGR_7Y", "Avg_CAGR_10Y"]
                         if c in df_out.columns]
        if avg_cagr_cols:
            df_out["_min_avg_cagr"] = df_out[avg_cagr_cols].apply(
                lambda row: row[row > 0].min() if (row > 0).any() else np.nan, axis=1
            )
        else:
            fallback_cols = [c for c in ["3Y_CAGR", "5Y_CAGR", "7Y_CAGR", "10Y_CAGR"]
                             if c in df_out.columns]
            df_out["_min_avg_cagr"] = df_out[fallback_cols].apply(
                lambda row: row[row > 0].min() if (row > 0).any() else np.nan, axis=1
            )
        stt_mask = df_out["Fund Type"].str.lower().isin(STT_HIT_TYPES)
        df_out["Worst_Exp_Ret_%"] = (
            df_out["_min_avg_cagr"] - stt_mask.astype(float) * 0.004
        ).round(4)
        df_out.drop(columns=["_min_avg_cagr"], inplace=True)

        n_10y = df_out["Combined_Ratio_10Y"].notna().sum()
        n_5y  = df_out["Combined_Ratio_5Y"].notna().sum()
        n_3y  = df_out["Combined_Ratio_3Y"].notna().sum()
        log.info(
            "Combined_Ratio computed: 10Y=%d  5Y=%d  3Y=%d  (of %d funds); sorted descending",
            n_10y, n_5y, n_3y, len(df_out),
        )

        # ── HANDOVER 2.5: measure risk_free_rate and write it back ────────
        # The through-the-cycle liquid-fund yield: average each liquid fund's
        # rolling 3Y CAGR across funds per calendar month, then take the
        # configured percentile of that monthly series.  Average first,
        # percentile second -- the orders are not interchangeable.
        try:
            _write_risk_free_rate(results, df_out)
        except Exception as _exc:                      # never abort the run
            log.warning("risk_free_rate not updated: %s", _exc)

        Path(args.output).parent.mkdir(parents=True, exist_ok=True)

        # ── Write rolling_data.json (raw series for chart popups) ─────────
        # Collect _rolling_series from results before stripping from df_out
        rolling_json: dict = {}
        for rec in results:
            fname = rec.get("Fund Name", "")
            series = rec.get("_rolling_series")
            if fname and series:
                rolling_json[fname] = series
        rolling_path = Path(args.output).parent / "rolling_data.json"
        try:
            import json as _json
            with open(rolling_path, "w", encoding="utf-8") as _jf:
                _json.dump(rolling_json, _jf, separators=(',', ':'))
            log.info("Rolling data saved -> %s (%d funds)",
                     rolling_path, len(rolling_json))
        except Exception as _je:
            log.warning("Could not write rolling_data.json: %s", _je)

        # Strip _rolling_series from df_out before CSV export
        if "_rolling_series" in df_out.columns:
            df_out = df_out.drop(columns=["_rolling_series"])

        df_out.to_csv(args.output, index=False)
        log.info("Saved %d results -> %s", len(df_out), args.output)

    if errors:
        pd.DataFrame(errors).to_csv(error_file, index=False)
        log.warning("Saved %d errors -> %s", len(errors), error_file)

    log.info("Done.  Success: %d / %d  |  Failed: %d / %d",
             len(results), len(funds), len(errors), len(funds))


if __name__ == "__main__":
    # Logging is configured here rather than at import, so that importing this
    # module (monte_carlo does, for the debt-index table) has no side effects.
    configure_logging()
    main()