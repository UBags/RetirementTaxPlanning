# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_fund_score.py -- relative risk within a category, and concentration.

THE FOUR THAT CARRY THE DESIGN:

  test_returns_cannot_buy_off_bad_credit  -- the whole reason credit is a
      multiplier rather than a weighted term
  test_unrated_scores_as_bad_not_average  -- a fund nobody checked is not an
      average fund
  test_one_big_breach_beats_three_small_ones  -- excess, not count
  test_a_borrower_across_three_lines_is_one_exposure  -- NABARD is three rows
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fund_filters import fund_score as FS  # noqa: E402
from ingest import fund_store as F  # noqa: E402

JUL, AUG = dt.date(2026, 7, 31), dt.date(2026, 8, 7)
GOOD = {"AAA": 90.0, "AA": 10.0}
POOR = {"AAA": 10.0, "Other": 60.0, "Unrated": 30.0}


def _f(name, sortino=1.0, calmar=1.0, vol=5.0, dd=-8.0, years=10.0,
       bands=None, exposures=None):
    return dict(name=name, sortino=sortino, calmar=calmar, volatility=vol,
                max_drawdown=dd, history_years=years,
                bands=bands or GOOD, issuer_exposures=exposures or [2.0, 1.5])


def _peers(n=5, **kw):
    return [_f(f"peer{i}", sortino=0.5 + i * 0.1, calmar=0.5 + i * 0.1,
               vol=6.0 - i * 0.2, **kw) for i in range(n)]


# ── credit as a multiplier ──────────────────────────────────────────────────

def test_returns_cannot_buy_off_bad_credit():
    """THE WHOLE REASON CREDIT MULTIPLIES RATHER THAN AVERAGES. Averaged in, a
    fund with a strong Sortino and weak paper scores mid-table -- but the
    returns were earned in the years before the default and the default takes
    them all back at once. Franklin Templeton's six schemes had unremarkable
    volatility the month before they froze."""
    funds = _peers(5) + [_f("best returns, worst paper", sortino=99.0,
                            calmar=99.0, vol=0.5, bands=POOR,
                            exposures=[9.0, 8.0])]
    out = FS.score_category(funds)
    bad = [d for d in out if d["name"] == "best returns, worst paper"][0]
    assert bad["base_score"] > 90, "its performance really is top of category"
    assert bad["credit_multiplier"] < 0.6
    # ASSERTED ON THE BOTTOM HALF, NOT ON "not first". A first version checked
    # only that it was not ranked #1 -- which an ordinary weighted average
    # also satisfies, landing it third of six. Averaging leaves a fund with
    # the worst paper in its category mid-table; multiplying puts it where it
    # belongs.
    assert bad["rank"] > len(out) / 2, (
        f"worst paper in the category ranked {bad['rank']} of {len(out)}")


def test_good_credit_does_not_rescue_bad_returns():
    """The multiplier caps at 1.0: credit can only pull a fund down, never
    lift it above what it earned."""
    funds = _peers(5) + [_f("perfect paper, poor returns", sortino=0.01,
                            calmar=0.01, vol=20.0, bands={"AAA": 100.0})]
    out = FS.score_category(funds)
    worst = out[-1]
    assert worst["name"] == "perfect paper, poor returns"
    assert worst["credit_multiplier"] == 1.0


def test_unrated_scores_as_bad_not_average():
    """A fund that is 40% unrated is not an average-credit fund, it is one
    nobody has checked. Scoring the unknown at the mean is how a screen
    recommends what it could not see."""
    assert FS.credit_quality({"AAA": 60.0, "Unrated": 40.0}) == 60.0
    assert FS.credit_quality({"AAA": 100.0}) == 100.0
    assert FS.credit_quality({"Unrated": 100.0}) == 0.0


def test_an_empty_split_is_zero_not_perfect():
    assert FS.credit_quality({}) == 0.0
    assert FS.credit_quality(None) == 0.0


@pytest.mark.parametrize("bands,expected", [
    ({"AAA": 100.0}, 100.0), ({"AA": 100.0}, 60.0),
    ({"Other": 100.0}, 15.0), ({"Unrated": 100.0}, 0.0),
])
def test_the_bands_are_weighted_in_order(bands, expected):
    assert FS.credit_quality(bands) == expected


