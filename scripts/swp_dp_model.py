#!/usr/bin/env python3
"""Two-purse SWP model: folios, funds, vintages and the tax-free band.

A standalone research model for systematic withdrawal across a Personal purse
and an HUF purse under Indian new-regime tax. Self-contained -- standard
library only. Every input is a constant in the INPUTS block below.

    python3 swp_dp_model.py                        # 1 chunk, 30 yr, 5yr vintages
    python3 swp_dp_model.py --scenario 2chunk
    python3 swp_dp_model.py --scenario 3chunk
    python3 swp_dp_model.py --horizon 35 --vintage 0   # 0 = drain-triggered
    python3 swp_dp_model.py --no_vintage               # never open folios (cap 1)
    python3 swp_dp_model.py --sweep
    python3 swp_dp_model.py --quiet --csv out.csv

Runtime is roughly 8s for a 35-year run and several minutes for --sweep, because
the personal/HUF split is solved by replaying each year up to SPILL_GRID times.

WHAT THE MODEL DOES

Each year the plan owes a net cash requirement. Other income covers part; the
rest is raised by selling units, and every rupee of realised capital gain counts
against a tax-free band. Money sits in FOLIOS, and inside each folio in FUNDS,
each fund being its own FIFO lot queue -- CBDT Circular 704 applies FIFO per
scheme per folio, so 21 funds across several folios give many independent
queues and therefore a fine-grained choice of which basis to realise.

The withdrawal is drawn from the earliest folio that can fund the year without
breaching the band; folio Fn is brought in only when F1..F(n-1) cannot, and
within any set the draw is maximised from the earliest. Whatever band remains
is spent HARVESTING -- selling low-basis units and buying them straight back,
which realises gain tax-free and resets the basis. Harvest proceeds land in the
current receiver folio, rotating through triples of funds so each year forms an
identifiable, separately sellable vintage.

CURRENT RESULTS (all figures Rs lakh)

    scenario  horizon    vintage       tax    corpus
      1chunk       30        5yr     0.000    1498.6
      1chunk       35        5yr     1.500    1907.6
      2chunk       30        5yr     0.000    1502.2
      2chunk       35        5yr     1.500    1908.3
      3chunk       30        5yr     1.555    1512.3    <- see KNOWN BROKEN

* 3chunk does not fully hold its allocation. The transitions are one-way
  EMPTYING mandates ("f15-f21 must be empty by FY10"), so nothing keeps money
  in the destination funds afterwards. Three mechanisms now push back -- global
  drawing, protecting under-allocated funds from the cash draw, and aiming the
  harvest at the over-allocated side so its proceeds are a genuine transfer --
  and together they lift the low-rate share from 15.4% to 41-43%.

  Chunk 2 now essentially hits its target (36.1% against 33%). CHUNK 3 DOES
  NOT: 41.1% at FY30 against 67%, and it is not reachable. Closing that gap
  means shifting roughly 390 of value at a ~50% gain fraction, i.e. ~195 of
  realised gain, against perhaps 130 of band across the whole decade. The
  target is arithmetically out of reach tax-free, exactly as the FY4-FY10
  liquidation mandate was in an earlier version.

  So 3chunk's tax and corpus remain NOT comparable to 1chunk and 2chunk. The
  honest options are to accept a slower drift into the target mix, or to price
  the tax of hitting it on a deadline. There is no free version.

SURPLUS ROUTING -- TWO CONSTRAINTS, GUARDING DIFFERENT THINGS

Harvest surplus is routed by two rules that both bind:

  DEBT_FLOOR_X   liquidity. Surplus first rebuilds the combined debt corpus to
                 25 years of net need. Only what is left over may go to equity.
  EQUITY_MAX_SHARE  allocation. Equity is additionally capped at 10% of the
                 purse on deployment.

The floor alone is NOT sufficient, and the measurement is worth keeping:

    floor      tax    corpus   equity  eq share  debt cover
      25X   63.415    2344.5   1304.4    55.6%      31.2X
      30X    1.380    2060.5    380.7    18.5%      50.4X
      35X    1.239    1909.7      0.1     0.0%      57.3X
    both     0.486    2145.0    539.9    25.2%

At a 25X floor almost everything is spare -- the plan naturally runs at 57.3X
cover -- so equity balloons to 55.6% and tax reaches 63.4. ALL of that 63.4 is
SLAB tax on a starved debt book; equity tax is 0.000 throughout.

The reason is the point of the whole sleeve, inverted: s.112A caps the tax-free
equity drawdown at the ANNUAL EXEMPTION regardless of sleeve size. At FY35 a
1304 sleeve draws 2.22 of cash -- exactly what a 200 sleeve would draw. Every
rupee of equity past that adds ZERO tax-free capacity while displacing debt,
whose capacity scales with the slab band and the harvest. So equity must be
sized by what its exemption can absorb, not by what happens to be spare.

A GOTCHA WORTH REMEMBERING: every rupee of surplus must land somewhere. Two
successive versions of the routing silently DISCARDED the above-floor remainder
when equity declined it, which vanished 685 of corpus and produced a completely
convincing false conclusion. The control that catches it is cheap: with
EQUITY_ENABLED = False the routing must reproduce the no-equity run EXACTLY.
Run it after any change here.

THE EQUITY SLEEVE (s.112A)

MEASURED CONFIGURATION SWEEP -- do not re-tune without a reason. Bucket share
(6 values from 5% to 0.25%), cap basis (market vs cost), one-bucket-per-year,
20 vs 40 buckets, and an annual deployment throttle (6 values) were all tested.
NONE moves the 35-year answer beyond the ~0.3 noise floor, and the simplest
configuration wins. The sleeve's internal structure is inert here because the
surplus that would feed fresh cohorts is concentrated in FY5-FY8; by FY27 the
annual surplus is under 1. Sequential filling cannot separate vintages that
were never created, and a throttle can only redistribute money that is not
there. This is the same constraint that made a late vintage cadence inert on
the debt side.

Capping on cumulative COST rather than market value keeps the door open and
admits far more equity (929 against 569, corpus +102) but costs 3.0 of tax,
because surplus routed to equity starves the debt purse. Market-value capping
is the better trade.

A separate folio F_EQ per purse, N_EQ_FUNDS buckets, funded from harvest
surplus only -- never by selling debt to buy equity. No lot is sold before it
qualifies as long term. Equity LTCG is a SEPARATE tax head: a flat rate above
its own annual exemption, and not eligible for the s.87A rebate, so it neither
consumes the slab band nor is sheltered by it.

    1chunk 35yr 5yr:   no sleeve  tax 1.500  corpus 1907.6
                       with       tax 0.486  corpus 2161.8   (+254, +13%)

THE TRIGGER IS JOINT, AND THAT IS WHERE THE VALUE IS. Personal + HUF corpus
must exceed EQUITY_TRIGGER_X years of the post-tax amount the two purses
jointly fund by redemption. An earlier per-purse version never opened the HUF
sleeve at all (it held ~290 against 25 x its ~12 draw), so only one s.112A
exemption was ever in play. Moving to the joint test was worth 1.239 -> 0.655.

SEQUENTIAL BUCKET FILLING IS INERT HERE, and the reason is worth recording.
Buckets are filled one at a time to EQ_BUCKET_SHARE of the excess over the
trigger, the pointer carrying across years and a filled bucket never topped up
until the cycle wraps. The intent was vintage separation inside the sleeve, so
a draw could take a one- or two-year-old bucket at a ~16% gain fraction instead
of the sleeve average at ~72%.

Measured, it is worth 0.169 -- below the noise floor:

    joint trigger + equal spread   tax 0.655   corpus 2162.5
    joint trigger + sequential     tax 0.486   corpus 2161.8

It fails for the same reason the accelerated late vintage cadence failed on the
debt side: THE SLEEVE STOPS BEING FED AT FY8. All deployment happens in FY5-FY8
and never resumes, so every bucket is filled inside a four-year window and all
of them are 24+ years old by FY32. Observed gain fractions on equity draws run
78-91%, not the 16% the design intended. The machinery is correct; there is
simply nothing for it to separate. It would start to matter if the sleeve were
topped up through the horizon.

THE 10% CAP IS A DEPLOYMENT CAP, NOT A HOLDING. EQUITY_MAX_SHARE limits what is
paid IN; nothing trims back. Equity compounds at 9% against debt's 7% for three
decades, so a 10% deployment cap produces a 26.3% holding by FY35. Raising it
trades tax for corpus, and 10% is already close to the tax optimum:

    cap    tax    corpus   end share
    10%  0.486   2161.8       26.3%
    15%  0.810   2241.1       35.0%
    20%  1.077   2283.3       39.4%
    30%  2.889   2360.2       48.2%

Tax rises with the cap because money in equity is money the debt purse cannot
use to manage its band, while the s.112A exemption stays fixed regardless of
sleeve size. Whether a 26% equity holding at age 90 is acceptable is a risk
question this model cannot answer -- see below.

IT MAKES 3chunk WORSE (1.555 -> 2.610 at 30 years). Surplus diverted into
equity is surplus the debt purse no longer has for its transitions. Corpus
still rises, so this is a genuine trade rather than a defect.

SIGMA IS NOT MODELLED. EQUITY_SIGMA is carried as a documented constant and
used nowhere. This model is deterministic; a 12.5% standard deviation cannot do
anything here except create false precision. Using it properly needs a Monte
Carlo wrapper that re-runs the horizon over sampled return paths and reports a
distribution of tax and corpus -- at which point the interesting question stops
being "what is the tax" and becomes "what is the probability the plan fails".
That is a different model, not a flag.

RESOLVED, AND WHY IT MATTERS

* Folio count is now monotone. One folio no longer beats several in any of the
  six scenario/horizon combinations. The cause was NOT an implementation
  defect: fund_year used lexicographic widening, which faithfully implemented
  the instruction "maximise the draw from the earliest folio". The earliest
  folio is the oldest and therefore the most gain-heavy, so forcing the cash
  draw through it burned band that fresher units could have supplied more
  cheaply, and walled off the low-basis stock the transition had just placed in
  the receiver. Drawing globally across all live folios fixed it.

  The fix costs tax where the band binds -- 1chunk/35/5yr 1.166 -> 1.500,
  3chunk/35/5yr 2.214 -> 3.331 before allocation work. Most of that is at or
  under the noise floor; 3chunk/35 is not, and 3chunk is the scenario already
  flagged above.

  Worth recording: this is the third time in this model's history that "draw
  from the oldest holding first" has lost. It is an intuitive rule and it has
  been wrong every time, because the band is better spent on the harvest, which
  RETAINS units at a stepped-up basis, than on a cash draw, which removes them.

INSTRUMENTATION TRAP

`simulate` REASSIGNS `personal` and `huf` each year (`personal, huf =
best["p"], best["h"]`) because the spillover search commits a deep copy. Any
diagnostic that captures the Book by patching `Book.__init__` therefore holds a
stale object from year one and will report nonsense. Measure from inside the
year loop instead. This produced two wrong readings of the 3chunk allocation
(97.3% reported against a true 15.4%) before it was noticed.

AUDIT CLAIMS EXAMINED AND REJECTED -- do not re-litigate without new evidence

* "Bisection in fund_year is unsafe because the feasibility predicate is not
  monotone." True in principle, never observed: zero non-monotone cases across
  80 sampled real states from 1chunk and 3chunk, scanning x on a 41-point grid.
  Structurally safe because folio k is always the oldest, folios open in time
  order, and money only flows forward -- so newer folios always hold
  higher-basis stock and the marginal-rate ordering cannot invert.

* "Fund.sell liquidates zero-gain lots without a cap during harvesting." True
  in principle, unreachable: zero events across both scenarios over ~3500 of
  harvest proceeds. Zero-gain lots are the NEWEST, so FIFO reaches them last
  and the gain budget is exhausted first; and a fund whose older lots are gone
  has front_frac near zero, so the min_gain_frac filter drops it from the pool.

The rotation-pointer claim WAS accepted and fixed -- see Folio.invest.
"""
from __future__ import annotations

