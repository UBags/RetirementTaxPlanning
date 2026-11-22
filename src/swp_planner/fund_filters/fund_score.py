# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""fund_score.py -- how risky is this fund, relative to its own category.

WITHIN CATEGORY, ALWAYS, AND THE COMPARISON IS MEANINGLESS OTHERWISE. A liquid
fund's Sharpe of 12 and a small-cap fund's Sharpe of 0.7 are not on the same
axis; ranking them together says nothing except which category was luckier.
Every score here is a percentile among peers, and `score_category` refuses to
rank a category with fewer than MIN_PEERS members rather than producing a
confident-looking number from three data points.

CREDIT IS A MULTIPLIER, NOT A WEIGHTED TERM, AND THAT IS THE ONE DESIGN CHOICE
WORTH ARGUING WITH. Averaging credit quality with Sharpe lets a fund buy its
way out of bad paper with good returns -- which is exactly backwards, because
the returns are earned in the years before the default and the default takes
them all back at once. Subordination and low-rated credit are JUMP risk: they
do not appear in the volatility series, so a fund holding them looks calm right
up until it does not. Franklin Templeton's six debt schemes had unremarkable
volatility the month before they froze.

So: performance and volatility produce a base score, and credit multiplies it
down. A fund cannot reach the top of its category on returns alone.

MISSING DATA SCORES AS BAD, NOT AS AVERAGE. A fund whose holdings are 40%
unrated is not an average-credit fund, it is a fund nobody has checked. Scoring
the unknown at the mean is how a screen quietly recommends the thing it could
not see -- the same failure as a check that returns zero on every file.