# ── the cap ─────────────────────────────────────────────────────────────────

def test_the_cap_is_three_and_a_quarter():
    """A quarter point above the round number, so a fund sitting at exactly
    3.00% by design is not flagged every week."""
    assert FS.ISSUER_CAP == 3.25 == F.ISSUER_CAP
    assert FS.breach_penalty([3.24]) == 100.0
    assert FS.breach_penalty([3.26]) < 100.0


def test_one_big_breach_beats_three_small_ones():
    """MEASURED BY EXCESS, NOT COUNT. One issuer at 9% is a worse fund than
    three at 3.5%, and a rule that counted breaches would say the opposite."""
    three_small = FS.breach_penalty([3.5, 3.5, 3.5])     # excess 0.75
    one_big = FS.breach_penalty([9.0])                   # excess 5.75
    assert one_big < three_small < 100.0


def test_no_breach_is_a_full_score():
    assert FS.breach_penalty([3.0, 2.5, 1.0]) == 100.0
    assert FS.breach_penalty([]) == 100.0
    assert FS.breach_penalty(None) == 100.0


# ── ranking ─────────────────────────────────────────────────────────────────

def test_a_category_too_small_to_rank_is_refused():
    """Three funds scored against each other produce 0, 50 and 100 whatever
    their merits."""
    with pytest.raises(FS.ScoreError):
        FS.score_category(_peers(3))


def test_scores_are_percentiles_among_peers_not_absolutes():
    """A liquid fund's Sortino of 12 and a small-cap's 0.7 are not on the same
    axis; ranking them together says which category was luckier."""
    out = FS.score_category(_peers(6))
    assert [d["rank"] for d in out] == [1, 2, 3, 4, 5, 6]
    assert out[0]["percentile"] > out[-1]["percentile"]
    assert all(d["peers"] == 6 for d in out)


def test_a_missing_metric_scores_zero_not_the_mean():
    """A metric nobody computed is not an average metric.

    Pinned on the term rather than on the final rank: a fund missing its
    Sortino can still finish mid-table on Calmar and volatility, and asserting
    it comes LAST tests the fixture rather than the rule -- which is what the
    first version of this test did."""
    assert FS._pct_rank(None, [1.0, 2.0, 3.0]) == 0.0
    with_metric = FS.score_category(_peers(5) + [_f("has it", sortino=5.0)])
    without = FS.score_category(_peers(5) + [_f("has it", sortino=None)])
    a = [d for d in with_metric if d["name"] == "has it"][0]
    b = [d for d in without if d["name"] == "has it"][0]
    assert b["base_score"] < a["base_score"]
    assert b["rank"] > a["rank"]


def test_low_volatility_ranks_higher():
    funds = _peers(5) + [_f("calm", vol=0.1)]
    out = FS.score_category(funds)
    calm = [d for d in out if d["name"] == "calm"][0]
    assert calm["base_score"] > sum(d["base_score"] for d in out) / len(out)


def test_short_history_scores_down_without_being_called_bad():
    """Uncertainty, not badness -- but for a thirty-year plan uncertainty is a
    reason to size smaller."""
    assert FS.history_score(10) == 100.0
    assert FS.history_score(2.5) == 25.0
    assert FS.history_score(0) == 0.0
    assert FS.history_score(None) == 0.0


def test_the_explanation_names_every_term():
    out = FS.score_category(_peers(6))
    line = FS.explain(out[0])
    for word in ("composite", "base", "credit", "worst issuer", "over cap"):
        assert word in line


# ── reading get_funds_data's own columns ────────────────────────────────────

def test_the_preferred_window_is_five_years():
    """Long enough to contain a drawdown, short enough that most funds have
    it. Mirrors the existing adj_quality convention rather than inventing a
    second one."""
    row = {"Sharpe_3Y": 1.0, "Sharpe_5Y": 2.0, "Sharpe_10Y": 3.0}
    assert FS.from_metrics_row(row)["sharpe"] == 2.0
    assert FS.from_metrics_row(row)["window"] == "5Y"


