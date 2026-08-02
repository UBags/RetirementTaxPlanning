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
    python3 swp_dp_model.py --interest_rate 0.065
    python3 swp_dp_model.py --max_folios 12
    python3 swp_dp_model.py --salary_income 6.0 --rental_income 4.0 \
                            --taxable_other_income 2.0 --taxfree_other_income 3.8

PARAMETERS

--interest_rate     ONE, TWO or THREE rates, slash separated. The rate belongs
                    to the FUND, fixed for its life, set by the chunk the fund
                    was first chosen for. How the supplied rates map:

                      0.045              1chunk  0.0450
                                         2chunk  0.0475 0.0425
                                         3chunk  0.0475 0.0450 0.0425
                      0.055/0.045        1chunk  0.0550
                                         2chunk  0.0550 0.0450
                                         3chunk  0.0550 0.0500 0.0450
                      0.065/0.055/0.045  1chunk  0.0650
                                         2chunk  0.0650 0.0550
                                         3chunk  0.0650 0.0550 0.0450

                    One rate is a MIDPOINT and the chunks spread around it by
                    CHUNK_SPREAD. Two are the first and last chunk, with a
                    middle chunk taking their mean. Three are literal. A
                    scenario always uses the first n_chunks of what it
                    resolves, so one flag gives a coherent set for all three.

                    The PORTFOLIO rate is an output, not an input: it falls as
                    the transitions shift the mix, and funds that never
                    transition (f1-f7 in 3chunk) keep their chunk-1 rate for
                    the whole horizon.

--post_tax_requirement
                    The requirement schedule, as amount/from_FY pairs, comma
                    separated:

                        35/1,40/6,45/11,50/16,52.5/21,55/25,57.5/31

                    Each pair holds until the next one starts; the last runs to
                    the horizon. Blocks need NOT be five years -- the example
                    above runs FY21-24 at 52.5 and FY25-30 at 55. Five-year
                    blocks were a baked-in assumption and this replaces them.

                    NOTE THE SEPARATORS, which differ from the other list-style
                    flags on purpose: SLASH splits an amount from its year,
                    COMMA splits one pair from the next. The first pair must
                    start at FY1 and the years must strictly increase.

--personal_corpus   Inflow amounts in Rs LAKH, slash separated, paired
--personal_year     positionally with the years:
--huf_corpus
--huf_year              --personal_corpus 500/20/30/40 --personal_year 1/6/9/14
                        --huf_corpus 190/60/40 --huf_year 5/8/12

                    The FIRST personal inflow is the opening corpus and must be
                    FY1; it seeds before FY1's growth, so it compounds through
                    year one. Later inflows arrive DURING their year at that
                    year's NAV -- they are contributions made partway through,
                    not opening balances. Each is spread across the funds that
                    purse already holds, in the same proportion.

                    Every money figure here is LAKH, so 350 is 3.5 crore. A
                    total under 25 is rejected as a probable crore/lakh
                    confusion rather than silently modelling an unfundable plan.

                    --starting_corpus is still accepted as an alias, and a
                    single value with no year is taken as FY1.

--huf_year          The FYs they land, paired positionally:

                        --huf_corpus 190/60/40 --huf_year 5/8/12

                    is 190 in FY5, 60 in FY8, 40 in FY12. The counts must
                    match; repeated years are rejected.

THE HUF IS NOT A SEPARATE ASSET CLASS. It holds the SAME funds at the SAME
NAVs as the personal purse, earns the same per-fund rates, and is carried
through the same chunk transitions. Each inflow is spread across the funds the
personal purse holds IN THAT YEAR, in the same proportion, so money arriving
mid-transition lands in the mix that actually exists rather than an arbitrary
one. Its transition gains are charged against the HUF's OWN nil band -- two
assessees, two allowances.

It was previously a flat-rate pool pinned at 7.0% that ignored --interest_rate
and never transitioned. At --interest_rate 0.045 that was worth 313 of corpus
and the difference between the second purse surviving and being exhausted, so
every low-rate result from before this change was optimistic.

--ration_k          HUF draw pacing: offer = k x huf_value / years_remaining,
                    capped at the HUF's own tax-free capacity. Around 1.2 the
                    ration binds early and the capacity binds late, so k only
                    controls roughly the first half of the horizon. Too low
                    wastes a second tax-free allowance; too high triggers tax
                    in the purse meant to avoid it.

--equity_max_share  Equity deployment cap as a share of the purse. This is a
                    cap on INFLOW, not a rebalancing target: the sleeve is
                    never trimmed back, so it drifts above the cap as equity
                    outgrows debt. Forcing it back costs tax and corpus both.

--income_step_absolute
                    ABSOLUTE lakh added to total other income each year. 0.10
                    is Rs 10,000/yr, NOT 10 percent, and it is linear rather
                    than compound: 15.30 becomes 18.70 over 35 years, not 41.1
                    as a 3% compound rate would give. Shared pro-rata across
                    the four heads so the taxable fraction stays stable.
                    --income_step is still accepted as an alias.

--equity_cagr       Equity sleeve CAGR as a decimal, e.g. 0.12. DETERMINISTIC:
                    EQUITY_SIGMA exists but is unused, because a fixed model
                    cannot use a dispersion for anything but false precision.
                    Measured on 2chunk/35yr, the sleeve is a corpus lever and
                    barely a tax one -- above 9% the tax stops moving at all
                    while the corpus roughly doubles:

                        7%   tax 1.130  corpus 1995.3  equity 16.1% of it
                        9%   tax 0.965  corpus 2238.5  equity 24.7%
                       12%   tax 0.965  corpus 2894.0  equity 41.1%
                       15%   tax 0.965  corpus 4196.7  equity 59.0%

                    s.112A caps the tax-free drawdown at the annual exemption
                    regardless of sleeve size, so a bigger sleeve adds nothing
                    to tax capacity. Raising this assumption inflates the
                    corpus and nothing else -- treat a high value as a
                    forecast, not a finding.

--max_folios n      maximum debt cohorts per purse (was --cap, still accepted).
                    Binds only at --vintage 3 or tighter, and releasing it
                    changes nothing measurable; a guard, not a lever.

Four income heads, each with its own statutory treatment:

--taxable_other_income   fully taxable (interest and the like)
--taxfree_other_income   PPF, tax-free bonds, return of capital
--salary_income          taxable less the s.16(ia) standard deduction
--rental_income          taxable less the s.24(a) 30% deduction

They sum to gross income; OTHER_INCOME_STEP grows the total pro-rata so the
taxable fraction is stable. Defaults reproduce the previous 15.80 gross with
5.95 taxable. The TAXABLE portion is the lever, not the gross -- it is
subtracted from the s.87A ceiling to give the year's tax-free band, so moving
4 of income from tax-free to salary costs about 3 of band every year.

BOUNDARY_GRACE  Every transition deadline sits this many years beyond its chunk
                boundary. MEASURED at 35 years: no setting from 0 to 6 produces
                any forced liquidation, so the grace is not buying safety. What
                it buys is corpus -- money stays in the higher-rate chunk-1
                funds longer -- at the cost of realising gain in later, more
                contested years:

                    3chunk 35yr   grace 0  tax 3.046  corpus 2133.2
                                  grace 3  tax 3.848  corpus 2171.6
                                  grace 6  tax 4.377  corpus 2198.5

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

WATER-FILLING WHEN THE FREE BANDS RUN OUT

Once every purse's tax-free capacity is spent, the plain path lets each purse
raise its share and pays whatever tax falls out. That is arbitrary: it can take
a rupee from personal debt at a 31% slab while the HUF's 5% bracket and both
s.112A exemptions sit unused.

`run_waterfill` draws every source's FREE capacity first, then fills the
remaining shortfall one slice at a time from whichever source is cheapest AT
THAT MOMENT, across four taps -- personal debt, HUF debt, personal equity, HUF
equity. Ties go to the personal purse.