THE METRICS COME FROM get_funds_data.py, NOT FROM HERE. Sharpe, Sortino,
Calmar, rolling CAGR and max drawdown are already computed there per fund per
window and written to Fund_Metrics_Output.csv. Recomputing them would create a
second set of numbers that disagrees with the first in ways nobody would notice
for months. `from_metrics_row` adapts that file's columns; this module does no
arithmetic on NAV series at all.
"""
from __future__ import annotations

# The corporate single-issuer ceiling actually applied. SEBI's own limit is
# lower for some categories; this is the house rule and it is deliberately a
# quarter-point above the round number so that a fund sitting exactly at 3.00%
# by design is not flagged every week.
ISSUER_CAP = 3.25

# A category with fewer peers than this cannot support a percentile. Three
# funds ranked against each other give 0, 50 and 100 whatever their merits.
MIN_PEERS = 5

# Windows in the order get_funds_data writes them, and the order to prefer.
# Five years first: long enough to contain a drawdown, short enough that most
# funds have it. This mirrors the existing adj_quality convention rather than
# inventing a second one.
WINDOW_PREFERENCE = ("5Y", "10Y", "7Y", "3Y")

# Composite weights. They sum to 1 and they are a JUDGEMENT, not a result --
# stated here so they can be moved in one place and the effect measured.
WEIGHTS = dict(risk_adjusted=0.45, drawdown=0.25, volatility=0.20,
               history=0.10)

# How far credit can pull a fund down. 1.0 leaves it untouched; the floor is
# what a fund with the worst credit in its category keeps. Not zero, because a
# score of zero stops conveying how bad it is.
CREDIT_FLOOR = 0.35

# Years of NAV history at which the history sub-score saturates. Below this a
# fund is not worse, it is less known -- but for a thirty-year retirement plan
# less known is a reason to size smaller.
FULL_HISTORY_YEARS = 10.0

# Below this share of debt, credit is not a dimension of the fund and the
# multiplier is reported as inapplicable. It still scales continuously either
# side of the line; this is a label for reading, not a switch.
CREDIT_MATERIAL_SHARE = 0.05

# Below this, a fund has not been through a full market cycle and its Sharpe
# and Calmar are measuring one regime. It is still ranked -- excluding it would
# hide most of the thematic and multi-asset universe -- but flagged, so nobody
# reads a 14-month #1 as the same claim as a ten-year #1.
THIN_HISTORY_YEARS = 3.0


class ScoreError(ValueError):
    """Raised rather than returning a score that cannot mean anything."""


def _pick_window(row, stem, windows=WINDOW_PREFERENCE):
    """The first window for which this fund has the metric.

    Exact key match, so "Std_Dev" does not pick up "Max_Std_Dev" -- the two
    mean different things and silently taking the wrong one would be
    undetectable in the output.
    """
    for w in windows:
        v = row.get(f"{stem}_{w}")
        if v is not None and v == v:            # not None, not NaN
            return float(v), w
    return None, None


def _number(v):
    """A CSV cell as a float, or None. Blank is missing, not zero."""
    if v is None:
        return None
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s or s.lower() in ("nan", "na", "n/a", "-", "none"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return None if f != f else f


def from_metrics_row(row):
    """One Fund_Metrics_Output.csv row, as this module needs it.

    THE COLUMN NAMES ARE THE REAL ONES, read off the file rather than sniffed:
    Std_Dev_XY, Sharpe_XY, Sortino_XY, Max_DD_XY, Calmar_XY for the point
    metrics, Avg_CAGR_XY / Min_CAGR_XY / Max_Std_Dev_XY / Worst_Max_DD_XY for
    the rolling ones, plus History_Months and AMFI Code.

    VOLATILITY IS Std_Dev, NOT Max_Std_Dev. The second is the worst rolling
    window, which is a drawdown-adjacent measure and would double-count
    against Calmar; the first is the volatility everyone means.

    Tolerant of missing windows: a three-year-old fund has no 10Y column and
    that is a fact about the fund, not a parse error.
    """
    clean = {k: _number(v) for k, v in row.items()
             if isinstance(k, str) and k[-2:] in ("0Y", "7Y", "5Y", "3Y")}
    sharpe, w1 = _pick_window(clean, "Sharpe")
    sortino, w2 = _pick_window(clean, "Sortino")
    calmar, _ = _pick_window(clean, "Calmar")
    cagr, _ = _pick_window(clean, "Avg_CAGR")
    min_cagr, _ = _pick_window(clean, "Min_CAGR")
    std, _ = _pick_window(clean, "Std_Dev")
    if std is None:
        std, _ = _pick_window(clean, "Max_Std_Dev")
    dd, _ = _pick_window(clean, "Max_DD")
    if dd is None:
        dd, _ = _pick_window(clean, "Worst_Max_DD")
    months = _number(row.get("History_Months"))
    # THE WINDOW LABEL IS NOT THE WINDOW. get_funds_data slices iloc[-N:] for
    # each window, so a fund shorter than N reports that window computed on
    # everything it has -- a 20-month fund shows identical 3Y, 5Y, 7Y and 10Y
    # figures. Since MIN_HISTORY dropped from 36 to 12 there are hundreds of
    # such funds, and "Sharpe_5Y" on one of them is a 20-month Sharpe wearing
    # a five-year label.
    #
    # `window` therefore reports the EFFECTIVE period. A row that says "20m"
    # cannot be mistaken for one that says "5Y", which is the whole point:
    # ranking them together compares a fund through one market to a fund
    # through three.
    label = w1 or w2
    effective = label
    if months is not None and label:
        want = {"10Y": 120, "7Y": 84, "5Y": 60, "3Y": 36}.get(label)
        if want and months < want:
            effective = f"{int(months)}m"
    return dict(
        sharpe=sharpe, sortino=sortino, calmar=calmar, cagr=cagr,
        min_cagr=min_cagr, volatility=std, max_drawdown=dd,
        window=effective, window_label=label,
        history_years=None if months is None else months / 12,
        amfi_code=int(_number(row.get("AMFI Code")) or 0) or None,
        metrics_name=(row.get("Fund Name") or "").strip(),
        metrics_category=(row.get("Fund Type") or "").strip(),
        match_quality=(row.get("Match Quality") or "").strip())


def _pct_rank(value, peers, higher_is_better=True):
    """Percentile of `value` among `peers`, 0-100.

    None ranks LAST, not in the middle. A metric nobody computed is not an
    average metric.
    """
    known = [p for p in peers if p is not None]
    if value is None or not known:
        return 0.0
    if len(known) == 1:
        return 50.0
    below = sum(1 for p in known
                if (p < value if higher_is_better else p > value))
    ties = sum(1 for p in known if p == value)
    return round(100.0 * (below + 0.5 * ties) / len(known), 2)


# ── credit ──────────────────────────────────────────────────────────────────

def credit_quality(bands):
    """0-100 from the AAA / AA / Other / Unrated split.

    UNRATED IS SCORED AS THE WORST BAND, not skipped. A fund that is 40%
    unrated is not an average-credit fund, it is one nobody has checked, and
    treating the unknown as the mean is how a screen recommends what it could
    not see.
    """
    total = sum(float(v or 0.0) for v in (bands or {}).values())
    if total <= 0:
        return 0.0
    weight = {"AAA": 1.0, "AA": 0.6, "Other": 0.15, "Unrated": 0.0}
    got = sum(float(bands.get(b, 0.0) or 0.0) * w for b, w in weight.items())
    return round(100.0 * got / total, 2)


def breach_penalty(exposures, cap=ISSUER_CAP):
    """0-100, where 100 is no issuer over the cap.

    MEASURED BY EXCESS, NOT BY COUNT. One issuer at 9% is a worse fund than
    three at 3.5%, and a rule that counted breaches would say the opposite.
    QUASI-SOVEREIGN EXPOSURE IS SKIPPED, not merely down-weighted. A gilt fund
    is 100% one issuer and is not concentrated in any sense that matters, and
    the same holds for NABARD or NHAI. Entries may be plain numbers or the
    dicts `fund_store.issuer_exposures` returns; a dict carrying cap_exempt is
    honoured, a bare number cannot be exempt and is treated as binding.
    """
    def _pct(e):
        if isinstance(e, dict):
            return 0.0 if e.get("cap_exempt") else float(e.get("percent") or 0)
        return float(e or 0.0)

    excess = sum(max(0.0, _pct(e) - cap) for e in (exposures or []))
    if excess <= 0:
        return 100.0
    # Ten points of aggregate excess is a badly concentrated fund. Linear
    # rather than stepped so a fund at 3.3% is barely marked and one at 12%
    # is not merely "in breach" alongside it.
    return round(max(0.0, 100.0 - 10.0 * excess), 2)


def _exposure_pct(e):
    """A binding exposure's percentage; zero for an exempt one."""
    if isinstance(e, dict):
        return 0.0 if e.get("cap_exempt") else float(e.get("percent") or 0.0)
    return float(e or 0.0)