def test_a_young_fund_falls_back_to_a_shorter_window():
    """A three-year-old fund has no 10Y column, and that is a fact about the
    fund rather than a parse error."""
    got = FS.from_metrics_row({"Sharpe_3Y": 1.0, "Sortino_3Y": 1.2})
    assert got["sharpe"] == 1.0 and got["window"] == "3Y"
    assert got["calmar"] is None


def test_metrics_are_read_not_recomputed():
    """Sharpe, Sortino, Calmar and max drawdown already exist in
    get_funds_data.py. A second implementation would disagree with the first
    in ways nobody would notice for months."""
    import inspect
    src = inspect.getsource(FS)
    for forbidden in ("stdev", "np.std", "sqrt(", "pct_change"):
        assert forbidden not in src


# ── issuer concentration ────────────────────────────────────────────────────

def _h(company, pct, rating="AAA", seniority=None, is_issuer=True, code=3):
    return dict(company=company, company_id=abs(hash(company)) % 9999,
                instrument_code=code, percent=pct, value=pct * 1e7,
                is_issuer=is_issuer, asset_class="debt",
                borrower_category="NBFC", rating=rating, seniority=seniority,
                instrument_type="Debenture")


def test_a_borrower_across_three_lines_is_one_exposure():
    """NABARD is three rows in one month under Bonds and Debenture; a 3.25%
    cap breached across three lines is still breached.

    The example borrower is a PRIVATE one, deliberately. A first version used
    NABARD itself and stopped meaning anything once quasi-sovereigns became
    exempt -- summing was still correct, but the assertion it was pinned on
    was about the cap and the cap no longer binds there."""
    out = F.issuer_exposures([_h("Acme Finance Ltd", 1.5),
                              _h("Acme Finance Ltd", 1.2),
                              _h("Acme Finance Ltd", 0.9),
                              _h("REC Ltd.", 2.0)])
    acme = [d for d in out if d["company"] == "Acme Finance Ltd"][0]
    assert acme["percent"] == 3.6 and acme["lines"] == 3
    assert acme["over_cap"] and acme["excess"] == pytest.approx(0.35)


def test_summing_still_happens_for_an_exempt_borrower():
    """Exemption changes the flag, not the arithmetic."""
    out = F.issuer_exposures([_h("NABARD", 1.5), _h("NABARD", 2.4)])
    assert out[0]["percent"] == 3.9 and out[0]["lines"] == 2
    assert out[0]["cap_exempt"] and not out[0]["over_cap"]


def test_cash_and_repo_are_not_borrowers():
    """A concentration screen that ranks "Net Receivables" among the top
    exposures is measuring the fund's plumbing."""
    out = F.issuer_exposures([_h("Others", 11.0, is_issuer=False),
                              _h("REC Ltd.", 2.0)])
    assert [d["company"] for d in out] == ["REC Ltd."]


def test_the_worst_rating_across_the_lines_is_kept():
    """An issuer whose two lines are AAA and A is not a AAA exposure."""
    out = F.issuer_exposures([_h("X Ltd", 1.0, rating="AAA"),
                              _h("X Ltd", 1.0, rating="A")])
    assert out[0]["rating"] == "A" and out[0]["rating_band"] == F.BAND_OTHER


def test_a_subordinated_line_marks_the_whole_exposure():
    """An issuer with a senior bond and a perpetual in the same fund is not a
    senior-only exposure."""
    out = F.issuer_exposures([_h("Bank Ltd", 2.0),
                              _h("Bank Ltd", 0.5, seniority="subordinated")])
    assert out[0]["worst_seniority"] == "subordinated"


def test_exposures_come_back_largest_first():
    out = F.issuer_exposures([_h("small", 0.5), _h("big", 9.0),
                              _h("mid", 3.0)])
    assert [d["company"] for d in out] == ["big", "mid", "small"]


def test_the_cap_applied_is_recorded_with_the_row():
    """Change the cap next year and a view silently restates history; a stored
    number records what was actually flagged when."""
    out = F.issuer_exposures([_h("X", 4.0)], cap=5.0)
    assert out[0]["cap_applied"] == 5.0 and not out[0]["over_cap"]


# ── against the database ────────────────────────────────────────────────────

