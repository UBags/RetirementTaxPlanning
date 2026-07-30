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
      1chunk       35        5yr     1.166    1908.0
      2chunk       30        5yr     0.000    1520.1
      2chunk       35        5yr     1.274    1934.2
      3chunk       30        5yr     1.448    1543.7    <- see KNOWN BROKEN
      3chunk       35        5yr     2.214    1991.4    <- see KNOWN BROKEN

Personal tax is ZERO in every scenario. The entire residual is HUF tax, and it
is deliberate: in the last three years the HUF pays ~5.2% on a few lakh of
excess gain to spare the personal purse a 26-31% marginal rate.

THE TWO CHANGES THAT MATTERED, AND WHY

1. ANCHORING THE SLAB LADDER. `slab_tax` used to carry absolute bracket
   boundaries starting at 4.0 while NILBAND was projected forward to 10.0. So
   the model charged tax from 4.0 no matter what exemption the year claimed,
   the projection was cosmetic, and `huf_tax` had a discontinuity -- 0.000 at
   10.00 and 0.417 at 10.01. Expressing the ladder as a width plus a rate list,
   anchored on whatever exemption applies, made it continuous and cut the FY33
   personal bill from 3.511 to 1.924 on its own.

   This was a MODELLING ERROR, not a strategy. Much of what the folio and
   vintage machinery appeared to buy in earlier versions was compensation for
   tax that would not have existed. After the fix the vintage lever largely
   collapsed: 1chunk/30 now spans 0.000 to 0.723 across every folio
   configuration, where it previously spanned 1.557 to 19.827.

2. THE HUF SPILLOVER. `RATION_K` allocated between the purses by a fixed
   formula, blind to tax. Near the s.87A ceiling the personal marginal rate is
   26-31% (and 104% inside the rebate claw-back, between the ceiling and about
   2.9 above it, where marginal relief returns a rupee of tax per rupee of
   income). The HUF's first bracket above its own exemption is 5.2%. `run_year`
   now plays the year out on copies for a range of HUF contributions and keeps
   the cheapest, holding back (horizon - fy) * band_h so the HUF retains enough
   to use its own free band in every remaining year. Worth ~14 of tax at 35
   years.

NOISE FLOOR -- READ THIS BEFORE COMPARING ROWS

Differences below about 0.3 are implementation noise, not signal.

Measured: permuting the fund rotation order -- a choice with no economic
content -- moves 1chunk/35/5yr across 1.153 to 1.274 with corpus varying by
0.1. An earlier version reported 0.887 for that cell; six random orderings all
failed to reproduce it, so that figure was a lucky sequence rather than a
better algorithm.

So 1.448 versus 1.421 means nothing. 0.000 versus 2.7 is real. Several
comparisons that look meaningful in a --sweep table sit below the floor.

KNOWN BROKEN

* 3chunk does not hold its allocation. The transitions are one-way EMPTYING
  mandates ("f15-f21 must be empty by FY10"), and nothing keeps money in the
  destination funds afterwards: it arrives with the freshest basis and the
  cheapest-first withdrawal takes it straight back out. The low-rate share
  reaches 26.7% at FY10 and decays to 15.4% by FY30 against a 67% target. So
  3chunk is closer to a one-chunk plan wearing a three-chunk label, and its tax
  and corpus are NOT comparable to 1chunk and 2chunk. Fixing this needs the
  mandate re-expressed as a share to MAINTAIN, which is a design change rather
  than a parameter. HOLD_ALLOCATION is a partial attempt; see its docstring for
  what it does and does not buy.