def credit_multiplier(quality, breach, debt_share=1.0, floor=CREDIT_FLOOR):
    """What credit does to the base score: 1.0 down to `floor`.

    A MULTIPLIER SO RETURNS CANNOT BUY IT OFF. Averaged in, a fund with a
    strong Sharpe and weak paper scores mid-table -- but the Sharpe was earned
    in the years before the default and the default takes it all back at once.

    SCALED BY HOW MUCH DEBT THE FUND ACTUALLY HOLDS, which the first version
    did not do and which shows up the moment the universe stops being all-debt.
    An equity fund has no bands because it has no bonds, and it was collecting
    a 0.545 multiplier for it -- harmless inside an all-equity category where
    the scaling is uniform, and wrong in a mixed one. Measured on a synthetic
    hybrid category, the fund with the BEST base score (84.2) fell to fourth
    for holding no debt.

    So the penalty applies to the credit-bearing part: a 100% debt fund takes
    it whole, a 30% debt fund takes 30% of it, an equity fund takes none. A
    debt fund whose paper could NOT be resolved still takes it in full -- that
    is a real gap, not an absent dimension.
    """
    combined = (float(quality) * 0.7 + float(breach) * 0.3) / 100.0
    raw = floor + (1.0 - floor) * max(0.0, min(1.0, combined))
    share = max(0.0, min(1.0, float(debt_share if debt_share is not None
                                    else 1.0)))
    return round(1.0 - share * (1.0 - raw), 4)


# ── the composite ───────────────────────────────────────────────────────────