@pytest.fixture()
def conn(dsn):
    import psycopg2

    from ingest import store as S
    c = psycopg2.connect(dsn)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS india CASCADE")
    c.commit()
    S.apply_schema(c)
    F.apply_schema(c)
    F.upsert_fund(c, 1, "Fund One", as_on=AUG)
    F.upsert_fund(c, 2, "Fund Two", as_on=AUG)
    yield c
    c.close()


def test_exposures_are_stored_and_breaches_are_queryable(conn):
    F.load_issuer_exposures(conn, 1, JUL, [
        _h("Acme Finance Ltd", 1.5), _h("Acme Finance Ltd", 2.4),
        _h("NABARD", 9.0), _h("REC Ltd.", 2.0)], today=AUG)
    breaches = F.cap_breaches(conn)
    assert [b["company"] for b in breaches] == ["Acme Finance Ltd"], \
        "NABARD at 9% is stored but not flagged"
    assert float(breaches[0]["excess"]) == pytest.approx(0.65)
    with conn.cursor() as cur:
        cur.execute("SELECT percent, cap_exempt, exempt_reason FROM "
                    "india.fund_issuer_exposure WHERE company='NABARD'")
        pct, exempt, reason = cur.fetchone()
        assert float(pct) == 9.0 and exempt
        assert reason == "quasi_sovereign:NABARD"


def test_reloading_a_month_replaces_it_whole(conn):
    F.load_issuer_exposures(conn, 1, JUL, [_h("A", 5.0), _h("B", 5.0)],
                            today=AUG)
    F.load_issuer_exposures(conn, 1, JUL, [_h("A", 9.0)], today=AUG)
    with conn.cursor() as cur:
        cur.execute("SELECT company FROM india.fund_issuer_exposure")
        assert [r[0] for r in cur.fetchall()] == ["A"]


def test_one_borrower_across_funds_is_one_row(conn):
    """What you read the morning after a downgrade."""
    for fid in (1, 2):
        F.load_issuer_exposures(conn, fid, JUL, [_h("Acme Finance Ltd", 4.0)],
                                today=AUG)
    with conn.cursor() as cur:
        cur.execute("SELECT company, funds, largest_percent, any_over_cap "
                    "FROM india.issuer_across_funds")
        row = cur.fetchone()
    assert row[0] == "Acme Finance Ltd" and row[1] == 2 and row[3] is True


# ── the cap does not bind on quasi-sovereigns ───────────────────────────────

from fund_filters import quasi_sovereign as Q  # noqa: E402


@pytest.mark.parametrize("name,key", [
    ("National Bank For Agriculture & Rural Development", "NABARD"),
    ("NABARD", "NABARD"),
    ("REC Ltd.", "REC"),
    ("Rural Electrification Corporation Ltd", "REC"),
    ("Housing & Urban Development Corporation Ltd.", "HUDCO"),
    ("Power Finance Corporation Ltd.", "PFC"),
    ("Indian Railway Finance Corporation Ltd", "IRFC"),
    ("National Highways Authority of India Ltd.", "NHAI"),
    ("Indian Renewable Energy Development Agency Ltd", "IREDA"),
    ("National Housing Bank", "NHB"),
    ("India Infrastructure Finance Company Ltd", "IIFCL"),
    ("NTPC Ltd.", "NTPC"),
    ("NHPC Ltd", "NHPC"),
    ("Power Grid Corporation of India Ltd.", "PGCIL"),
    ("Indian Oil Corporation Ltd.", "IOCL"),
    ("Nuclear Power Corpn. Of India Ltd.", "NPCIL"),
    ("Small Industries Devp. Bank of India Ltd.", "SIDBI"),
])
def test_every_named_quasi_sovereign_matches_as_the_source_writes_it(name, key):
    """THE SOURCE ABBREVIATES DIFFERENTLY IN DIFFERENT FUNDS -- "Nuclear Power
    Corpn. Of India Ltd." in one, "NPCIL" in the next, "Small Industries
    Devp. Bank" in a third. Every pattern tolerates CORP/CORPN/CORPORATION and
    DEV/DEVP/DEVELOPMENT."""
    assert Q.match(name) == key