* Folio count is non-monotonic. 3chunk/35 pays 2.981 with one folio and 3.981
  with four; 3chunk/30 pays 1.761 with one and 1.925 with three. A folio can
  always be ignored, so opening one cannot cost tax -- this is a bug signal,
  not a modelling choice, and it is the only remaining discrepancy comfortably
  above the noise floor. It has survived every other fix in this model's
  history, which makes it the most likely place a genuine defect still hides.

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
HOLD_ALLOCATION = False
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

    def sell(self, nav, fy, amount=None, gain_cap=None):
        """FIFO. Returns (gross proceeds, gain realised, exit load)."""
        gross = gain = load = 0.0
        for lot in self.lots:
            if lot[0] <= EPS:
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
                  protect=None):
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

        for n in range(1, len(lv) + 1):
            subset = lv[:n]
            probe = copy.deepcopy(self)
            got, gmin, _ = probe.draw(navs, fy, folio_idxs=subset, amount=need,
                                      prefer=prefer, high_gain_first=False, protect=protect)
            if got < need - 1e-6 or gmin > band + 1e-9:
                continue                        # this set cannot do it cleanly

            need_left, band_left, gain_tot = need, band, 0.0
            for j, k in enumerate(subset):
                rest = subset[j + 1:]
                lo, hi, best = 0.0, need_left, 0.0
                for _ in range(35):             # most from the earliest folio
                    x = (lo + hi) / 2
                    trial = copy.deepcopy(self)
                    a, ga, _ = trial.draw(navs, fy, folio_idxs=[k], amount=x,
                                          prefer=prefer, high_gain_first=True, protect=protect)
                    b, gb, _ = trial.draw(navs, fy, folio_idxs=rest,
                                          amount=max(0.0, need_left - a),
                                          prefer=prefer, high_gain_first=False, protect=protect)
                    if (a + b >= need_left - 1e-6
                            and ga + gb <= band_left + 1e-9):
                        best, lo = x, x
                    else:
                        hi = x
                a, ga, per = self.draw(navs, fy, folio_idxs=[k], amount=best,
                                       prefer=prefer, high_gain_first=True, protect=protect)
                for fi, amt in per.items():
                    sold[fi] = sold.get(fi, 0.0) + amt
                need_left -= a
                band_left -= ga
                gain_tot += ga
                if need_left <= 1e-6:
                    break
            if need_left > 1e-6:
                b, gb, per = self.draw(navs, fy, folio_idxs=subset,
                                       amount=need_left, prefer=prefer,
                                       high_gain_first=False)
                gain_tot += gb
                for fi, amt in per.items():
                    sold[fi] = sold.get(fi, 0.0) + amt

            surplus = 0.0
            if band - gain_tot > EPS:           # harvest the rest of the band
                pr, g2, per = self.draw(navs, fy, folio_idxs=lv,
                                        gain_cap=band - gain_tot,
                                        prefer=prefer, high_gain_first=True,
                                        min_gain_frac=0.01, protect=protect)
                surplus = pr
                gain_tot += g2
                for fi, amt in per.items():
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
    navs = [1.0] * nf
    nav_h = 1.0

    start_funds = [k for k in range(nf)
                   if spec["rate"](k, 1) > 0 and k < 21]
    personal.folios[0].seed(PERSONAL_CORPUS, navs, 0, start_funds)

    wins = [dict(start=s, end=e, out=out, into=into, armed=False, still=0.0)
            for s, e, out, into in spec["transitions"]]

    tax_p_tot = tax_h_tot = 0.0
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

        def run_year(huf_offer):
            """Play the year out on copies for a given HUF contribution.

            Returns everything needed to commit the winner, so the chosen split
            is executed exactly once rather than replayed.
            """
            p2, h2 = copy.deepcopy(personal), copy.deepcopy(huf)
            want = max(0.0, need - huf_offer)
            _prot = None
            if HOLD_ALLOCATION and target_share and low_funds:
                _t = p2.value(navs)
                if _t > EPS and (p2.group_value(navs, low_funds) / _t
                                 < target_share(fy)):
                    _prot = set(low_funds)
            sold2, cash_p2, surp2, gain_p2, touched2 = p2.fund_year(
                want, band_left, navs, fy, eligible,
                prefer=active_expiring, protect=_prot)
            if touched2 and not vintage_every and not no_vintage:
                p2.open_next()
            p2.put(surp2, navs, fy, _dest(p2, navs, fy, eligible))

            cash_h2 = gain_h2 = 0.0
            if h2.value(hnavs) > EPS:
                _s2, cash_h2, surph2, gain_h2, th2 = h2.fund_year(
                    max(0.0, need - cash_p2), band_h, hnavs, fy, eligible)
                if th2 and not vintage_every and not no_vintage:
                    h2.open_next()
                h2.put(surph2, hnavs, fy, eligible)

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
            return dict(tax=txp + txh, p=p2, h=h2, sold=sold2, cash_p=cash_p2,
                        cash_h=cash_h2, surplus=surp2, gain_p=gpt + egp,
                        gain_h=gain_h2 + egh, txp=txp, txh=txh)

        # The ration-implied split first. It is free and usually optimal.
        best = run_year(offer)

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
                        best = alt

        personal, huf = best["p"], best["h"]
        sold_p, cash_p, surplus = best["sold"], best["cash_p"], best["surplus"]
        cash_h = best["cash_h"]
        gain_p_total, gain_h = best["gain_p"], best["gain_h"]
        tax_p_extra_gain = tax_h_extra_gain = 0.0
        tax_p_final, tax_h_final = best["txp"], best["txh"]

        tax_p_tot += tax_p_final
        tax_h_tot += tax_h_final

        val_p = personal.value(navs)
        val_h = huf.value(hnavs)

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
            folios_open=len(personal.folios),
            value_personal=val_p,
            value_huf=val_h,
            folio_values=[f.value(navs) for f in personal.folios],
            tax_personal=tax_p_final,
            tax_huf=tax_h_final,
            tax_total=tax_p_final + tax_h_final,
            tax_cumulative=tax_p_tot + tax_h_tot,
        ))

    if verbose:
        print(f"{'FY':>3}{'band':>7}{'need':>7}{'cash_p':>8}{'cash_h':>8}"
              f"{'surp':>8}{'trans':>8}{'gain':>7}{'fol':>5}"
              f"{'personal':>10}{'HUF':>9}{'tax':>7}{'cum':>8}")
        print("-" * 95)
        for r in rows:
            print(f"{r['fy']:>3}{r['personal_band']:>7.2f}{r['net_need']:>7.2f}"
                  f"{r['personal_cash']:>8.2f}{r['huf_cash']:>8.2f}"
                  f"{r['surplus']:>8.2f}{r['transition']:>8.2f}"
                  f"{r['gain_personal']:>7.2f}{r['folios_open']:>5}"
                  f"{r['value_personal']:>10.1f}{r['value_huf']:>9.1f}"
                  f"{r['tax_total']:>7.2f}{r['tax_cumulative']:>8.2f}")

    return dict(tax=tax_p_tot + tax_h_tot, tax_personal=tax_p_tot,
                tax_huf=tax_h_tot,
                corpus=rows[-1]["value_personal"] + rows[-1]["value_huf"],
                folios=rows[-1]["folios_open"], rows=rows)


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
        print(f"{'scenario':>10}{'horizon':>9}{'vintage':>11}{'cap':>5}"
              f"{'tax':>10}{'corpus':>10}{'folios':>8}")
        print("-" * 63)
        for sc in ("1chunk", "2chunk", "3chunk"):
            for hz in (30, 35):
                for vt_label, vt, nv in (("on drain", 0, False),
                                         ("no_vintage", 0, True),
                                         ("5yr", 5, False),
                                         ("3yr", 3, False)):
                    r = simulate(sc, hz, a.cap, vintage_every=vt, no_vintage=nv)
                    print(f"{sc:>10}{hz:>9}{vt_label:>11}{a.cap:>5}"
                          f"{r['tax']:>10.3f}{r['corpus']:>10.1f}"
                          f"{r['folios']:>8}")
            print()
        return 0

    r = simulate(a.scenario, a.horizon, a.cap, vintage_every=a.vintage,
                 no_vintage=a.no_vintage, verbose=not a.quiet)
    print(f"\nscenario {a.scenario}, {a.horizon} years, cap {a.cap}, "
          f"vintage {'no_vintage' if a.no_vintage else ('on drain' if not a.vintage else str(a.vintage) + 'yr')}")
    print(f"lifetime tax {r['tax']:.3f}  "
          f"(personal {r['tax_personal']:.3f} + HUF {r['tax_huf']:.3f})")
    print(f"end corpus   {r['corpus']:.1f}   folios used {r['folios']}")
    if a.csv:
        write_csv(r, a.csv)
        print(f"wrote {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())