def history_score(years, full=FULL_HISTORY_YEARS):
    """Short history is uncertainty, not badness -- but for a thirty-year plan
    uncertainty is a reason to size smaller, so it scores down."""
    if years is None:
        return 0.0
    return round(100.0 * min(1.0, max(0.0, float(years)) / full), 2)


def score_category(funds, cap=ISSUER_CAP, weights=None, min_peers=MIN_PEERS):
    """Score every fund in ONE category against the others in it.

    `funds` is a list of dicts, each carrying the metrics from
    `from_metrics_row` plus `bands` and `issuer_exposures`. Returns a new list
    with sub-scores, the composite, and the rank -- highest composite first.

    Refuses a category too small to rank, because three funds scored against
    each other produce 0, 50 and 100 whatever their merits.
    """
    if len(funds) < min_peers:
        raise ScoreError(
            f"{len(funds)} funds is too few to rank; a percentile needs at "
            f"least {min_peers} peers, and fewer produces a confident-looking "
            f"number from nothing")
    w = dict(WEIGHTS, **(weights or {}))

    sortinos = [f.get("sortino") for f in funds]
    calmars = [f.get("calmar") for f in funds]
    vols = [f.get("volatility") for f in funds]
    dds = [f.get("max_drawdown") for f in funds]

    out = []
    for f in funds:
        quality = credit_quality(f.get("bands"))
        breach = breach_penalty(f.get("issuer_exposures"), cap=cap)
        # debt_share defaults to 1.0 when unknown: assuming a fund has no debt
        # because nobody measured it would hand every unmeasured fund a free
        # pass, which is the opposite of what missing data should do.
        share = f.get("debt_share")
        mult = credit_multiplier(quality, breach, debt_share=share)

        # Sortino over Sharpe as the risk-adjusted term: upside deviation is
        # not risk to a retiree drawing down, and Sharpe punishes it.
        base = (
            w["risk_adjusted"] * _pct_rank(f.get("sortino"), sortinos)
            + w["drawdown"] * _pct_rank(f.get("calmar"), calmars)
            + w["volatility"] * _pct_rank(f.get("volatility"), vols,
                                          higher_is_better=False)
            + w["history"] * history_score(f.get("history_years")))

        years = f.get("history_years")
        out.append(dict(
            f, credit_quality=quality, breach_score=breach,
            thin_history=bool(years is not None and years < THIN_HISTORY_YEARS),
            debt_share=share,
            credit_applicable=bool(share is None
                                   or share >= CREDIT_MATERIAL_SHARE),
            credit_multiplier=mult, base_score=round(base, 2),
            composite=round(base * mult, 2),
            worst_issuer=max((_exposure_pct(e)
                              for e in (f.get("issuer_exposures") or [0])),
                             default=0.0),
            over_cap=sum(1 for e in (f.get("issuer_exposures") or [])
                         if _exposure_pct(e) > cap)))

    out.sort(key=lambda d: -d["composite"])
    n = len(out)
    for i, d in enumerate(out):
        d["rank"] = i + 1
        d["peers"] = n
        d["percentile"] = round(100.0 * (n - i - 0.5) / n, 2)
    # Drawdown percentile is computed on Calmar, which already divides return
    # by drawdown; `max_drawdown` itself is reported for reading, not scoring,
    # so it is not double-counted.
    for d in out:
        d["drawdown_percentile"] = _pct_rank(d.get("max_drawdown"), dds,
                                             higher_is_better=False)
    return out


def explain(row):
    """One line a person can argue with."""
    thin = "  THIN HISTORY" if row.get("thin_history") else ""
    if not row.get("credit_applicable", True):
        thin += "  (no debt)"
    return (f"#{row['rank']}/{row['peers']}  composite {row['composite']:.1f} "
            f"= base {row['base_score']:.1f} x credit {row['credit_multiplier']}"
            f"  (AAA-weighted {row['credit_quality']:.0f}, "
            f"worst issuer {row['worst_issuer']:.2f}%, "
            f"{row['over_cap']} over cap, {row.get('window') or '?'})"
            f"{thin}")