@pytest.mark.parametrize("name", [
    "Recron Synthetics Ltd", "HDFC Bank Ltd.", "Tata Power Company Ltd",
    "Torrent Power Ltd", "Adani Power Ltd", "JSW Energy Ltd",
    "National Aluminium Company", "Grid Corporation of Odisha",
    "Indian Energy Exchange", "Bajaj Finance Ltd",
])
def test_a_substring_does_not_make_a_borrower_quasi_sovereign(name):
    """A bare substring test for "REC", "NPC" or "GRID" fires inside unrelated
    names -- the mistake that put 801 NBFCs in the private-bank band."""
    assert Q.match(name) is None


def test_a_psu_bank_is_exempt_by_category_not_by_name():
    """Category exemptions come from NSDL's sector and ownership columns, so
    they follow the source; the name list is a judgement someone maintains.
    Six months from now the difference decides whether to check the data or
    the list."""
    exempt, reason = Q.cap_exemption("Union Bank of India", "PSU Bank")
    assert exempt and reason == "category:PSU Bank"
    exempt, reason = Q.cap_exemption("REC Ltd.", "Large Corporate")
    assert exempt and reason == "quasi_sovereign:REC"


def test_a_private_bank_is_not_exempt():
    """HDFC Bank is not government-supported, whatever its size."""
    assert Q.cap_exemption("HDFC Bank Ltd.", "Large Pvt Bank")[0] is False


def test_the_exposure_is_kept_even_when_exempt():
    """A fund with 9.45% in NABARD is still a fund with 9.45% in NABARD;
    exemption clears the flag, it does not hide the exposure."""
    out = F.issuer_exposures([_h("National Bank For Agriculture & Rural "
                                 "Development", 9.45)])
    assert out[0]["percent"] == 9.45
    assert out[0]["cap_exempt"] and not out[0]["over_cap"]
    assert out[0]["excess"] == 0.0
    assert out[0]["exempt_reason"] == "quasi_sovereign:NABARD"


def test_a_private_borrower_over_the_cap_is_still_flagged():
    """The exemptions must not quietly disarm the whole rule."""
    out = F.issuer_exposures([_h("Some Private NBFC Ltd", 9.45)])
    assert out[0]["over_cap"] and out[0]["excess"] == pytest.approx(6.2)


def test_the_breach_score_ignores_exempt_exposures():
    """On the first real fund four of the eight flagged issuers were
    government entities, which buries the breaches that matter."""
    exempt = [dict(percent=9.45, cap_exempt=True),
              dict(percent=9.52, cap_exempt=True)]
    binding = [dict(percent=9.45, cap_exempt=False)]
    assert FS.breach_penalty(exempt) == 100.0
    assert FS.breach_penalty(binding) < 100.0


def test_a_bare_number_cannot_be_exempt():
    """A caller passing plain percentages has told us nothing about
    exemption, and assuming exemption would silently disarm the cap."""
    assert FS.breach_penalty([9.45]) < 100.0


# ── the runner: reading the real metrics file ───────────────────────────────

import importlib.util as _ilu  # noqa: E402

_rk = _ilu.spec_from_file_location("fund_rank",
                                   _ROOT / "scripts" / "fund_rank.py")
RK = _ilu.module_from_spec(_rk)
_rk.loader.exec_module(RK)

METRICS_ROW = {
    "Fund Type": "Debt Scheme - Liquid Fund",
    "Fund Name": "Bajaj Finserv Liquid Fund", "Fund Size": "5000",
    "AMFI Code": "151833", "Match Quality": "exact",
    "Std_Dev_10Y": "", "Sharpe_10Y": "", "Sortino_10Y": "", "Max_DD_10Y": "",
    "Calmar_10Y": "", "Std_Dev_5Y": "0.0022", "Sharpe_5Y": "12.1",
    "Sortino_5Y": "15.4893", "Max_DD_5Y": "0.0", "Calmar_5Y": "676.2351",
    "Std_Dev_3Y": "0.0031", "Sharpe_3Y": "10.0", "Sortino_3Y": "11.0",
    "Max_DD_3Y": "0.0", "Calmar_3Y": "500.0",
    "Max_Std_Dev_5Y": "0.9999", "Worst_Max_DD_5Y": "-9.9",
    "History_Months": "36",
}