import argparse
import copy
import csv
import sys

# ═══════════════════════════════════════════════════════════════════════════
#  INPUTS -- everything below this line is a dial
# ═══════════════════════════════════════════════════════════════════════════

# ---- the money, in Rs lakh -------------------------------------------------
PERSONAL_CORPUS = 350.0     # starting personal corpus, spread evenly on FY0
HUF_WINDFALL = 190.0        # arrives in the HUF, clean under s.64(2)
HUF_YEAR = 5                # the FY the windfall lands
HUF_RETURN = 0.070          # the HUF earns a flat rate; no chunk structure

# ---- the horizon -----------------------------------------------------------
HORIZON = 30                # 30 or 35; ladders below extend to 35

# ---- structure -------------------------------------------------------------
N_FUNDS = 21                # funds per folio, each its own FIFO queue
TRIPLE = 3                  # a year's surplus is split across this many funds
FOLIO_CAP = 8               # maximum cohorts per purse
VINTAGE_EVERY = 5           # open a fresh receiver every N years.
                            # 0 = the old drain-triggered rule (much worse)

# ---- the requirement ladder (Rs lakh/yr, one entry per 5-year block) --------
REQUIREMENT_BLOCKS = [35.0, 37.5, 42.5, 45.0, 47.5, 50.0, 52.5]

# ---- other income ----------------------------------------------------------
OTHER_INCOME_START = 15.80  # FY1 gross other income
OTHER_INCOME_STEP = 0.10    # flat increment per year
TAXABLE_SHARE = 5.95 / 15.80    # the taxable fraction of other income

# ---- statutory limits, projected forward (one entry per 5-year block) ------
EXEMPT_BLOCKS = [12.0, 14.0, 16.0, 18.0, 20.0, 20.0, 20.0]   # s.87A ceiling
NILBAND_BLOCKS = [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]        # HUF basic exemption

# ---- tax -------------------------------------------------------------------
# New-regime ladder expressed as a WIDTH and a rate list rather than absolute
# boundaries, so it can be anchored on whatever basic exemption applies in a
# given year. NILBAND is projected forward (4.0 -> 10.0); a fixed ladder kept
# charging from 4.0 regardless, which made the projection cosmetic and left a
# discontinuity at the threshold (0.000 at 10.00, 0.417 at 10.01).
BRACKET_WIDTH = 4.0
SLAB_RATES = [0.05, 0.10, 0.15, 0.20, 0.25]
TOP_RATE = 0.30
CESS = 1.04                 # health and education cess
EXIT_LOAD = 0.01            # charged on units sold in their year of purchase

# ---- the equity sleeve (s.112A) --------------------------------------------
# Equity LTCG is a SEPARATE tax head: a flat rate above its own annual
# exemption, and NOT eligible for the s.87A rebate. So it neither consumes the
# slab band nor is sheltered by it -- the exemption is additive capacity on top
# of everything else, once per assessee.
EQUITY_ENABLED = True
EQUITY_RETURN = 0.090       # deterministic CAGR
EQUITY_SIGMA = 0.125        # NOT USED -- deterministic model. See docstring.
N_EQ_FUNDS = 20             # eq_f1 .. eq_f20, filled SEQUENTIALLY not equally
EQ_BUCKET_SHARE = 0.05      # each bucket is filled to this share of the excess
                            # over the floor. 20 x 5% = the whole excess.