TWO THINGS ABOUT IT ARE EASY TO GET WRONG.

The ordering is by tax cost per rupee of CASH, not per rupee of GAIN. Cash is
what the year needs, and gain per rupee of cash varies enormously: an old debt
lot is ~90% gain, a fresh equity bucket ~20%. So equity at 13% on 20% gain
costs 2.6% per rupee raised while an HUF slab at 5.2% on 85% gain costs 4.4%.
Sorting by tax RATE alone inverts those two.

Marginal rates are read from the tax functions NUMERICALLY, not from a
hardcoded ladder, so the s.87A claw-back's 104% band is seen and avoided
automatically rather than needing a special case.

AND IT IS CHOSEN ON TERMINAL VALUE, NOT ON TAX. Minimising tax alone will spend
equity compounding at EQUITY_RETURN to avoid a smaller bill on debt compounding
at the chunk rate. Measured on 2chunk/35yr at defaults, the tax-minimising path
saved 1.166 of tax and cost 40.6 of corpus. Each purse is projected forward at
its own rate to the end of the plan and the larger total wins, so that trade is
correctly refused.

TAX_EPS guards the trigger. EPS is 1e-12, and a tax bill of nine nanopaise --
which a 35-year run really does produce -- once triggered a restructure that
cost 25 of corpus to save nothing.

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
# The personal purse, like the HUF, takes one or more external inflows:
#   --personal_corpus 500/20/30/40 --personal_year 1/6/9/14
# The FIRST is the OPENING corpus and is seeded before FY1's growth, so it
# compounds through year one. Later inflows arrive DURING their year, at that
# year's NAV, exactly as HUF inflows do -- they are contributions made partway
# through, not opening balances.
PERSONAL_INFLOWS = ((350.0, 1),)
# The HUF receives one or more external inflows. Amounts and years are given
# separately -- --huf_corpus 190/60/40 with --huf_year 5/8/12 -- and are zipped
# into (amount, FY) pairs. Each inflow is spread across the funds the PERSONAL
# purse holds in that year, in the same proportion, so the HUF starts life with
# the same portfolio shape rather than an arbitrary one.
HUF_INFLOWS = ((190.0, 5),)

# ---- the horizon -----------------------------------------------------------
HORIZON = 30                # 30 or 35; ladders below extend to 35

# ---- structure -------------------------------------------------------------
N_FUNDS = 21                # funds per folio, each its own FIFO queue
TRIPLE = 3                  # a year's surplus is split across this many funds
MAX_FOLIOS = 8              # maximum debt cohorts per purse
VINTAGE_EVERY = 5           # open a fresh receiver every N years.
                            # 0 = the old drain-triggered rule (much worse)

# ---- the requirement ladder (Rs lakh/yr, one entry per 5-year block) --------
# (post-tax requirement, first FY it applies). Each pair holds until the next
# one starts; the last extends to the horizon. Blocks need NOT be five years --
# that was a baked-in assumption and this replaces it.
REQUIREMENT_SCHEDULE = ((35.0, 1), (37.5, 6), (42.5, 11), (45.0, 16),
                        (47.5, 21), (50.0, 26), (52.5, 31))

# ---- the debt return ------------------------------------------------------
# The rate is a property of the FUND, fixed for its life, set by the chunk the
# fund was first chosen for. The portfolio rate then falls as the transitions
# shift the mix -- the blend is an OUTPUT, not an input.
INTEREST_RATES = (0.070,)   # one, two or three rates. What each chunk's funds
CHUNK_SPREAD = 0.0025       # earn is resolved by chunk_rates() below.

# ---- income, by head ------------------------------------------------------
# Split so each head carries its own statutory treatment. The defaults sum to
# the previous total of 15.80 with a taxable portion of 5.95, so an
# out-of-the-box run is unchanged.
TAXABLE_OTHER_INCOME = 4.85     # interest and the like -- fully taxable
TAXFREE_OTHER_INCOME = 10.45    # PPF, tax-free bonds, return of capital
SALARY_INCOME = 0.0             # taxable less the s.16(ia) standard deduction
RENTAL_INCOME = 0.0             # taxable less the s.24(a) 30% deduction
SALARY_STD_DEDUCTION = 0.75     # s.16(ia)
RENTAL_DEDUCTION = 0.30         # s.24(a)
OTHER_INCOME_STEP = 0.10        # ABSOLUTE lakh added to total other income each
                                # year -- 0.10 is Rs 10,000/yr, not 10%. Linear,
                                # not compound. Shared pro-rata across the four
                                # heads so the taxable fraction stays stable.

# ---- transition timing ----------------------------------------------------
BOUNDARY_GRACE = 3          # a transition's deadline sits this many years
                            # BEYOND its chunk boundary, giving the pacing more
                            # tax-free band to work with before it is forced

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
EQUITY_RETURN = 0.090       # deterministic CAGR, --equity_cagr
EQUITY_SIGMA = 0.125        # NOT USED -- deterministic model. See docstring.
N_EQ_FUNDS = 20             # eq_f1 .. eq_f20, filled SEQUENTIALLY not equally
EQ_BUCKET_SHARE = 0.05      # each bucket is filled to this share of the excess
                            # over the floor. 20 x 5% = the whole excess.
# ---- turnover -------------------------------------------------------------
# Fraction of the INITIAL fund count moved at each chunk boundary. One entry per
# boundary: 3chunk uses both, 2chunk uses the first. Turnover was previously
# implicit in how many funds each transition emptied -- 7 of 21 at each of
# 3chunk's boundaries, 10 of 21 at 2chunk's. It now drives the fund groups.
N_BASE_FUNDS = 21
TURNOVER = (1.0 / 3.0, 1.0 / 3.0)

MIN_HORIZON = 20            # below this the tier arithmetic stops meaning
                            # anything and the plan is not a retirement plan

# The floor steps DOWN over the horizon in three tiers. The liability shrinks
# as the plan runs, so holding the opening multiple to the end traps surplus
# against a debt that no longer exists. Tier boundaries scale with the horizon:
# the first two are min(10, N//3) years each, the third is the remainder.
#     20yr -> 6 / 6 / 8      25yr -> 8 / 8 / 9
#     30yr -> 10 / 10 / 10   35yr -> 10 / 10 / 15
DEBT_FLOOR_MULTIPLES = (25.0, 22.0, 18.0)

DEBT_FLOOR_X = 25.0         # retained for reference. The combined DEBT corpus
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


def chunk_rates(n_chunks):
    """The rate each chunk's funds earn, given 1, 2 or 3 supplied rates.

    ONE rate is a midpoint and the chunks spread around it by CHUNK_SPREAD.
    TWO rates are the first and last chunk; a middle chunk takes their mean.
    THREE rates are taken literally.

    A scenario always uses the FIRST n_chunks of what it resolves, so the same
    --interest_rate produces a coherent set for every scenario:

        --interest_rate 0.045              1chunk  0.0450
                                           2chunk  0.0475 0.0425
                                           3chunk  0.0475 0.0450 0.0425
        --interest_rate 0.055/0.045        1chunk  0.0550
                                           2chunk  0.0550 0.0450
                                           3chunk  0.0550 0.0500 0.0450
        --interest_rate 0.065/0.055/0.045  1chunk  0.0650
                                           2chunk  0.0650 0.0550
                                           3chunk  0.0650 0.0550 0.0450
    """
    r = INTEREST_RATES
    if len(r) == 1:
        i = r[0]
        if n_chunks == 1:
            return [i]
        if n_chunks == 2:
            return [i + CHUNK_SPREAD, i - CHUNK_SPREAD]
        return [i + CHUNK_SPREAD, i, i - CHUNK_SPREAD]
    if n_chunks == 1:
        return [r[0]]
    if n_chunks == 2:
        return [r[0], r[1]]
    if len(r) == 2:
        return [r[0], (r[0] + r[1]) / 2.0, r[1]]
    return [r[0], r[1], r[2]]