def test_the_metrics_columns_are_the_real_ones():
    m = FS.from_metrics_row(METRICS_ROW)
    assert m["amfi_code"] == 151833
    assert m["metrics_category"] == "Debt Scheme - Liquid Fund"
    assert m["sortino"] == 15.4893 and m["calmar"] == 676.2351
    assert m["history_years"] == 3.0


def test_volatility_is_std_dev_not_max_std_dev():
    """Max_Std_Dev is the worst rolling window -- a drawdown-adjacent measure
    that would double-count against Calmar. Std_Dev is the volatility everyone
    means, and taking the wrong one would be undetectable in the output."""
    m = FS.from_metrics_row(METRICS_ROW)
    assert m["volatility"] == 0.0022, "Std_Dev_5Y, not Max_Std_Dev_5Y 0.9999"
    assert m["max_drawdown"] == 0.0, "Max_DD_5Y, not Worst_Max_DD_5Y -9.9"


def test_an_empty_cell_is_missing_not_zero():
    """A blank 10Y column means the fund is younger than ten years. Reading it
    as 0.0 would rank a three-year-old fund as having the worst decade in its
    category."""
    assert FS._number("") is None and FS._number("nan") is None
    assert FS._number("0") == 0.0
    m = FS.from_metrics_row(METRICS_ROW)
    assert m["window_label"] == "5Y", "10Y is blank, so the 5Y column is used"
    # And the EFFECTIVE window is 36 months, not five years: this fixture has
    # History_Months 36, so its "Sharpe_5Y" is a three-year Sharpe wearing a
    # five-year label. That distinction is the reason `window` exists.
    assert m["window"] == "36m"


def test_a_young_fund_falls_back_to_three_years():
    row = dict(METRICS_ROW)
    for k in list(row):
        if k.endswith("_5Y"):
            row[k] = ""
    assert FS.from_metrics_row(row)["window"] == "3Y"


# ── peer groups and the roll-up ─────────────────────────────────────────────

def _fund(fid, cat, sortino=1.0):
    return dict(fund_id=fid, label=f"F{fid}", as_on=JUL, category=cat,
                amfi_scheme_code=fid, amfi_name=f"F{fid}", sortino=sortino,
                calmar=1.0, volatility=1.0, max_drawdown=-1.0,
                history_years=10.0, bands={"AAA": 100.0},
                issuer_exposures=[], has_credit=True, joined_by="amfi_code")


def test_a_small_category_rolls_up_to_its_scheme_type():
    """A percentile over three funds is 0, 50 and 100 whatever their merits."""
    funds = ([_fund(i, "Debt Scheme - Liquid Fund") for i in range(6)]
             + [_fund(100 + i, "Debt Scheme - Long Duration Fund")
                for i in range(3)])
    groups = RK.peer_groups(funds)
    assert "Debt Scheme - Liquid Fund" in groups
    assert "Debt Scheme - Long Duration Fund" not in groups
    assert len(groups["Debt Scheme"]) == 3


def test_the_roll_up_is_recorded_on_every_row():
    """A fund ranked among all Debt Scheme funds is not comparable to one
    ranked within Liquid Fund, and a reader must be able to tell which
    happened."""
    funds = [_fund(i, f"Debt Scheme - Tiny {i % 3} Fund", sortino=i)
             for i in range(9)]
    ranked, unranked = RK.score_all(funds)
    assert ranked and all(d["peer_group"] == "Debt Scheme" for d in ranked)


def test_a_group_still_too_small_comes_back_unranked_not_scored():
    ranked, unranked = RK.score_all([_fund(1, "Odd Scheme - Only Fund")])
    assert ranked == [] and len(unranked) == 1
    assert "1 peers" in unranked[0]["reason"]


# ── the join ────────────────────────────────────────────────────────────────