DEBT_FLOOR_X = 25.0         # The combined DEBT corpus must not be allowed to
                            # fall below this many years of net cash need.
                            # Surplus rebuilds debt to the floor FIRST; only
                            # what is left over goes to equity. This replaces
                            # an arbitrary 10% equity cap -- the floor is the
                            # real constraint, and equity is funded from
                            # genuinely spare capacity rather than a quota.
                            # The same mark is the TRIGGER (above it there is
                            # spare capacity, below it there is none) and the
                            # base against which each equity bucket is sized.
EQUITY_MAX_SHARE = 0.10     # ...and equity is ALSO capped as a share of the
                            # purse. Both bind, and they guard different things.
                            # The floor protects LIQUIDITY: never hold less
                            # debt than the liability needs. The share cap
                            # protects ALLOCATION: s.112A caps the tax-free
                            # equity drawdown at the exemption regardless of
                            # sleeve size, so equity past a point adds no
                            # capacity while displacing debt that has some.
                            # MEASURED at 35yr: floor alone at 25X gives 55.6%
                            # equity and tax 63.4, of which 63.4 is SLAB tax on
                            # a starved debt book while the 1304 sleeve draws
                            # just 2.22/yr. Both constraints together: 0.486.
EQUITY_MIN_HOLD = 1         # years before a lot qualifies as LTCG (s.2(42A))

# per 5-year block, so both can be re-projected like the slab ladders
EQ_EXEMPT_BLOCKS = [1.25, 1.25, 1.50, 1.50, 1.75, 1.75, 2.00]   # s.112A exemption
EQ_RATE_BLOCKS = [0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125]  # s.112A rate

# ---- the HUF draw ----------------------------------------------------------
RATION_K = 1.195            # HUF offer = K * huf_value / years_remaining,
                            # capped at the HUF's own tax-free capacity

# ---- scenarios: what each fund earns, and any mandated fund switches -------
#   rate(fund_index, fy) -> annual return
#   transitions: (window_start_fy, deadline_fy, funds_to_exit, funds_to_enter)
#                the exiting funds must be EMPTIED by the deadline

def _grp(a, b):
    return list(range(a - 1, b))        # 1-based fund numbers, inclusive


SCENARIOS = {
    # all 21 funds at a single rate for the whole horizon
    "1chunk": dict(
        n_funds=21,
        rate=lambda k, fy: 0.070,
        transitions=[],
    ),
    # two 15-year chunks. The SAME funds re-rate at FY16 -- nothing is sold,
    # so there is no transition cost at all.
    "2chunk": dict(
        n_funds=21,
        rate=lambda k, fy: (0.070 if k in _grp(1, 11)
                            else (0.075 if fy <= 15 else 0.065)),
        transitions=[],
    ),
    # three 10-year chunks with MANDATED switches into new funds:
    #   f15-f21 must be emptied into f22-f28 by FY10 (window starts FY1)
    #   f8-f14  must be emptied into f29-f35 by FY20 (window starts FY11)
    # Blended: 7.25% / 7.00% / 6.75%.
    "3chunk": dict(
        n_funds=35,
        rate=lambda k, fy: (0.0725 if k < 21 else 0.0650),
        transitions=[(1, 10, _grp(15, 21), _grp(22, 28)),
                     (11, 20, _grp(8, 14), _grp(29, 35))],
        # Share of personal value that should sit in the incoming 6.50% funds.
        # Without this the transitions are one-way emptying mandates and
        # nothing holds the destination: money arrives with the freshest basis
        # and the cheapest-first withdrawal takes it straight back out, so the
        # plan drifts to ~97% in the 7.25% funds against a 33% target.
        target_low_share=lambda fy: (0.0 if fy <= 10
                                     else 1.0 / 3.0 if fy <= 20
                                     else 2.0 / 3.0),
    ),
}