def _turnover_counts():
    """Funds moved at each boundary, as counts. At least one, at most what is
    left, so a silly fraction degrades rather than producing empty groups."""
    n1 = max(1, min(N_BASE_FUNDS - 1, round(TURNOVER[0] * N_BASE_FUNDS)))
    n2 = max(1, min(N_BASE_FUNDS - n1 - 1,
                    round(TURNOVER[1] * N_BASE_FUNDS)))
    return n1, n2


def get_scenario(name):
    """Scenario spec built from the CURRENT constants.

    Rebuilt on each call rather than held in a module-level dict, so that
    --turnover and --interest_rate actually take effect. The fund groups are a
    consequence of turnover: boundary 1 empties the last n1 of the base funds
    into n1 brand-new ones, boundary 2 empties the last n2 of what remains.
    """
    n1, n2 = _turnover_counts()
    base_last = N_BASE_FUNDS - 1

    if name == "1chunk":
        rt = chunk_rates(1)
        return dict(n_funds=N_BASE_FUNDS, rate=lambda k: rt[0],
                    transitions=[])

    if name == "2chunk":
        out1 = list(range(N_BASE_FUNDS - n1, N_BASE_FUNDS))
        into1 = list(range(N_BASE_FUNDS, N_BASE_FUNDS + n1))
        return dict(
            n_funds=N_BASE_FUNDS + n1,
            rate=lambda k, _r=chunk_rates(2): (_r[0] if k < N_BASE_FUNDS
                                               else _r[1]),
            transitions=[(1, 15 + BOUNDARY_GRACE, out1, into1)],
            low_fund_idx=lambda k: k >= N_BASE_FUNDS,
            target_low_share=lambda fy: (0.0 if fy <= 15
                                         else n1 / float(N_BASE_FUNDS)),
        )

    if name == "3chunk":
        out1 = list(range(N_BASE_FUNDS - n1, N_BASE_FUNDS))
        into1 = list(range(N_BASE_FUNDS, N_BASE_FUNDS + n1))
        out2 = list(range(N_BASE_FUNDS - n1 - n2, N_BASE_FUNDS - n1))
        into2 = list(range(N_BASE_FUNDS + n1, N_BASE_FUNDS + n1 + n2))
        cut1, cut2 = N_BASE_FUNDS, N_BASE_FUNDS + n1
        return dict(
            n_funds=N_BASE_FUNDS + n1 + n2,
            rate=lambda k, _r=chunk_rates(3): (_r[0] if k < cut1
                                               else _r[1] if k < cut2
                                               else _r[2]),
            transitions=[(1, 10 + BOUNDARY_GRACE, out1, into1),
                         (11, 20 + BOUNDARY_GRACE, out2, into2)],
            low_fund_idx=lambda k: k >= N_BASE_FUNDS,
            target_low_share=lambda fy: (
                0.0 if fy <= 10
                else n1 / float(N_BASE_FUNDS) if fy <= 20
                else (n1 + n2) / float(N_BASE_FUNDS)),
        )
    raise KeyError(name)


SCENARIO_NAMES = ("1chunk", "2chunk", "3chunk")