def test_the_join_prefers_the_amfi_code():
    """Fund_Metrics_Output.csv carries AMFI Code and india.fund stores
    amfi_scheme_code, so the two meet on an integer. A fuzzy join that
    silently pairs the wrong funds is the same class of error as the four
    schemes that took a neighbour's ETMoney id."""
    m = FS.from_metrics_row(METRICS_ROW)
    fund = dict(_fund(1, "Debt Scheme - Liquid Fund"),
                amfi_scheme_code=151833, amfi_name="Something Else Entirely")
    joined, unjoined = RK.join([fund], {151833: m}, {})
    assert unjoined == [] and joined[0]["joined_by"] == "amfi_code"


def test_the_name_join_is_a_fallback_and_is_labelled():
    m = FS.from_metrics_row(METRICS_ROW)
    fund = dict(_fund(1, "Debt Scheme - Liquid Fund"), amfi_scheme_code=None,
                amfi_name="Bajaj Finserv Liquid Fund")
    joined, _ = RK.join([fund], {}, {RK._normalise("Bajaj Finserv Liquid Fund"): m})
    assert joined[0]["joined_by"] == "name"


def test_a_fund_with_no_metrics_is_reported_not_scored():
    """Only 801 of ~2,300 funds have metrics; the report must say so rather
    than presenting the ranked third as the whole."""
    fund = dict(_fund(1, "Debt Scheme - Liquid Fund"), amfi_scheme_code=999)
    joined, unjoined = RK.join([fund], {}, {})
    assert joined == [] and len(unjoined) == 1


def test_a_fund_with_no_credit_picture_is_flagged_not_given_a_free_pass():
    """Scoring it on performance alone with a multiplier of 1.0 would rank it
    above funds whose paper was actually checked."""
    funds = [dict(_fund(i, "Debt Scheme - Liquid Fund"), bands={},
                  has_credit=False) for i in range(6)]
    ranked, _ = RK.score_all(funds)
    assert all(d["credit_multiplier"] < 1.0 for d in ranked)
    assert all(d["has_credit"] is False for d in ranked)


def test_inspecting_one_group_does_not_skip_the_write(conn):
    """FOUND END TO END. The --category branch returned before storing, so
    inspecting one group silently skipped the write for all of them: the
    scores appeared on screen and india.fund_score stayed empty."""
    import inspect
    src = inspect.getsource(RK.main)
    store_at = src.index("store(conn, ranked)")
    category_at = src.index('if a.category:')
    assert store_at < category_at, \
        "the write must happen before any early return"


# ── after MIN_HISTORY dropped from 36 to 12 ─────────────────────────────────

def _short_row(months, label="5Y"):
    return {f"Sortino_{label}": "1.5", f"Sharpe_{label}": "1.0",
            f"Calmar_{label}": "2.0", f"Std_Dev_{label}": "0.1",
            f"Max_DD_{label}": "-5", "History_Months": str(months)}


def test_the_window_label_is_not_the_window():
    """get_funds_data slices iloc[-N:] per window, so a fund shorter than N
    reports that window computed on everything it has -- a 20-month fund shows
    identical 3Y, 5Y, 7Y and 10Y figures. "Sharpe_5Y" on that fund is a
    20-month Sharpe wearing a five-year label, and since MIN_HISTORY dropped
    to 12 there are hundreds of them."""
    assert FS.from_metrics_row(_short_row(20))["window"] == "20m"
    assert FS.from_metrics_row(_short_row(20))["window_label"] == "5Y"


def test_a_fund_long_enough_keeps_its_window_label():
    assert FS.from_metrics_row(_short_row(72))["window"] == "5Y"
    assert FS.from_metrics_row(_short_row(40, "3Y"))["window"] == "3Y"


def test_thin_history_is_flagged_but_still_ranked():
    """Excluding short-history funds would hide most of the thematic and
    multi-asset universe. Ranking them silently would let a 14-month #1 read
    as the same claim as a ten-year #1."""
    funds = [_f(f"old{i}", sortino=0.5 + i * 0.1) for i in range(5)]
    funds.append(dict(_f("new", sortino=9.0), history_years=1.2))
    out = FS.score_category(funds)
    new = [d for d in out if d["name"] == "new"][0]
    assert new["thin_history"] is True
    assert all(not d["thin_history"] for d in out if d["name"] != "new")
    assert "THIN HISTORY" in FS.explain(new)
    assert "THIN HISTORY" not in FS.explain(out[-1] if out[-1]["name"]
                                            != "new" else out[0])