EPS = 1e-12
SPILL_GRID = 8   # search resolution for the personal/HUF split
EQ_GRID = 4      # search resolution for the equity draw
HOLD_ALLOCATION = True
"""Protect the destination funds so the chunk's target mix is held.

MEASURED, 3chunk / 30yr / 5yr vintages:

    off   tax 1.448   low-rate share at FY30  15.4%
    on    tax 2.421   low-rate share at FY30  19.9%   (target 67%)

So it buys 4.5 points of allocation fidelity for 0.97 of tax, and still falls
far short. Left OFF by default because a partial fix that costs real money is
worse than an honest gap. The reason it cannot get there: the transitions are
one-way EMPTYING mandates, and most of the exiting funds are drained by the
ordinary withdrawal (which sends the money out as cash) rather than by the
transition (which would route it into the destination funds). Reaching the
target needs the mandate re-expressed as a share to maintain, not funds to
empty -- a design change, not a parameter.
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Ladders
# ═══════════════════════════════════════════════════════════════════════════

def build_ladders(horizon):
    n_blocks = (horizon + 4) // 5

    def spread(blocks):
        out = []
        for v in blocks[:n_blocks]:
            out += [v] * 5
        return out[:horizon]

    oi = [OTHER_INCOME_START + OTHER_INCOME_STEP * k for k in range(horizon)]
    return dict(
        requirement=spread(REQUIREMENT_BLOCKS),
        exempt=spread(EXEMPT_BLOCKS),
        nilband=spread(NILBAND_BLOCKS),
        other_income=oi,
        taxable_other=[x * TAXABLE_SHARE for x in oi],
        eq_exempt=spread(EQ_EXEMPT_BLOCKS),
        eq_rate=spread(EQ_RATE_BLOCKS),
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Tax
# ═══════════════════════════════════════════════════════════════════════════

def slab_tax(income, basic_exemption=BRACKET_WIDTH):
    """Slab tax with the ladder anchored on `basic_exemption`.

    Continuous by construction: income at the exemption pays nothing and the
    first rupee above it pays the first slab rate, not a full recomputation
    from a stale anchor.
    """
    if income <= basic_exemption:
        return 0.0
    rem, tax = income - basic_exemption, 0.0
    for rate in SLAB_RATES:
        if rem <= 0:
            break
        take = min(rem, BRACKET_WIDTH)
        tax += take * rate
        rem -= take
    return tax + max(0.0, rem) * TOP_RATE


def personal_tax(total_income, exempt_ceiling, basic_exemption=BRACKET_WIDTH):
    """Slab tax with the s.87A rebate and its marginal relief.

    Two separate thresholds, and they are not the same number: the BASIC
    EXEMPTION anchors the slab ladder, while the s.87A CEILING is the income
    below which the rebate wipes the bill out entirely.
    """
    if total_income <= exempt_ceiling:
        return 0.0
    return min(slab_tax(total_income, basic_exemption),
               total_income - exempt_ceiling) * CESS


def equity_tax(gain, exemption, rate):
    """s.112A long-term capital gain on listed equity.

    A flat rate above an annual exemption, per assessee. Critically it is NOT
    eligible for the s.87A rebate, so it sits outside the slab computation
    entirely: it neither consumes the debt band nor is wiped out by the rebate.
    """
    return max(0.0, gain - exemption) * rate * CESS


def huf_tax(gain, nil_band):
    """HUFs get the basic exemption but NOT the s.87A rebate.

    The ladder is anchored on nil_band, so this is continuous at the threshold.
    """
    return slab_tax(gain, nil_band) * CESS


# ═══════════════════════════════════════════════════════════════════════════
#  Fund -- one FIFO lot queue
# ═══════════════════════════════════════════════════════════════════════════

class Fund:
    __slots__ = ("lots",)

    def __init__(self):
        self.lots = []                      # [units, purchase_nav, buy_fy]

    def invest(self, amount, nav, fy):
        if amount > EPS:
            self.lots.append([amount / nav, nav, fy])

    def units(self):
        return sum(l[0] for l in self.lots)

    def value(self, nav):
        return self.units() * nav

    def front_frac(self, nav):
        """Gain fraction of the next rupee sold. Inf if empty."""
        for u, basis, _ in self.lots:
            if u > EPS:
                return (nav - basis) / nav
        return float("inf")

    def sell(self, nav, fy, amount=None, gain_cap=None, max_buy_fy=None):
        """FIFO. Returns (gross proceeds, gain realised, exit load)."""
        gross = gain = load = 0.0
        for lot in self.lots:
            if lot[0] <= EPS:
                continue
            # a lot bought after max_buy_fy has not yet qualified as long term
            if max_buy_fy is not None and lot[2] > max_buy_fy:
                continue
            if amount is not None and amount - gross <= EPS:
                break
            if gain_cap is not None and gain_cap - gain <= EPS:
                break
            frac = (nav - lot[1]) / nav
            take = lot[0] * nav
            if amount is not None:
                take = min(take, amount - gross)
            if gain_cap is not None and frac > EPS:
                take = min(take, (gain_cap - gain) / frac)
            if take <= EPS:
                continue
            gross += take
            gain += take * frac
            if lot[2] == fy:
                load += take * EXIT_LOAD
            lot[0] -= take / nav
        self.lots = [l for l in self.lots if l[0] > EPS]
        return gross, gain, load


# ═══════════════════════════════════════════════════════════════════════════
#  Folio -- N funds, plus a rotation pointer for incoming money
# ═══════════════════════════════════════════════════════════════════════════

class Folio:
    def __init__(self, n_funds):
        self.funds = [Fund() for _ in range(n_funds)]
        self.ptr = 0

    def value(self, navs):
        return sum(f.value(navs[k]) for k, f in enumerate(self.funds))

    def units(self):
        return sum(f.units() for f in self.funds)

    def seed(self, total, navs, fy, idxs):
        for k in idxs:
            self.funds[k].invest(total / len(idxs), navs[k], fy)

    def invest_direct(self, amount, navs, fy, target_funds):
        """Invest directly into target funds without touching the rotation pointer."""
        if amount <= EPS or not target_funds:
            return
        share = amount / len(target_funds)
        for k in target_funds:
            self.funds[k].invest(share, navs[k], fy)

    def invest(self, amount, navs, fy, eligible):
        """Next triple in the rotation, so each year is a distinct vintage."""
        if amount <= EPS or not eligible:
            return
        n = len(eligible)
        picks = [eligible[(self.ptr * TRIPLE + j) % n] for j in range(TRIPLE)]
        for k in picks:
            self.funds[k].invest(amount / TRIPLE, navs[k], fy)
        # Advance by TRIPLE modulo n, not by 1 modulo (n // TRIPLE). The
        # truncating form orphans the tail whenever len(eligible) is not a
        # multiple of TRIPLE -- at n = 20 it never reaches funds 18 and 19.
        # Every shipped scenario happens to have n = 21, so this is insurance
        # against the next change to N_FUNDS or the exit groups, not a live bug.
        self.ptr = (self.ptr + TRIPLE) % n


# ═══════════════════════════════════════════════════════════════════════════
#  Equity sleeve -- one folio, N equal funds, its own tax head
# ═══════════════════════════════════════════════════════════════════════════

class Sleeve:
    """The equity folio F_EQ. Buckets are filled SEQUENTIALLY, not equally.

    Equal allocation across every fund gives every fund the same vintage mix,
    so every FIFO front lot is as old as the sleeve itself and the gain
    fraction is ~72% by the time the exemption is needed. Filling one bucket at
    a time makes each bucket a distinct vintage: the most recently filled one
    is a year or two old with a gain fraction near 16%, so the SAME s.112A
    exemption frees roughly four times the cash.

    A bucket is filled to EQ_BUCKET_SHARE of the current excess over the
    trigger, then the pointer advances. A filled bucket is NOT topped up when
    the excess grows -- it stays as it is until the cycle reaches eq_f20 and
    wraps back to eq_f1.
    """

    def __init__(self, n=N_EQ_FUNDS):
        self.funds = [Fund() for _ in range(n)]
        self.ptr = 0            # bucket currently being filled
        self.in_current = 0.0   # deployed into that bucket this cycle

    def value(self, nav):
        return sum(f.value(nav) for f in self.funds)

    def buckets_used(self):
        """How many of the N buckets currently hold anything."""
        return sum(1 for f in self.funds if f.units() > EPS)

    def cost(self):
        """Cost basis of everything still held."""
        return sum(u * b for f in self.funds for u, b, _y in f.lots)

    def invest(self, amount, nav, fy, bucket_target=None):
        """Deploy `amount`, filling buckets in order from the current pointer."""
        if amount <= EPS:
            return
        n = len(self.funds)
        if bucket_target is None or bucket_target <= EPS:
            bucket_target = amount / n      # degenerate: spread it
        left = amount
        for _ in range(2 * n + 1):          # bounded: at most one full cycle
            if left <= EPS:
                break
            room = max(0.0, bucket_target - self.in_current)
            if room <= EPS:
                self.ptr = (self.ptr + 1) % n
                self.in_current = 0.0
                continue
            put = min(left, room)
            self.funds[self.ptr].invest(put, nav, fy)
            self.in_current += put
            left -= put
            if self.in_current >= bucket_target - EPS:
                self.ptr = (self.ptr + 1) % n
                self.in_current = 0.0

    def sell(self, nav, fy, amount=None, gain_cap=None):
        """Cheapest bucket first -- i.e. the most recently filled vintage.

        LTCG-qualified lots only.
        """
        cutoff = fy - EQUITY_MIN_HOLD
        order = sorted(self.funds, key=lambda f: f.front_frac(nav))
        gross = gain = 0.0
        for f in order:
            if amount is not None and amount - gross <= EPS:
                break
            if gain_cap is not None and gain_cap - gain <= EPS:
                break
            a = None if amount is None else amount - gross
            c = None if gain_cap is None else gain_cap - gain
            g, gn, _ld = f.sell(nav, fy, amount=a, gain_cap=c, max_buy_fy=cutoff)
            gross += g
            gain += gn
        return gross, gain

    def capacity(self, nav, fy, gain_cap):
        """Cash raisable while keeping realised gain within `gain_cap`."""
        probe = copy.deepcopy(self)
        return probe.sell(nav, fy, gain_cap=gain_cap)[0]


# ═══════════════════════════════════════════════════════════════════════════
#  Book -- one purse: an ordered list of folios
# ═══════════════════════════════════════════════════════════════════════════

class Book:
    def __init__(self, cap, n_funds):
        self.cap, self.n_funds = cap, n_funds
        self.folios = [Folio(n_funds)]
        self.receiver = 0

    def value(self, navs):
        return sum(f.value(navs) for f in self.folios)

    def live(self, navs):
        return [k for k, f in enumerate(self.folios) if f.units() > EPS]

    def group_value(self, navs, idxs):
        return sum(fo.funds[k].value(navs[k])
                   for fo in self.folios for k in idxs)

    def open_next(self):
        if len(self.folios) < self.cap:
            self.folios.append(Folio(self.n_funds))
            self.receiver = len(self.folios) - 1

    def put_direct(self, amount, navs, fy, target_funds):
        """Invest directly into active receiver folio without advancing rotation pointer."""
        if amount > EPS:
            self.folios[self.receiver].invest_direct(amount, navs, fy, target_funds)

    def put(self, amount, navs, fy, eligible):
        """Surplus goes ONLY to the active receiver folio.

        Which folio is the receiver, and when it is replaced, was the dominant
        lever before the tax ladder was fixed. Opening a fresh receiver on a
        fixed cadence beats waiting for the current one to be drained, because
        the drain-triggered rule creates cohorts exactly when the surplus that
        would fill them has already dried up.
        """
        if amount > EPS:
            self.folios[self.receiver].invest(amount, navs, fy, eligible)

    # -- raise cash, cheapest fund first (minimum gain per rupee) ------------
    def draw(self, navs, fy, folio_idxs=None, amount=None, gain_cap=None,
             only=None, prefer=None, high_gain_first=False, min_gain_frac=0.0,
             protect=None):
        idxs = range(len(self.folios)) if folio_idxs is None else folio_idxs
        pool = []
        for fi in idxs:
            for k, f in enumerate(self.folios[fi].funds):
                if only is not None and k not in only:
                    continue
                if f.units() > EPS:
                    # Skip zero-gain / newly seeded lots during harvesting to prevent churn
                    if min_gain_frac > EPS and f.front_frac(navs[k]) < min_gain_frac:
                        continue
                    pool.append((fi, k, f))

        def sort_key(t):
            # protected funds go to the BACK: they are still available if the
            # year cannot be funded without them, but nothing else is taken
            # from them first.
            guard = 1 if (protect is not None and t[1] in protect) else 0
            prio = 0 if (prefer is not None and t[1] in prefer) else 1
            ff = t[2].front_frac(navs[t[1]])
            frac = -ff if high_gain_first else ff
            return (guard, prio, frac)

        pool.sort(key=sort_key)
        gross = gain = load = 0.0
        per = {}
        for fi, k, f in pool:
            if amount is not None and amount - (gross - load) <= EPS:
                break
            if gain_cap is not None and gain_cap - gain <= EPS:
                break
            a = None if amount is None else amount - (gross - load)
            c = None if gain_cap is None else gain_cap - gain
            g, gn, ld = f.sell(navs[k], fy, amount=a, gain_cap=c)
            gross += g
            gain += gn
            load += ld
            per[fi] = per.get(fi, 0.0) + g
        return gross - load, gain, per

    # -- fund the year ------------------------------------------------------
    def fund_year(self, need, band, navs, fy, eligible, prefer=None,
                  protect=None, harvest_only=None):
        """Fund the year, bringing in Fn only when F1..F(n-1) cannot do it tax-free.

        Within whichever set is used the draw is maximised from the EARLIEST
        folio, by bisection on the cash taken from it. The bisection assumes
        the feasibility predicate is monotone in that quantity; see the module
        docstring for why that holds structurally here.

        Returns (sold per folio, cash raised, surplus harvested, gain, touched).
        """
        sold = {}
        lv = self.live(navs)
        if not lv:
            return sold, 0.0, 0.0, 0.0, False

        if need <= EPS:
            surplus = 0.0
            gain_tot = 0.0
            if band > EPS:
                pr, g3, per3 = self.draw(navs, fy, folio_idxs=lv, gain_cap=band,
                                         prefer=prefer, high_gain_first=True,
                                         min_gain_frac=0.01, protect=protect)
                surplus = pr
                gain_tot = g3
                for fi, amt in per3.items():
                    sold[fi] = sold.get(fi, 0.0) + amt
            return (sold, 0.0, surplus, gain_tot, sold.get(self.receiver, 0.0) > EPS)

        # GLOBAL DRAW across every live folio. The previous lexicographic
        # widening (subset = lv[:n]) maximised the draw from the EARLIEST
        # folio, which is the oldest and therefore the most gain-heavy. That
        # walled off fresh low-gain units sitting in the receiver folio, and is
        # the suspected cause of one folio beating four.
        probe = copy.deepcopy(self)
        got, gmin, _ = probe.draw(navs, fy, folio_idxs=lv, amount=need,
                                  prefer=prefer, high_gain_first=False,
                                  protect=protect)

        if got >= need - 1e-6 and gmin <= band + 1e-9:
            lo, hi, best = 0.0, need, 0.0
            for _ in range(35):
                mid = (lo + hi) / 2
                trial = copy.deepcopy(self)
                a, ga, _ = trial.draw(navs, fy, folio_idxs=lv, amount=mid,
                                      prefer=prefer, high_gain_first=True,
                                      protect=protect)
                rem = max(0.0, need - a)
                b, gb, _ = trial.draw(navs, fy, folio_idxs=lv, amount=rem,
                                      prefer=prefer, high_gain_first=False,
                                      protect=protect)
                if (a + b >= need - 1e-6) and (ga + gb <= band + 1e-9):
                    best, lo = mid, mid
                else:
                    hi = mid

            a, ga, per1 = self.draw(navs, fy, folio_idxs=lv, amount=best,
                                    prefer=prefer, high_gain_first=True,
                                    protect=protect)
            for fi, amt in per1.items():
                sold[fi] = sold.get(fi, 0.0) + amt
            rem_need = max(0.0, need - a)
            band_left = max(0.0, band - ga)
            gain_tot = ga
            if rem_need > 1e-6:
                b, gb, per2 = self.draw(navs, fy, folio_idxs=lv,
                                        amount=rem_need, prefer=prefer,
                                        high_gain_first=False, protect=protect)
                for fi, amt in per2.items():
                    sold[fi] = sold.get(fi, 0.0) + amt
                band_left = max(0.0, band_left - gb)
                gain_tot += gb

            surplus = 0.0
            if band_left > EPS:
                pr, g3, per3 = self.draw(navs, fy, folio_idxs=lv,
                                         gain_cap=band_left, prefer=prefer,
                                         high_gain_first=True,
                                         min_gain_frac=0.01, protect=protect,
                                         only=harvest_only)
                surplus = pr
                gain_tot += g3
                for fi, amt in per3.items():
                    sold[fi] = sold.get(fi, 0.0) + amt
            return (sold, need, surplus, gain_tot,
                    sold.get(self.receiver, 0.0) > EPS)

        # nothing reaches zero tax: raise it at minimum gain and pay
        got, gain, per = self.draw(navs, fy, amount=need, prefer=prefer, high_gain_first=False, protect=protect)
        for fi, amt in per.items():
            sold[fi] = sold.get(fi, 0.0) + amt
        return sold, got, 0.0, gain, sold.get(self.receiver, 0.0) > EPS


# ═══════════════════════════════════════════════════════════════════════════
#  Fixed-Point Tax Payment Engine
# ═══════════════════════════════════════════════════════════════════════════

def pay_tax_bill(book, initial_tax, base_gain, navs, fy, is_huf=False,
                 nil_band=0.0, exempt_ceiling=0.0, taxable_other=0.0,
                 basic_exemption=BRACKET_WIDTH):
    """Sell units to pay `initial_tax`, converging on the tax-on-tax circularity.

    `base_gain` is the gain the year has ALREADY realised. It must be passed in:
    an earlier version recomputed the bill from the tax-sale gain alone, so the
    loop exited after one pass and up to 7.4 of tax was recorded but never
    removed from the corpus. Conservation now holds to ~4e-6, which is the
    10-iteration tolerance rather than a leak.
    """
    if initial_tax <= EPS or book.value(navs) <= EPS:
        return 0.0, 0.0

    current_tax_needed = initial_tax
    total_tax_paid = 0.0
    total_tax_gain = 0.0

    for _ in range(10):
        if current_tax_needed <= EPS:
            break
        drawn_cash, add_gain, _ = book.draw(navs, fy, amount=current_tax_needed, high_gain_first=False)
        total_tax_paid += drawn_cash
        total_tax_gain += add_gain

        total_realized_gain = base_gain + total_tax_gain
        if is_huf:
            new_tax = huf_tax(total_realized_gain, nil_band)
        else:
            new_tax = personal_tax(taxable_other + total_realized_gain,
                                   exempt_ceiling, basic_exemption)

        current_tax_needed = max(0.0, new_tax - total_tax_paid)

    return total_tax_paid, total_tax_gain


# ═══════════════════════════════════════════════════════════════════════════
#  The simulation
# ═══════════════════════════════════════════════════════════════════════════

def simulate(scenario="1chunk", horizon=HORIZON, cap=FOLIO_CAP,
             vintage_every=VINTAGE_EVERY, no_vintage=False, verbose=False):
    spec = SCENARIOS[scenario]
    nf = spec["n_funds"]
    lad = build_ladders(horizon)

    if no_vintage:
        cap = 1
        vintage_every = 0

    exiting_all = set()
    for _s, _e, out, _into in spec["transitions"]:
        exiting_all |= set(out)
    base_eligible = [k for k in range(nf) if k not in exiting_all]
    low_funds = [k for k in base_eligible if spec["rate"](k, 1) < 0.070]
    high_funds = [k for k in base_eligible if k not in set(low_funds)]
    target_share = spec.get("target_low_share")

    personal = Book(cap, nf)
    huf = Book(cap, nf)
    eq_p, eq_h = Sleeve(), Sleeve()
    nav_eq = 1.0
    navs = [1.0] * nf
    nav_h = 1.0

    start_funds = [k for k in range(nf)
                   if spec["rate"](k, 1) > 0 and k < 21]
    personal.folios[0].seed(PERSONAL_CORPUS, navs, 0, start_funds)

    wins = [dict(start=s, end=e, out=out, into=into, armed=False, still=0.0)
            for s, e, out, into in spec["transitions"]]

    tax_p_tot = tax_h_tot = tax_eq_tot = 0.0
    rows = []

    for fy in range(1, horizon + 1):
        i = fy - 1
        for k in range(nf):
            navs[k] *= (1.0 + spec["rate"](k, fy))
        if fy == HUF_YEAR:
            huf.folios[0].seed(HUF_WINDFALL, [nav_h] * nf, fy, start_funds)
        elif fy > HUF_YEAR:
            nav_h *= (1.0 + HUF_RETURN)
        hnavs = [nav_h] * nf
        if EQUITY_ENABLED:
            nav_eq *= (1.0 + EQUITY_RETURN)
        eq_exempt, eq_rate = lad["eq_exempt"][i], lad["eq_rate"][i]

        need = lad["requirement"][i] - lad["other_income"][i]
        band_p = lad["exempt"][i] - lad["taxable_other"][i]
        band_h = lad["nilband"][i]

        # scheduled vintage -- the single most valuable dial in this model
        if vintage_every and fy > 1 and (fy - 1) % vintage_every == 0:
            personal.open_next()
            if fy >= HUF_YEAR:
                huf.open_next()

        # ---- mandated transitions take PRIORITY over the harvest ----------
        turned = gain_turn = forced = 0.0
        for w in wins:
            w["still"] = personal.group_value(navs, w["out"])
            if fy < w["start"] or w["still"] <= EPS or fy > w["end"]:
                continue

            w["armed"] = True
            w_len = w["end"] - w["start"] + 1
            n_out = len(w["into"])
            step = fy - w["start"]
            i_start = (step * n_out) // w_len
            i_end = ((step + 1) * n_out) // w_len
            into_fy = w["into"][i_start:i_end] or w["into"]

            slice_amt = w["still"] / max(1, w["end"] - fy + 1)

            if fy < w["end"]:
                pr, g, _ = personal.draw(navs, fy, amount=slice_amt, gain_cap=band_p, only=set(w["out"]))
            else:
                left = personal.group_value(navs, w["out"])
                pr, g, _ = personal.draw(navs, fy, amount=left, only=set(w["out"]))
                forced += pr

            personal.put_direct(pr, navs, fy, into_fy)
            turned += pr
            gain_turn += g
            w["still"] = personal.group_value(navs, w["out"])

        band_left = max(0.0, band_p - gain_turn)

        # ---- the HUF offer, rationed over the remaining horizon ------------
        offer = 0.0
        if huf.value(hnavs) > EPS:
            probe = copy.deepcopy(huf)
            capacity = probe.draw(hnavs, fy, amount=need, gain_cap=band_h)[0]
            offer = min(RATION_K * huf.value(hnavs) / (horizon + 1 - fy),
                        capacity)

        active_expiring = set()
        for w in wins:
            if fy <= w["end"] and personal.group_value(navs, w["out"]) > EPS:
                active_expiring.update(w["out"])

        eligible = [k for k in base_eligible]
        toi, exempt_c = lad["taxable_other"][i], lad["exempt"][i]

        def _dest(book, nvs, year, default):
            """Steer new money to whichever side is short of its target."""
            if not target_share or not low_funds or not high_funds:
                return default
            tot = book.value(nvs)
            if tot <= EPS:
                return default
            cur = book.group_value(nvs, low_funds) / tot
            return low_funds if cur < target_share(year) else high_funds

        # The 25X mark does three jobs: it is the DEBT FLOOR that surplus must
        # rebuild before anything else, the TRIGGER above which spare capacity
        # exists at all, and the base against which each equity bucket is
        # sized. `need` is the post-tax amount the two purses jointly fund by
        # redemption -- exactly the quantity the floor should be measured in.
        joint_debt = personal.value(navs) + huf.value(hnavs)
        joint_eq = eq_p.value(nav_eq) + eq_h.value(nav_eq)
        eq_excess = max(0.0, joint_debt + joint_eq - DEBT_FLOOR_X * need)
        bucket_target = EQ_BUCKET_SHARE * eq_excess

        eligible = [k for k in base_eligible]
        toi, exempt_c = lad["taxable_other"][i], lad["exempt"][i]

        def _dest(book, nvs, year, default):
            """Steer new money to whichever side is short of its target."""
            if not target_share or not low_funds or not high_funds:
                return default
            tot = book.value(nvs)
            if tot <= EPS:
                return default
            cur = book.group_value(nvs, low_funds) / tot
            return low_funds if cur < target_share(year) else high_funds

        def run_year(huf_offer, eq_use=0.0):
            """Play the year out on copies, for a given HUF and equity draw.

            `eq_use` is cash taken from the equity sleeves. Equity is a LAST
            RESORT, not a habit: it compounds at 9% against debt's 7%, so
            spending it costs corpus. The caller engages it only when the
            debt-only answer would pay tax.

            Returns everything needed to commit the winner, so the chosen split
            is executed exactly once rather than replayed.
            """
            p2, h2 = copy.deepcopy(personal), copy.deepcopy(huf)
            e2p, e2h = copy.deepcopy(eq_p), copy.deepcopy(eq_h)
            eq_cash = eq_gain_p = eq_gain_h = 0.0
            if eq_use > EPS:
                g, gn = e2p.sell(nav_eq, fy, amount=eq_use,
                                 gain_cap=eq_exempt)
                eq_cash += g
                eq_gain_p += gn
                if eq_use - eq_cash > EPS:
                    g, gn = e2h.sell(nav_eq, fy, amount=eq_use - eq_cash,
                                     gain_cap=eq_exempt)
                    eq_cash += g
                    eq_gain_h += gn
            need_left = max(0.0, need - eq_cash)
            want = max(0.0, need_left - huf_offer)
            # Rebalancing. Under-allocation is corrected on BOTH sides of the
            # churn: the low funds are protected from the cash draw, and the
            # harvest is restricted to selling the over-allocated high funds so
            # its proceeds -- which _dest routes to the low side -- represent a
            # genuine transfer rather than a round trip in place.
            _prot = _harv_only = None
            if HOLD_ALLOCATION and target_share and low_funds and high_funds:
                _t = p2.value(navs)
                if _t > EPS and (p2.group_value(navs, low_funds) / _t
                                 < target_share(fy)):
                    _prot = set(low_funds)
                    _harv_only = set(high_funds)
            sold2, cash_p2, surp2, gain_p2, touched2 = p2.fund_year(
                want, band_left, navs, fy, eligible,
                prefer=active_expiring, protect=_prot,
                harvest_only=_harv_only)
            if touched2 and not vintage_every and not no_vintage:
                p2.open_next()
            # Both purses draw FIRST, so the floor test sees the year's
            # actual post-withdrawal position rather than a stale one.
            cash_h2 = gain_h2 = surph2 = 0.0
            huf_live = h2.value(hnavs) > EPS
            if huf_live:
                _s2, cash_h2, surph2, gain_h2, th2 = h2.fund_year(
                    max(0.0, need_left - cash_p2), band_h, hnavs, fy, eligible)
                if th2 and not vintage_every and not no_vintage:
                    h2.open_next()

            # ---- surplus routing: rebuild the debt floor, then equity ------
            # Only the part of the surplus needed to restore the combined debt
            # corpus to DEBT_FLOOR_X years of net need goes back into debt.
            # Whatever remains is spare, and spare capacity is what equity is
            # for.
            short = max(0.0, DEBT_FLOOR_X * need
                        - (p2.value(navs) + h2.value(hnavs)))

            # EVERY rupee of surplus must land somewhere. If equity is off,
            # or the sleeve declines it, the remainder returns to debt --
            # otherwise the harvest becomes a liquidation and the money simply
            # disappears from the plan.
            to_debt_p = min(surp2, short)
            short -= to_debt_p
            to_eq_p = surp2 - to_debt_p
            if EQUITY_ENABLED:
                room = max(0.0, EQUITY_MAX_SHARE
                           * (p2.value(navs) + e2p.value(nav_eq))
                           - e2p.value(nav_eq))
                to_eq_p = min(to_eq_p, room)
            else:
                to_eq_p = 0.0
            if to_eq_p > EPS:
                e2p.invest(to_eq_p, nav_eq, fy, bucket_target)
            # anything equity declines returns to debt -- never discarded
            to_debt_p = surp2 - to_eq_p
            p2.put(to_debt_p, navs, fy, _dest(p2, navs, fy, eligible))

            if huf_live:
                to_debt_h = min(surph2, short)
                short -= to_debt_h
                to_eq_h = surph2 - to_debt_h
                if EQUITY_ENABLED:
                    room_h = max(0.0, EQUITY_MAX_SHARE
                                 * (h2.value(hnavs) + e2h.value(nav_eq))
                                 - e2h.value(nav_eq))
                    to_eq_h = min(to_eq_h, room_h)
                else:
                    to_eq_h = 0.0
                if to_eq_h > EPS:
                    e2h.invest(to_eq_h, nav_eq, fy, bucket_target)
                to_debt_h = surph2 - to_eq_h
                h2.put(to_debt_h, hnavs, fy, eligible)

            gpt = gain_p2 + gain_turn
            txp0 = personal_tax(toi + gpt, exempt_c, band_h)
            txh0 = huf_tax(gain_h2, band_h)
            _dp, egp = pay_tax_bill(p2, txp0, gpt, navs, fy, is_huf=False,
                                    exempt_ceiling=exempt_c, taxable_other=toi,
                                    basic_exemption=band_h)
            _dh, egh = pay_tax_bill(h2, txh0, gain_h2, hnavs, fy, is_huf=True,
                                    nil_band=band_h)
            txp = personal_tax(toi + gpt + egp, exempt_c, band_h)
            txh = huf_tax(gain_h2 + egh, band_h)
            txe = (equity_tax(eq_gain_p, eq_exempt, eq_rate)
                   + equity_tax(eq_gain_h, eq_exempt, eq_rate))
            return dict(tax=txp + txh + txe, p=p2, h=h2, ep=e2p, eh=e2h,
                        sold=sold2, cash_p=cash_p2 + eq_cash, cash_h=cash_h2,
                        surplus=surp2, gain_p=gpt + egp, gain_h=gain_h2 + egh,
                        txp=txp, txh=txh, txe=txe, eq_cash=eq_cash,
                        eq_gain=eq_gain_p + eq_gain_h)

        # The ration-implied split first. It is free and usually optimal.
        best = run_year(offer)
        best_offer = offer

        # If it costs tax, the marginal rupee may be far cheaper in the HUF:
        # the personal side is in the slab regime (26-31%) or the s.87A
        # claw-back (104%), while the HUF's first bracket above its exemption
        # is 5.2%. Search upward, holding back enough HUF value to keep its own
        # free band usable in every remaining year.
        if best["tax"] > EPS and huf.value(hnavs) > EPS:
            reserve = max(0.0, (horizon - fy) * band_h)
            spill_max = min(need, max(0.0, huf.value(hnavs) - reserve))
            if spill_max > offer + EPS:
                for j in range(1, SPILL_GRID + 1):
                    x = offer + (spill_max - offer) * j / SPILL_GRID
                    alt = run_year(x)
                    if alt["tax"] < best["tax"] - 1e-9:
                        best, best_offer = alt, x

        # Equity is engaged ONLY when the debt answer still costs tax. The
        # s.112A exemption is a separate head worth eq_exempt PER PURSE per
        # year, so a modest equity draw can erase a slab bill outright. But
        # every rupee moved out of a 9% sleeve to spare a 7% one costs corpus,
        # so it is never spent for its own sake.
        if best["tax"] > EPS and EQUITY_ENABLED:
            eq_free = (eq_p.capacity(nav_eq, fy, eq_exempt)
                       + eq_h.capacity(nav_eq, fy, eq_exempt))
            if eq_free > EPS:
                for j in range(1, EQ_GRID + 1):
                    use = min(need, eq_free) * j / EQ_GRID
                    # against the BEST HUF split found, not the original ration
                    # -- comparing equity variants to a worse baseline is how
                    # this silently did nothing in the years that paid tax.
                    alt = run_year(best_offer, eq_use=use)
                    if alt["tax"] < best["tax"] - 1e-9:
                        best = alt

        personal, huf = best["p"], best["h"]
        eq_p, eq_h = best["ep"], best["eh"]
        sold_p, cash_p, surplus = best["sold"], best["cash_p"], best["surplus"]
        cash_h = best["cash_h"]
        gain_p_total, gain_h = best["gain_p"], best["gain_h"]
        tax_p_extra_gain = tax_h_extra_gain = 0.0
        tax_p_final, tax_h_final = best["txp"], best["txh"]

        tax_p_tot += tax_p_final
        tax_h_tot += tax_h_final

        # Reported as four separate buckets: debt and equity, personal and HUF.
        p_debt = personal.value(navs)
        huf_debt = huf.value(hnavs)
        p_eq = eq_p.value(nav_eq)
        huf_eq = eq_h.value(nav_eq)
        val_eq = p_eq + huf_eq
        # F_EQ is ONE folio per purse holding N_EQ_FUNDS buckets, so the folio
        # count is 0 or 1; `buckets` is the informative number alongside it.
        p_eq_folios = 1 if p_eq > EPS else 0
        huf_eq_folios = 1 if huf_eq > EPS else 0
        tax_eq_tot += best.get("txe", 0.0)

        rows.append(dict(
            fy=fy,
            requirement=lad["requirement"][i],
            other_income=lad["other_income"][i],
            net_need=need,
            personal_band=band_p,
            huf_nil_band=band_h,
            transition=turned,
            transition_forced=forced,
            personal_cash=cash_p,
            huf_cash=cash_h,
            surplus=surplus,
            gain_personal=gain_p_total + tax_p_extra_gain,
            gain_huf=gain_h + tax_h_extra_gain,
            p_debt=p_debt,
            p_eq=p_eq,
            huf_debt=huf_debt,
            huf_eq=huf_eq,
            p_debt_folios=len(personal.folios),
            p_eq_folios=p_eq_folios,
            huf_debt_folios=len(huf.folios),
            huf_eq_folios=huf_eq_folios,
            p_eq_buckets=eq_p.buckets_used(),
            huf_eq_buckets=eq_h.buckets_used(),
            folios_open=len(personal.folios),   # retained: CSV column expansion
            value_equity=val_eq,
            equity_cash=best.get("eq_cash", 0.0),
            equity_gain=best.get("eq_gain", 0.0),
            tax_equity=best.get("txe", 0.0),
            folio_values=[f.value(navs) for f in personal.folios],
            tax_personal=tax_p_final,
            tax_huf=tax_h_final,
            tax_total=tax_p_final + tax_h_final,
            tax_cumulative=tax_p_tot + tax_h_tot,
        ))

    if verbose:
        print(f"{'FY':>3}{'band':>7}{'need':>7}{'cash_p':>8}{'cash_h':>8}"
              f"{'surp':>8}{'trans':>8}{'gain':>7}{'fol':>5}"
              f"{'p_debt':>10}{'p_eq':>9}{'h_debt':>9}{'h_eq':>8}"
              f"{'eq$':>7}{'tax':>7}{'cum':>8}")
        print("-" * 118)
        for r in rows:
            print(f"{r['fy']:>3}{r['personal_band']:>7.2f}{r['net_need']:>7.2f}"
                  f"{r['personal_cash']:>8.2f}{r['huf_cash']:>8.2f}"
                  f"{r['surplus']:>8.2f}{r['transition']:>8.2f}"
                  f"{r['gain_personal']:>7.2f}{r['p_debt_folios']:>5}"
                  f"{r['p_debt']:>10.1f}{r['p_eq']:>9.1f}"
                  f"{r['huf_debt']:>9.1f}{r['huf_eq']:>8.1f}"
                  f"{r['equity_cash']:>7.2f}"
                  f"{r['tax_total']:>7.2f}{r['tax_cumulative']:>8.2f}")

    return dict(tax=tax_p_tot + tax_h_tot + tax_eq_tot,
                tax_personal=tax_p_tot, tax_huf=tax_h_tot,
                tax_equity=tax_eq_tot,
                p_debt=rows[-1]["p_debt"], p_eq=rows[-1]["p_eq"],
                huf_debt=rows[-1]["huf_debt"], huf_eq=rows[-1]["huf_eq"],
                equity=rows[-1]["value_equity"],
                corpus=(rows[-1]["p_debt"] + rows[-1]["p_eq"]
                        + rows[-1]["huf_debt"] + rows[-1]["huf_eq"]),
                p_debt_folios=rows[-1]["p_debt_folios"],
                p_eq_folios=rows[-1]["p_eq_folios"],
                huf_debt_folios=rows[-1]["huf_debt_folios"],
                huf_eq_folios=rows[-1]["huf_eq_folios"],
                folios=rows[-1]["p_debt_folios"], rows=rows)


def write_csv(result, path):
    rows = []
    maxf = max(r["folios_open"] for r in result["rows"])
    for r in result["rows"]:
        d = {k: (round(v, 4) if isinstance(v, float) else v)
             for k, v in r.items() if k != "folio_values"}
        vals = r["folio_values"] + [0.0] * maxf
        for j in range(maxf):
            d[f"value_F{j + 1}"] = round(vals[j], 4)
        rows.append(d)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="1chunk", choices=sorted(SCENARIOS))
    ap.add_argument("--horizon", type=int, default=HORIZON)
    ap.add_argument("--cap", type=int, default=FOLIO_CAP)
    ap.add_argument("--vintage", type=int, default=VINTAGE_EVERY,
                    help="open a fresh receiver every N years; 0 = on drain")
    ap.add_argument("--no_vintage", "--no-vintage", action="store_true",
                    help="never open new folios (cap = 1)")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--sweep", action="store_true",
                    help="compare scenarios, horizons and vintage rules")
    a = ap.parse_args()

    if a.sweep:
        print(f"{'scenario':>10}{'horizon':>8}{'vintage':>11}"
              f"{'tax':>9}{'corpus':>10}"
              f"{'p_debt':>10}{'p_eq':>9}{'h_debt':>9}{'h_eq':>8}"
              f"{'folios p/e/h/e':>16}")
        print("-" * 100)
        for sc in ("1chunk", "2chunk", "3chunk"):
            for hz in (30, 35):
                for vt_label, vt, nv in (("on drain", 0, False),
                                         ("no_vintage", 0, True),
                                         ("5yr", 5, False),
                                         ("3yr", 3, False)):
                    r = simulate(sc, hz, a.cap, vintage_every=vt, no_vintage=nv)
                    fol = (f"{r['p_debt_folios']}/{r['p_eq_folios']}"
                           f"/{r['huf_debt_folios']}/{r['huf_eq_folios']}")
                    print(f"{sc:>10}{hz:>8}{vt_label:>11}"
                          f"{r['tax']:>9.3f}{r['corpus']:>10.1f}"
                          f"{r['p_debt']:>10.1f}{r['p_eq']:>9.1f}"
                          f"{r['huf_debt']:>9.1f}{r['huf_eq']:>8.1f}"
                          f"{fol:>16}")
            print()
        return 0

    r = simulate(a.scenario, a.horizon, a.cap, vintage_every=a.vintage,
                 no_vintage=a.no_vintage, verbose=not a.quiet)
    print(f"\nscenario {a.scenario}, {a.horizon} years, cap {a.cap}, "
          f"vintage {'no_vintage' if a.no_vintage else ('on drain' if not a.vintage else str(a.vintage) + 'yr')}")
    print(f"lifetime tax {r['tax']:.3f}  "
          f"(personal {r['tax_personal']:.3f} + HUF {r['tax_huf']:.3f})")
    print(f"end corpus   {r['corpus']:.1f}")
    print(f"  p_debt   {r['p_debt']:>9.1f}   folios {r['p_debt_folios']}")
    print(f"  p_eq     {r['p_eq']:>9.1f}   folios {r['p_eq_folios']}"
          f"   buckets {r['rows'][-1]['p_eq_buckets']} of {N_EQ_FUNDS}")
    print(f"  huf_debt {r['huf_debt']:>9.1f}   folios {r['huf_debt_folios']}")
    print(f"  huf_eq   {r['huf_eq']:>9.1f}   folios {r['huf_eq_folios']}"
          f"   buckets {r['rows'][-1]['huf_eq_buckets']} of {N_EQ_FUNDS}")
    if a.csv:
        write_csv(r, a.csv)
        print(f"wrote {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())