def floor_tier_ends(horizon):
    """(last fy of tier 1, last fy of tier 2). Tier 3 runs to the horizon."""
    span = min(10, horizon // 3)
    return span, 2 * span


def debt_floor_x(fy, horizon):
    """The floor multiple in force in year `fy`."""
    e1, e2 = floor_tier_ends(horizon)
    if fy <= e1:
        return DEBT_FLOOR_MULTIPLES[0]
    if fy <= e2:
        return DEBT_FLOOR_MULTIPLES[1]
    return DEBT_FLOOR_MULTIPLES[2]


EPS = 1e-12
HUF_BAND_RESERVE = False
"""Hold back enough HUF debt to use its nil band every remaining year.

Without it the HUF debt is exhausted around FY31 and 40.0 of nil band is
wasted over the last five years. With it, that capacity is preserved.

OFF BY DEFAULT, because it loses money. MEASURED on a 35-year run:

    off   tax 14.620   corpus 1755.3
    on    tax 12.345   corpus 1747.4

Corpus is already NET of tax, so the version with less tax is the worse plan.
Holding debt to use the band means not holding equity compounding 6-7 points
faster, and the foregone growth exceeds the tax saved. Kept as a switch because
the reasoning is not obvious and someone will propose it again.
"""
TAX_EPS = 1e-4   # Rs 10. Below this a tax figure is float noise,
                 # not a bill worth restructuring a year for.
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

def requirement_ladder(horizon):
    """Per-year post-tax requirement from the (amount, from_FY) schedule."""
    out = []
    for fy in range(1, horizon + 1):
        amount = REQUIREMENT_SCHEDULE[0][0]
        for value, start in REQUIREMENT_SCHEDULE:
            if fy >= start:
                amount = value
            else:
                break
        out.append(amount)
    return out


def build_ladders(horizon):
    n_blocks = (horizon + 4) // 5

    def spread(blocks):
        out = []
        for v in blocks[:n_blocks]:
            out += [v] * 5
        return out[:horizon]

    base = (TAXABLE_OTHER_INCOME + TAXFREE_OTHER_INCOME
            + SALARY_INCOME + RENTAL_INCOME)
    taxable_base = (TAXABLE_OTHER_INCOME
                    + max(0.0, SALARY_INCOME - SALARY_STD_DEDUCTION)
                    + RENTAL_INCOME * (1.0 - RENTAL_DEDUCTION))
    oi, toi = [], []
    for k in range(horizon):
        gross = base + OTHER_INCOME_STEP * k
        scale = gross / base if base > EPS else 0.0
        oi.append(gross)
        toi.append(taxable_base * scale)
    return dict(
        requirement=requirement_ladder(horizon),
        exempt=spread(EXEMPT_BLOCKS),
        nilband=spread(NILBAND_BLOCKS),
        other_income=oi,
        taxable_other=toi,
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

    def sell(self, nav, fy, amount=None, gain_cap=None, dearest=False):
        """LTCG-qualified lots only.

        Cheapest bucket first when raising CASH -- least gain per rupee. Dearest
        first when HARVESTING, where the object is the opposite: extinguish as
        much gain as the exemption allows per rupee churned.
        """
        cutoff = fy - EQUITY_MIN_HOLD
        order = sorted(self.funds, key=lambda f: f.front_frac(nav),
                       reverse=dearest)
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

    def harvest(self, nav, fy, gain_budget, bucket_target=None):
        """Spend leftover s.112A exemption: sell dearest, rebuy immediately.

        The exemption is annual, per assessee, and use-it-or-lose-it. Without
        this the sleeve only ever realises gain in the years it is called on
        for cash -- MEASURED at 27% utilisation over 35 years, with 72.7 of 100
        simply forfeited.

        Churning is free here: proceeds are rebought at the same NAV, so value
        is unchanged and the basis steps up by exactly the gain extinguished.
        The rebought lot carries the current fy, so EQUITY_MIN_HOLD keeps it out
        of next year's sales, which is correct.
        """
        if gain_budget <= EPS:
            return 0.0, 0.0
        gross, gain = self.sell(nav, fy, gain_cap=gain_budget, dearest=True)
        if gross > EPS:
            self.invest(gross, nav, fy, bucket_target)
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

def simulate(scenario="1chunk", horizon=HORIZON, cap=MAX_FOLIOS,
             vintage_every=VINTAGE_EVERY, no_vintage=False, verbose=False):
    if horizon < MIN_HORIZON:
        raise ValueError(
            f"horizon {horizon} is below MIN_HORIZON ({MIN_HORIZON}). The "
            f"floor tiers are min(10, N//3) years each and stop being "
            f"meaningful below that, and a drawdown shorter than 20 years is "
            f"not what this model is for.")
    spec = get_scenario(scenario)
    nf = spec["n_funds"]
    lad = build_ladders(horizon)

    if no_vintage:
        cap = 1
        vintage_every = 0

    exiting_all = set()
    for _s, _e, out, _into in spec["transitions"]:
        exiting_all |= set(out)
    base_eligible = [k for k in range(nf) if k not in exiting_all]
    _is_low = spec.get("low_fund_idx")
    low_funds = [k for k in base_eligible if _is_low and _is_low(k)]
    high_funds = [k for k in base_eligible if k not in set(low_funds)]
    target_share = spec.get("target_low_share")

    personal = Book(cap, nf)
    huf = Book(cap, nf)
    eq_p, eq_h = Sleeve(), Sleeve()
    nav_eq = 1.0
    navs = [1.0] * nf

    start_funds = [k for k in range(nf) if k < N_BASE_FUNDS]
    personal.folios[0].seed(PERSONAL_INFLOWS[0][0], navs, 0, start_funds)

    wins = [dict(start=s, end=e, out=out, into=into, armed=False, still=0.0)
            for s, e, out, into in spec["transitions"]]

    tax_p_tot = tax_h_tot = tax_eq_tot = 0.0
    rows = []

    for fy in range(1, horizon + 1):
        i = fy - 1
        for k in range(nf):
            navs[k] *= (1.0 + spec["rate"](k))
        # The HUF holds the SAME funds at the SAME NAVs as the personal purse.
        # It is not a separate asset class with its own flat return; it is the
        # same portfolio in a second pair of hands, so it earns the per-fund
        # rates and ages exactly as the personal purse does.
        hnavs = navs
        for _amount, _fy in PERSONAL_INFLOWS[1:]:
            if fy != _fy or _amount <= EPS:
                continue
            _held = {k: sum(fo.funds[k].value(navs[k]) for fo in personal.folios)
                     for k in range(nf)}
            _tot = sum(_held.values())
            if _tot <= EPS:
                personal.folios[personal.receiver].seed(_amount, navs, fy,
                                                        start_funds)
            else:
                for k, v in _held.items():
                    if v > EPS:
                        personal.folios[personal.receiver].funds[k].invest(
                            _amount * v / _tot, navs[k], fy)
        for _amount, _fy in HUF_INFLOWS:
            if fy != _fy or _amount <= EPS:
                continue
            # mirror the personal purse's CURRENT fund spread, so an inflow
            # arriving mid-transition lands in the mix that actually exists
            _held = {k: sum(fo.funds[k].value(navs[k]) for fo in personal.folios)
                     for k in range(nf)}
            _total = sum(_held.values())
            if _total <= EPS:
                huf.folios[huf.receiver].seed(_amount, navs, fy, start_funds)
            else:
                for k, v in _held.items():
                    if v > EPS:
                        huf.folios[huf.receiver].funds[k].invest(
                            _amount * v / _total, navs[k], fy)
        if EQUITY_ENABLED:
            nav_eq *= (1.0 + EQUITY_RETURN)
        eq_exempt, eq_rate = lad["eq_exempt"][i], lad["eq_rate"][i]

        need = lad["requirement"][i] - lad["other_income"][i]
        band_p = lad["exempt"][i] - lad["taxable_other"][i]
        band_h = lad["nilband"][i]

        # scheduled vintage -- the single most valuable dial in this model
        if vintage_every and fy > 1 and (fy - 1) % vintage_every == 0:
            personal.open_next()
            if any(fy >= _fy for _a, _fy in HUF_INFLOWS):
                huf.open_next()

        # ---- mandated transitions take PRIORITY over the harvest ----------
        turned = gain_turn = forced = gain_turn_h = 0.0
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

            # The HUF holds the same funds, so the same mandate applies to it.
            # Its gain is charged against the HUF's OWN nil band, not the
            # personal band -- two assessees, two allowances.
            h_left = huf.group_value(navs, w["out"])
            if h_left > EPS:
                h_slice = (h_left if fy >= w["end"]
                           else h_left / max(1, w["end"] - fy + 1))
                cap = None if fy >= w["end"] else max(0.0, band_h - gain_turn_h)
                hpr, hg, _ = huf.draw(navs, fy, amount=h_slice,
                                      gain_cap=cap, only=set(w["out"]))
                huf.put_direct(hpr, navs, fy, into_fy)
                gain_turn_h += hg

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
        _floor_x = debt_floor_x(fy, horizon)
        eq_excess = max(0.0, joint_debt + joint_eq - _floor_x * need)
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
            short = max(0.0, _floor_x * need
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
            gain_h2 += gain_turn_h
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
                        eq_gain=eq_gain_p + eq_gain_h,
                        eq_gain_p=eq_gain_p, eq_gain_h=eq_gain_h)

        # The ration-implied split first. It is free and usually optimal.
        def marginal_cost(kind, g_p, g_h, g_eqp, g_eqh, frac, d=0.01):
            """Tax on the next rupee of CASH from a source, not of gain.

            Cash is what the year needs, and gain per rupee of cash varies
            enormously between sources -- an old debt lot is ~90% gain, a fresh
            equity bucket ~20%. So a 13% equity rate on 20% gain costs 2.6% per
            rupee raised, while a 5.2% HUF slab on 85% gain costs 4.4%. Sorting
            by TAX RATE alone inverts those two.

            Marginal rates are read from the tax functions NUMERICALLY rather
            than from a hardcoded ladder, so the s.87A claw-back's 104% band is
            seen and avoided automatically instead of having to be special-cased.
            """
            if kind == "p_debt":
                r = (personal_tax(toi + g_p + d, exempt_c, band_h)
                     - personal_tax(toi + g_p, exempt_c, band_h)) / d
            elif kind == "h_debt":
                r = (huf_tax(g_h + d, band_h) - huf_tax(g_h, band_h)) / d
            elif kind == "p_eq":
                r = (equity_tax(g_eqp + d, eq_exempt, eq_rate)
                     - equity_tax(g_eqp, eq_exempt, eq_rate)) / d
            else:
                r = (equity_tax(g_eqh + d, eq_exempt, eq_rate)
                     - equity_tax(g_eqh, eq_exempt, eq_rate)) / d
            return r * frac

        def run_waterfill():
            """Fund the year free first, then fill the rest cheapest-margin up.

            The plain path lets each purse raise its share and pays whatever
            tax falls out. Once the free bands are gone that is arbitrary: it
            can take a rupee from personal debt at a 31% slab while the HUF's
            5% bracket and both equity exemptions sit unused.

            This draws every source's FREE capacity first, then water-fills the
            remaining shortfall one slice at a time, always from whichever
            source is cheapest per rupee of cash AT THAT MOMENT. Ties go to the
            personal purse.
            """
            p2, h2 = copy.deepcopy(personal), copy.deepcopy(huf)
            e2p, e2h = copy.deepcopy(eq_p), copy.deepcopy(eq_h)

            # Cash is tracked PER PURSE, not as one total, so the reported
            # cash_p / cash_h split stays real. Lumping it into cash_p once
            # made the table show the HUF contributing nothing while its debt
            # book visibly drained.
            cash_p = cash_h = eq_cash = 0.0
            g_p = g_h = g_eqp = g_eqh = 0.0
            c, gg, _ = p2.draw(navs, fy, gain_cap=band_left)
            cash_p += c; g_p += gg
            if h2.value(hnavs) > EPS:
                # Net off the HUF's transition gain, exactly as band_left does
                # for the personal purse. Passing band_h raw let the free draw
                # overshoot the nil band in transition years.
                band_h_left = max(0.0, band_h - gain_turn_h)
                c, gg, _ = h2.draw(hnavs, fy, gain_cap=band_h_left)
                cash_h += c; g_h += gg
            if EQUITY_ENABLED:
                c, gg = e2p.sell(nav_eq, fy, gain_cap=eq_exempt)
                cash_p += c; eq_cash += c; g_eqp += gg
                c, gg = e2h.sell(nav_eq, fy, gain_cap=eq_exempt)
                cash_h += c; eq_cash += c; g_eqh += gg
            cash = cash_p + cash_h

            short = max(0.0, need - cash)
            step = max(0.05, short / 60.0)
            for _ in range(200):
                if short <= EPS:
                    break
                cands = []
                for kind, book, nv in (("p_debt", p2, navs),
                                       ("h_debt", h2, hnavs),
                                       ("p_eq", e2p, None),
                                       ("h_eq", e2h, None)):
                    if kind.endswith("eq"):
                        if not EQUITY_ENABLED:
                            continue
                        live = [f for f in book.funds if f.units() > EPS]
                        if not live:
                            continue
                        frac = min(f.front_frac(nav_eq) for f in live)
                    elif kind == "h_debt":
                        # The HUF's nil band is free slab capacity EVERY year,
                        # but only while it has debt left to realise gain
                        # against. Draining it now at 5% is locally the
                        # cheapest tap and globally wrong: measured, it
                        # exhausted the HUF at FY31 and wasted 40.0 of nil band
                        # over the last five years, forcing the same cash out
                        # of equity at 13%.
                        #
                        # So hold back enough to keep realising band_h of gain
                        # a year for the rest of the plan. Value needed per
                        # year is band_h / gain_fraction; the reserve is that
                        # times the years remaining.
                        val = book.value(nv)
                        if val <= EPS:
                            continue
                        pool = [(fi, k, f) for fi in range(len(book.folios))
                                for k, f in enumerate(book.folios[fi].funds)
                                if f.units() > EPS]
                        if not pool:
                            continue
                        frac = min(f.front_frac(nv[k]) for _fi, k, f in pool)
                        yrs_left = max(0, horizon - fy)
                        if HUF_BAND_RESERVE and yrs_left > 0 and frac > 0.05:
                            reserve = yrs_left * band_h / frac
                            if val <= reserve:
                                continue
                    else:
                        if book.value(nv) <= EPS:
                            continue
                        pool = [(fi, k, f) for fi in range(len(book.folios))
                                for k, f in enumerate(book.folios[fi].funds)
                                if f.units() > EPS]
                        if not pool:
                            continue
                        frac = min(f.front_frac(nv[k]) for _fi, k, f in pool)
                    frac = max(0.0, min(1.0, frac))
                    cost = marginal_cost(kind, g_p, g_h, g_eqp, g_eqh, frac)
                    order = {"p_debt": 0, "p_eq": 1, "h_debt": 2, "h_eq": 3}[kind]
                    cands.append((cost, order, kind, book, nv))
                if not cands:
                    break
                cands.sort(key=lambda t: (t[0], t[1]))
                _cost, _o, kind, book, nv = cands[0]
                take = min(short, step)
                if kind.endswith("eq"):
                    c, gg = book.sell(nav_eq, fy, amount=take)
                    eq_cash += c
                    if kind == "p_eq":
                        g_eqp += gg
                        cash_p += c
                    else:
                        g_eqh += gg
                        cash_h += c
                else:
                    c, gg, _ = book.draw(nv, fy, amount=take)
                    if kind == "p_debt":
                        g_p += gg
                        cash_p += c
                    else:
                        g_h += gg
                        cash_h += c
                if c <= EPS:
                    break
                cash += c
                short = max(0.0, short - c)

            gpt = g_p + gain_turn
            ght = g_h + gain_turn_h
            txp0 = personal_tax(toi + gpt, exempt_c, band_h)
            txh0 = huf_tax(ght, band_h)
            # The tax must come OUT of the books, exactly as the plain path
            # does it. Reporting a bill without paying it is the conservation
            # leak that test_surplus_is_never_discarded exists to catch.
            _dp, egp = pay_tax_bill(p2, txp0, gpt, navs, fy, is_huf=False,
                                    exempt_ceiling=exempt_c, taxable_other=toi,
                                    basic_exemption=band_h)
            _dh, egh = pay_tax_bill(h2, txh0, ght, hnavs, fy, is_huf=True,
                                    nil_band=band_h)
            gpt += egp
            ght += egh
            txp = personal_tax(toi + gpt, exempt_c, band_h)
            txh = huf_tax(ght, band_h)
            txe = (equity_tax(g_eqp, eq_exempt, eq_rate)
                   + equity_tax(g_eqh, eq_exempt, eq_rate))
            return dict(tax=txp + txh + txe, p=p2, h=h2, ep=e2p, eh=e2h,
                        sold={}, cash_p=cash_p, cash_h=cash_h, surplus=0.0,
                        gain_p=gpt, gain_h=ght,
                        txp=txp, txh=txh, txe=txe,
                        eq_cash=eq_cash, eq_gain=g_eqp + g_eqh,
                        eq_gain_p=g_eqp, eq_gain_h=g_eqh)

        best = run_year(offer)
        best_offer = offer

        # If it costs tax, the marginal rupee may be far cheaper in the HUF:
        # the personal side is in the slab regime (26-31%) or the s.87A
        # claw-back (104%), while the HUF's first bracket above its exemption
        # is 5.2%. Search upward, holding back enough HUF value to keep its own
        # free band usable in every remaining year.
        if best["tax"] > EPS and huf.value(hnavs) > EPS:
            # DIMENSIONS. band_h is measured in realised GAIN; huf.value is
            # measured in portfolio VALUE. Subtracting one from the other
            # assumes a 100% gain fraction and over-reserves by 1/frac -- at a
            # 50% gain fraction it holds back twice what is needed. With
            # band_h = 10 and 30 years left that reserved 300 against an HUF
            # worth ~190, so spill_max was zero and the spillover search never
            # ran at all for most of the horizon.
            _hf = 1.0
            _hpool = [(k, f) for fo in huf.folios
                      for k, f in enumerate(fo.funds) if f.units() > EPS]
            if _hpool:
                _hf = max(0.05, min(1.0, min(f.front_frac(hnavs[k])
                                             for k, f in _hpool)))
            reserve = max(0.0, (horizon - fy) * band_h / _hf)
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

        # Water-filling is tried only when the plain path pays a MEANINGFUL
        # amount of tax. EPS is 1e-12; a tax bill of nine nanopaise is a float
        # artefact, and letting it trigger a restructure once cost 25 of corpus
        # to save nothing.
        #
        # And it is chosen on TERMINAL VALUE, not on tax. Minimising tax alone
        # will happily spend equity compounding at EQUITY_RETURN to avoid a
        # smaller bill on debt compounding at the chunk rate -- which is a bad
        # trade whenever the growth differential over the remaining horizon
        # exceeds the tax saved. Each purse is projected forward at its own
        # rate to the end of the plan and the larger total wins.
        if best["tax"] > TAX_EPS:
            alt = run_waterfill()
            _yrs = max(0, horizon - fy)
            _rd = (1.0 + chunk_rates(1)[0]) ** _yrs
            _re = (1.0 + EQUITY_RETURN) ** _yrs

            def _terminal(c):
                return ((c["p"].value(navs) + c["h"].value(hnavs)) * _rd
                        + (c["ep"].value(nav_eq) + c["eh"].value(nav_eq)) * _re)

            if _terminal(alt) > _terminal(best) + TAX_EPS:
                best = alt

        personal, huf = best["p"], best["h"]
        eq_p, eq_h = best["ep"], best["eh"]

        # ---- spend whatever s.112A exemption the year did not use ----------
        eq_harv_gain = 0.0
        if EQUITY_ENABLED:
            for _sl, _used in ((eq_p, best.get("eq_gain_p", 0.0)),
                               (eq_h, best.get("eq_gain_h", 0.0))):
                _left = max(0.0, eq_exempt - _used)
                if _left > EPS and _sl.value(nav_eq) > EPS:
                    _g, _gn = _sl.harvest(nav_eq, fy, _left, bucket_target)
                    eq_harv_gain += _gn
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
            debt_multiple=_floor_x,
            debt_cover=((p_debt + huf_debt) / need if need > EPS else 0.0),
            folios_open=len(personal.folios),   # retained: CSV column expansion
            value_equity=val_eq,
            equity_cash=best.get("eq_cash", 0.0),
            equity_gain=best.get("eq_gain", 0.0) + eq_harv_gain,
            equity_harvest_gain=eq_harv_gain,
            tax_equity=best.get("txe", 0.0),
            folio_values=[f.value(navs) for f in personal.folios],
            tax_personal=tax_p_final,
            tax_huf=tax_h_final,
            tax_total=tax_p_final + tax_h_final + best.get("txe", 0.0),
            tax_cumulative=tax_p_tot + tax_h_tot + tax_eq_tot,
        ))

    if verbose:
        print(f"{'FY':>3}{'band':>7}{'need':>7}{'cash_p':>8}{'cash_h':>8}"
              f"{'surp':>8}{'trans':>8}{'gain':>7}{'mult':>6}{'cvr':>6}{'fol':>5}"
              f"{'p_debt':>10}{'p_eq':>9}{'h_debt':>9}{'h_eq':>8}"
              f"{'eq$':>7}{'tax':>7}{'cum':>8}")
        print("-" * 130)
        for r in rows:
            print(f"{r['fy']:>3}{r['personal_band']:>7.2f}{r['net_need']:>7.2f}"
                  f"{r['personal_cash']:>8.2f}{r['huf_cash']:>8.2f}"
                  f"{r['surplus']:>8.2f}{r['transition']:>8.2f}"
                  f"{r['gain_personal']:>7.2f}"
                  f"{r['debt_multiple']:>6.0f}{r['debt_cover']:>6.1f}"
                  f"{r['p_debt_folios']:>5}"
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


def _echo_invocation(a):
    """Echo a fully-populated command line on every run.

    Every dial spelled out with the value actually in effect, so the full
    parameter set is discoverable from any invocation rather than only from
    --help or the docstring. Built from the parsed args, so it cannot go stale
    when a flag is added.
    """
    parts = [
        f"--scenario {a.scenario}",
        f"--horizon {a.horizon}",
        f"--vintage {a.vintage}",
        f"--max_folios {a.max_folios}",
        "--interest_rate " + "/".join(f"{r:g}" for r in INTEREST_RATES),
        f"--equity_cagr {EQUITY_RETURN:g}",
        "--personal_corpus " + "/".join(f"{amt:g}" for amt, _ in PERSONAL_INFLOWS),
        "--personal_year " + "/".join(str(fy) for _, fy in PERSONAL_INFLOWS),
        "--huf_corpus " + "/".join(f"{amt:g}" for amt, _ in HUF_INFLOWS),
        "--huf_year " + "/".join(str(fy) for _, fy in HUF_INFLOWS),
        f"--ration_k {RATION_K:g}",
        f"--equity_max_share {EQUITY_MAX_SHARE:g}",
        f"--income_step_absolute {OTHER_INCOME_STEP:g}",
        f"--taxable_other_income {TAXABLE_OTHER_INCOME:.2f}",
        f"--taxfree_other_income {TAXFREE_OTHER_INCOME:.2f}",
        f"--salary_income {SALARY_INCOME:.2f}",
        f"--rental_income {RENTAL_INCOME:.2f}",
        "--post_tax_requirement "
        + ",".join(f"{amt:g}/{fy}" for amt, fy in REQUIREMENT_SCHEDULE),
        "--turnover " + "/".join(f"{t:g}" for t in TURNOVER),
        "--debt_corpus_2_req_multiple "
        + "/".join(f"{m:g}" for m in DEBT_FLOOR_MULTIPLES),
    ]
    if a.no_vintage:
        parts.append("--no_vintage")
    if a.sweep:
        parts.append("--sweep")
    if a.quiet:
        parts.append("--quiet")
    parts.append(f"--csv {a.csv}" if a.csv else "--csv out.csv")

    _e1, _e2 = floor_tier_ends(a.horizon)
    _req = requirement_ladder(a.horizon)
    _spans = []
    for _j, (_amt, _from) in enumerate(REQUIREMENT_SCHEDULE):
        if _from > a.horizon:
            break
        _to = (REQUIREMENT_SCHEDULE[_j + 1][1] - 1
               if _j + 1 < len(REQUIREMENT_SCHEDULE) else a.horizon)
        _spans.append(f"FY{_from}-{min(_to, a.horizon)} at {_amt:g}")
    print("post-tax requirement: " + ", ".join(_spans))
    _n1, _n2 = _turnover_counts()
    print(f"turnover: {TURNOVER[0]:.1%} then {TURNOVER[1]:.1%} of the "
          f"{N_BASE_FUNDS} base funds ({_n1} then {_n2} funds)")
    print(f"debt floor tiers: FY1-{_e1} at {DEBT_FLOOR_MULTIPLES[0]:g}X, "
          f"FY{_e1 + 1}-{_e2} at {DEBT_FLOOR_MULTIPLES[1]:g}X, "
          f"FY{_e2 + 1}-{a.horizon} at {DEBT_FLOOR_MULTIPLES[2]:g}X")
    print("full parameter set (values shown are the ones in effect):")
    line = "  python3 scripts/swp_dp_model.py"
    for p in parts:
        if len(line) + len(p) + 1 > 78:
            print(line + " \\")
            line = "      " + p
        else:
            line += " " + p
    print(line)
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="1chunk", choices=sorted(SCENARIO_NAMES))
    ap.add_argument("--horizon", type=int, default=HORIZON)
    ap.add_argument("--max_folios", "--max-folios", "--cap",
                    type=int, default=MAX_FOLIOS, dest="max_folios",
                    help="maximum debt cohorts per purse")
    ap.add_argument("--vintage", type=int, default=VINTAGE_EVERY,
                    help="open a fresh receiver every N years; 0 = on drain")
    ap.add_argument("--no_vintage", "--no-vintage", action="store_true",
                    help="never open new folios (cap = 1)")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--rate_regime_stress_test", "--rate-regime-stress-test",
                    action="store_true", dest="rate_stress",
                    help="sweep flat interest rates from 7%% down to 4%% in "
                         "0.5%% steps. Forces 1chunk and ignores "
                         "--scenario/--interest_rate/--turnover; every other "
                         "parameter is kept.")
    ap.add_argument("--sweep", action="store_true",
                    help="compare scenarios, horizons and vintage rules")
    ap.add_argument("--interest_rate", "--interest-rate", type=str,
                    default=None, dest="interest_rate",
                    help="one, two or three debt rates as decimals, slash "
                         "separated: 0.045 spreads by +-0.25%% per chunk; "
                         "0.055/0.045 gives the first and last chunk and "
                         "interpolates a middle one; 0.065/0.055/0.045 is "
                         "taken literally.")
    ap.add_argument("--taxable_other_income", type=float, default=None,
                    help="fully taxable other income, Rs lakh/yr")
    ap.add_argument("--taxfree_other_income", type=float, default=None,
                    help="tax-free other income, Rs lakh/yr")
    ap.add_argument("--salary_income", type=float, default=None,
                    help="salary, Rs lakh/yr (s.16(ia) deduction applied)")
    ap.add_argument("--rental_income", type=float, default=None,
                    help="rent, Rs lakh/yr (s.24(a) 30%% deduction applied)")
    ap.add_argument("--personal_corpus", "--personal-corpus",
                    "--starting_corpus", "--starting-corpus", type=str,
                    default=None, dest="personal_corpus",
                    help="personal inflow amounts in Rs LAKH, slash separated: "
                         "500/20/30/40. The first is the opening corpus. "
                         "Paired positionally with --personal_year.")
    ap.add_argument("--personal_year", "--personal-year", type=str,
                    default=None, dest="personal_year",
                    help="FYs the personal inflows land: 1/6/9/14. The first "
                         "must be FY1. Same count as --personal_corpus.")
    ap.add_argument("--huf_corpus", "--huf-corpus", type=str, default=None,
                    dest="huf_corpus",
                    help="HUF inflow amounts in Rs LAKH, slash separated: "
                         "190/60/40. Paired positionally with --huf_year.")
    ap.add_argument("--huf_year", "--huf-year", type=str, default=None,
                    dest="huf_year",
                    help="FYs the HUF inflows land, slash separated: 5/8/12. "
                         "Must have the same count as --huf_corpus.")
    ap.add_argument("--ration_k", "--ration-k", type=float, default=None,
                    dest="ration_k",
                    help="HUF draw pacing. offer = k x huf_value / years_left, "
                         "capped at the HUF's own tax-free capacity.")
    ap.add_argument("--equity_max_share", "--equity-max-share", type=float,
                    default=None, dest="equity_max_share",
                    help="equity deployment cap as a share of the purse")
    ap.add_argument("--income_step_absolute", "--income-step-absolute",
                    "--income_step", type=float, default=None,
                    dest="income_step_absolute",
                    help="ABSOLUTE lakh added to total other income each year. "
                         "0.10 is Rs 10,000/yr, not 10 percent. Linear.")
    ap.add_argument("--equity_cagr", "--equity-cagr", type=float, default=None,
                    dest="equity_cagr",
                    help="equity sleeve CAGR as a decimal, e.g. 0.12. "
                         "Deterministic -- see EQUITY_SIGMA in the docstring "
                         "for why the dispersion around it is not modelled.")
    ap.add_argument("--post_tax_requirement", "--post-tax-requirement",
                    type=str, default=None, dest="post_tax_requirement",
                    help="post-tax requirement schedule as amount/from_FY "
                         "pairs, comma separated, e.g. "
                         "35/1,40/6,45/11,50/16,52.5/21,55/25,57.5/31. Each "
                         "pair holds until the next starts; the last runs to "
                         "the horizon. Blocks need not be five years. NOTE the "
                         "separators: SLASH splits amount from year, COMMA "
                         "splits pairs.")
    ap.add_argument("--turnover", type=str, default=None,
                    help="fraction of the initial fund count moved at each "
                         "chunk boundary, slash separated, e.g. 0.33/0.25. "
                         "3chunk uses both, 2chunk uses the first. A single "
                         "value applies to every boundary.")
    ap.add_argument("--debt_corpus_2_req_multiple", "--debt-corpus-2-req-multiple",
                    type=str, default=None, dest="floor_multiples",
                    help="three debt-floor multiples, slash separated, e.g. "
                         "25/22/18. Applied over tiers of min(10, N//3) years, "
                         "min(10, N//3) years, and the remainder.")
    a = ap.parse_args()

    # Overrides are module globals because the scenario rate lambdas and
    # build_ladders() read them directly. Set before any run.
    if a.interest_rate is not None:
        try:
            rr = [float(x) for x in a.interest_rate.replace(",", "/").split("/")]
        except ValueError:
            sys.exit(f"FATAL: could not read --interest_rate "
                     f"{a.interest_rate!r}. Expected one to three decimals "
                     f"like 0.065 or 0.065/0.055/0.045.")
        if not 1 <= len(rr) <= 3:
            sys.exit(f"FATAL: --interest_rate takes one to three values, got "
                     f"{len(rr)}: {a.interest_rate!r}")
        if any(not (0.0 < x < 1.0) for x in rr):
            sys.exit("FATAL: interest rates are DECIMALS, not percentages. "
                     "Use 0.075, not 7.5.")
        if len(rr) > 1 and not all(rr[i] >= rr[i + 1] for i in range(len(rr) - 1)):
            print(f"  NOTE: rates {rr} do not step DOWN. That is allowed, but "
                  f"the chunk structure exists to move money into "
                  f"lower-yielding funds over time, so a rising path inverts "
                  f"the premise.")
        globals()["INTEREST_RATES"] = tuple(rr)

    for flag, name in (("taxable_other_income", "TAXABLE_OTHER_INCOME"),
                       ("taxfree_other_income", "TAXFREE_OTHER_INCOME"),
                       ("salary_income", "SALARY_INCOME"),
                       ("rental_income", "RENTAL_INCOME")):
        val = getattr(a, flag)
        if val is not None:
            globals()[name] = val

    # Money parameters are in LAKH throughout, like every other figure in this
    # model. A corpus entered in crore is a factor-of-100 error that would
    # quietly model a plan with no chance of funding itself, so it is caught.
    def _inflows(amounts_s, years_s, label, first_must_be_1):
        try:
            amounts = [float(x) for x in amounts_s.replace(",", "/").split("/")]
            years = [int(x) for x in years_s.replace(",", "/").split("/")]
        except ValueError:
            sys.exit(f"FATAL: could not read --{label}_corpus {amounts_s!r} / "
                     f"--{label}_year {years_s!r}. Expected 500/20/30 and 1/6/9.")
        if len(amounts) != len(years):
            sys.exit(f"FATAL: --{label}_corpus has {len(amounts)} amount(s) but "
                     f"--{label}_year has {len(years)} year(s). They pair up "
                     f"positionally, so the counts must match.")
        if any(x <= 0 for x in amounts):
            sys.exit(f"FATAL: {label} inflows must be positive.")
        if any(y < 1 for y in years):
            sys.exit(f"FATAL: --{label}_year values must be 1 or later.")
        if len(set(years)) != len(years):
            sys.exit(f"FATAL: --{label}_year has a repeated year: {years}.")
        if sum(amounts) < 25.0:
            sys.exit(f"FATAL: --{label}_corpus totals {sum(amounts):g}, which "
                     f"is in Rs LAKH not crore. Did you mean "
                     f"{sum(amounts) * 100:g}?")
        pairs = tuple(sorted(zip(amounts, years), key=lambda t: t[1]))
        if first_must_be_1 and pairs[0][1] != 1:
            sys.exit(f"FATAL: the first --{label}_year must be 1. The plan has "
                     f"no corpus to draw on before its opening balance "
                     f"arrives.")
        return pairs

    if (a.personal_corpus is None) != (a.personal_year is None):
        # one value with no year is the old single-corpus form; accept it
        if a.personal_year is None and a.personal_corpus is not None \
                and "/" not in a.personal_corpus:
            a.personal_year = "1"
        else:
            sys.exit("FATAL: --personal_corpus and --personal_year go together. "
                     "Give both, or give a single --personal_corpus alone.")
    if a.personal_corpus is not None:
        globals()["PERSONAL_INFLOWS"] = _inflows(
            a.personal_corpus, a.personal_year, "personal", True)

    if (a.huf_corpus is None) != (a.huf_year is None):
        sys.exit("FATAL: --huf_corpus and --huf_year go together. Give both or "
                 "neither.")
    if a.huf_corpus is not None:
        globals()["HUF_INFLOWS"] = _inflows(a.huf_corpus, a.huf_year, "huf",
                                            False)

    for flag, name, lo, hi in (("equity_max_share", "EQUITY_MAX_SHARE", 0.0, 1.0),):
        val = getattr(a, flag)
        if val is not None:
            if not (lo < val < hi):
                sys.exit(f"FATAL: --{flag} is a DECIMAL between 0 and 1. "
                         f"Got {val}.")
            globals()[name] = val

    if a.ration_k is not None:
        if a.ration_k < 0:
            sys.exit("FATAL: --ration_k must be non-negative. 0 means the HUF "
                     "offers nothing and only backstops.")
        globals()["RATION_K"] = a.ration_k

    if a.income_step_absolute is not None:
        globals()["OTHER_INCOME_STEP"] = a.income_step_absolute

    if a.equity_cagr is not None:
        if not (0.0 < a.equity_cagr < 1.0):
            sys.exit(f"FATAL: --equity_cagr is a DECIMAL, not a percentage. "
                     f"Use 0.12, not 12. Got {a.equity_cagr}.")
        globals()["EQUITY_RETURN"] = a.equity_cagr

    if a.post_tax_requirement is not None:
        pairs = []
        for token in a.post_tax_requirement.split(","):
            token = token.strip()
            if not token:
                continue
            if "/" not in token:
                sys.exit(f"FATAL: {token!r} is not an amount/from_FY pair. The "
                         f"schedule looks like 35/1,40/6,45/11 -- slash splits "
                         f"amount from year, comma splits pairs.")
            amt_s, fy_s = token.split("/", 1)
            try:
                pairs.append((float(amt_s), int(fy_s)))
            except ValueError:
                sys.exit(f"FATAL: could not read the pair {token!r}. Expected "
                         f"a number, a slash, then a whole year.")
        if not pairs:
            sys.exit("FATAL: --post_tax_requirement is empty.")
        if pairs[0][1] != 1:
            sys.exit(f"FATAL: the first pair must start at FY1, not FY"
                     f"{pairs[0][1]}. Years before the first pair would have "
                     f"no requirement at all.")
        if any(pairs[i][1] >= pairs[i + 1][1] for i in range(len(pairs) - 1)):
            sys.exit(f"FATAL: the from_FY values must strictly increase, got "
                     f"{[p[1] for p in pairs]}.")
        if any(amt <= 0 for amt, _ in pairs):
            sys.exit("FATAL: requirements must be positive.")
        globals()["REQUIREMENT_SCHEDULE"] = tuple(pairs)

    if a.turnover is not None:
        try:
            tv = [float(x) for x in a.turnover.replace(",", "/").split("/")]
        except ValueError:
            sys.exit(f"FATAL: could not read --turnover {a.turnover!r}. "
                     f"Expected one or two fractions like 0.33/0.25.")
        if len(tv) == 1:
            tv = tv * 2
        if len(tv) != 2:
            sys.exit(f"FATAL: --turnover takes one or two values, got "
                     f"{len(tv)}: {a.turnover!r}")
        if any(not (0.0 < x < 1.0) for x in tv):
            sys.exit("FATAL: turnover fractions must be between 0 and 1 "
                     "exclusive. They are fractions, not percentages.")
        if tv[0] + tv[1] >= 1.0:
            sys.exit(f"FATAL: turnover {tv[0]:.3f} + {tv[1]:.3f} = "
                     f"{tv[0] + tv[1]:.3f} would move the entire base holding "
                     f"and leave nothing in the chunk-1 funds. Keep the sum "
                     f"below 1.0.")
        globals()["TURNOVER"] = tuple(tv)

    if a.floor_multiples is not None:
        try:
            parts = [float(x) for x in a.floor_multiples.replace(",", "/").split("/")]
        except ValueError:
            sys.exit(f"FATAL: could not read --debt_corpus_2_req_multiple "
                     f"{a.floor_multiples!r}. Expected three numbers like 25/22/18.")
        if len(parts) != 3:
            sys.exit(f"FATAL: --debt_corpus_2_req_multiple needs exactly three "
                     f"values, got {len(parts)}: {a.floor_multiples!r}")
        if any(p <= 0 for p in parts):
            sys.exit("FATAL: floor multiples must be positive.")
        if not (parts[0] >= parts[1] >= parts[2]):
            print(f"  NOTE: floor multiples {parts} do not step DOWN. That is "
                  f"allowed, but the liability shrinks over the horizon, so a "
                  f"rising floor traps surplus against a debt that is going "
                  f"away.")
        globals()["DEBT_FLOOR_MULTIPLES"] = tuple(parts)

    if a.horizon < MIN_HORIZON:
        sys.exit(f"FATAL: --horizon {a.horizon} is below the minimum of "
                 f"{MIN_HORIZON}.")

    _tot = (TAXABLE_OTHER_INCOME + TAXFREE_OTHER_INCOME
            + SALARY_INCOME + RENTAL_INCOME)
    if _tot <= 0:
        sys.exit("FATAL: the four income heads sum to zero.")
    _echo_invocation(a)
    _n = {"1chunk": 1, "2chunk": 2, "3chunk": 3}[a.scenario]
    _rt = chunk_rates(_n)
    print("chunk rates: "
          + ", ".join(f"chunk {j + 1} {x:.4%}" for j, x in enumerate(_rt))
          + (f"   (from {'/'.join(f'{r:g}' for r in INTEREST_RATES)})"
             if len(INTEREST_RATES) != _n else ""))
    print("corpus: personal "
          + " + ".join(f"{amt:g} at FY{fy}" for amt, fy in PERSONAL_INFLOWS)
          + f" (total {sum(x for x, _ in PERSONAL_INFLOWS):g}); HUF "
          + " + ".join(f"{amt:g} at FY{fy}" for amt, fy in HUF_INFLOWS)
          + f" (total {sum(x for x, _ in HUF_INFLOWS):g})")
    print("the HUF holds the same funds at the same NAVs as the personal purse "
          "and transitions with it")
    print(f"equity CAGR {EQUITY_RETURN:.2%}"
          + ("" if EQUITY_ENABLED else "   (sleeve DISABLED)"))
    print(f"income {_tot:.2f} "
          f"(taxable-other {TAXABLE_OTHER_INCOME:.2f}, tax-free "
          f"{TAXFREE_OTHER_INCOME:.2f}, salary {SALARY_INCOME:.2f}, "
          f"rent {RENTAL_INCOME:.2f})")

    if a.rate_stress:
        print("RATE REGIME STRESS TEST")
        print("Flat rates, 1chunk -- chunk structure and turnover are switched")
        print("off so the rate is the only thing moving. Every other parameter")
        print("is as printed above.\n")
        print(f"{'rate':>7}{'tax total':>11}{'  personal':>11}{'HUF':>8}"
              f"{'equity':>9}   {'corpus':>10}{'personal':>11}{'HUF':>10}")
        print("-" * 78)
        base = 0.070
        while base >= 0.0399:
            globals()["INTEREST_RATES"] = (base,)
            r = simulate("1chunk", a.horizon, a.max_folios,
                         vintage_every=a.vintage, no_vintage=a.no_vintage)
            p_tot = r["p_debt"] + r["p_eq"]
            h_tot = r["huf_debt"] + r["huf_eq"]
            print(f"{base:>7.2%}{r['tax']:>11.3f}{r['tax_personal']:>11.3f}"
                  f"{r['tax_huf']:>8.3f}{r['tax_equity']:>9.3f}   "
                  f"{r['corpus']:>10.1f}{p_tot:>11.1f}{h_tot:>10.1f}")
            base -= 0.005
        print("\nCorpus is NET of tax. Read the corpus columns, not the tax")
        print("columns: a low-rate world produces little gain and therefore")
        print("little tax, which is a symptom of a poorer plan, not a better one.")
        return 0

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
                    r = simulate(sc, hz, a.max_folios, vintage_every=vt, no_vintage=nv)
                    fol = (f"{r['p_debt_folios']}/{r['p_eq_folios']}"
                           f"/{r['huf_debt_folios']}/{r['huf_eq_folios']}")
                    print(f"{sc:>10}{hz:>8}{vt_label:>11}"
                          f"{r['tax']:>9.3f}{r['corpus']:>10.1f}"
                          f"{r['p_debt']:>10.1f}{r['p_eq']:>9.1f}"
                          f"{r['huf_debt']:>9.1f}{r['huf_eq']:>8.1f}"
                          f"{fol:>16}")
            print()
        return 0

    r = simulate(a.scenario, a.horizon, a.max_folios, vintage_every=a.vintage,
                 no_vintage=a.no_vintage, verbose=not a.quiet)
    print(f"\nscenario {a.scenario}, {a.horizon} years, max_folios {a.max_folios}, "
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