def test_three_years_is_the_thin_boundary():
    """Below a full market cycle, Sharpe and Calmar are measuring one
    regime."""
    assert FS.THIN_HISTORY_YEARS == 3.0
    funds = [_f(f"p{i}", sortino=0.5 + i * 0.1) for i in range(5)]
    funds.append(dict(_f("just_over"), history_years=3.01))
    out = FS.score_category(funds)
    assert not [d for d in out if d["name"] == "just_over"][0]["thin_history"]


# ── credit scales with credit exposure ──────────────────────────────────────

def _mixed(name, sortino, bands, debt_share):
    return dict(name=name, sortino=sortino, calmar=sortino, volatility=1.0,
                max_drawdown=-1.0, history_years=10.0, bands=bands,
                debt_share=debt_share, issuer_exposures=[])


def test_an_equity_fund_is_not_penalised_for_holding_no_bonds():
    """FOUND WHEN THE UNIVERSE STOPPED BEING ALL-DEBT. An equity fund has no
    bands because it has no bonds, and it was collecting a 0.545 multiplier
    for it -- harmless inside an all-equity category where the scaling is
    uniform, and wrong in a mixed one. On a synthetic hybrid category the fund
    with the BEST base score fell to fourth for holding no debt."""
    funds = [_mixed("equity", 2.0, {}, 0.0)] + \
        [_mixed(f"debt{i}", 0.5 + i * 0.1, {"AAA": 100.0}, 1.0)
         for i in range(5)]
    out = FS.score_category(funds)
    eq = [d for d in out if d["name"] == "equity"][0]
    assert eq["credit_multiplier"] == 1.0
    assert eq["rank"] == 1, "its base score is the best in the category"
    assert eq["credit_applicable"] is False


def test_a_debt_fund_that_did_not_resolve_is_still_penalised_in_full():
    """An absent dimension and an unmeasured one are different. A fund holding
    bonds nobody could rate is a real gap."""
    funds = [_mixed("unresolved", 2.0, {}, 1.0)] + \
        [_mixed(f"p{i}", 0.5 + i * 0.1, {"AAA": 100.0}, 1.0) for i in range(5)]
    bad = [d for d in FS.score_category(funds)
           if d["name"] == "unresolved"][0]
    assert bad["credit_multiplier"] < 0.6
    assert bad["credit_applicable"] is True


def test_the_penalty_is_proportional_to_the_debt_held():
    """A 100% debt fund takes it whole, a 30% debt fund takes 30% of it."""
    junk = {"Other": 100.0}
    full = FS.credit_multiplier(FS.credit_quality(junk), 100.0,
                                debt_share=1.0)
    third = FS.credit_multiplier(FS.credit_quality(junk), 100.0,
                                 debt_share=0.3)
    none = FS.credit_multiplier(FS.credit_quality(junk), 100.0,
                                debt_share=0.0)
    assert none == 1.0
    assert full < third < none
    assert (1.0 - third) == pytest.approx(0.3 * (1.0 - full), abs=1e-3)


def test_an_unmeasured_debt_share_is_treated_as_fully_exposed():
    """Assuming a fund has no debt because nobody measured it would hand every
    unmeasured fund a free pass, which is the opposite of what missing data
    should do."""
    assert FS.credit_multiplier(0.0, 100.0, debt_share=None) == \
        FS.credit_multiplier(0.0, 100.0, debt_share=1.0)
    funds = [_mixed("unmeasured", 2.0, {}, None)] + \
        [_mixed(f"p{i}", 0.5 + i * 0.1, {"AAA": 100.0}, 1.0) for i in range(5)]
    d = [x for x in FS.score_category(funds) if x["name"] == "unmeasured"][0]
    assert d["credit_multiplier"] < 1.0


def test_the_explanation_says_when_credit_does_not_apply():
    funds = [_mixed("equity", 2.0, {}, 0.0)] + \
        [_mixed(f"p{i}", 0.5 + i * 0.1, {"AAA": 100.0}, 1.0) for i in range(5)]
    out = FS.score_category(funds)
    assert "(no debt)" in FS.explain(out[0])
    assert "(no debt)" not in FS.explain(out[-1])
