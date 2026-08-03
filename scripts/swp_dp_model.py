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

                    Every money figure here is LAKH, so 100 is 1 crore. A
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

It was previously a flat-rate pool that ignored --interest_rate and never
transitioned. In a low-rate world that was worth a large slice of corpus and
the difference between the second purse surviving and being exhausted, so
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
                    than compound: over a 35-year horizon it adds 3.4 to the
                    starting total, not the far larger figure a compound rate
                    would give. Shared pro-rata across
                    the four heads so the taxable fraction stays stable.
                    --income_step is still accepted as an alias.

--equity_cagr       Equity sleeve CAGR as a decimal, e.g. 0.12. DETERMINISTIC:
                    EQUITY_SIGMA exists but is unused, because a fixed model
                    cannot use a dispersion for anything but false precision.
                    The sleeve is a CORPUS lever and barely a tax one: past
                    the point where the s.112A exemption is fully used the tax
                    stops responding to the rate altogether while the corpus
                    keeps climbing, because the exemption is fixed in rupees
                    and does not scale with the sleeve.

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
taxable fraction is stable. The TAXABLE portion is the lever, not the gross --
it is subtracted from the s.87A ceiling to give the year's tax-free band, so
moving income from a tax-free head to a taxable one costs band every year.

BOUNDARY_GRACE  Every transition deadline sits this many years beyond its chunk
                boundary. No setting in the usable range produces a forced
                liquidation, so the grace is not buying safety. What it buys is
                corpus -- money stays in the higher-rate chunk-1 funds longer
                -- at the cost of realising gain in later, more contested
                years. Longer grace, more corpus and more tax, monotonically.

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

RESULTS ARE NOT QUOTED IN THIS FILE. Every figure here was measured against a
parameter set that has since moved, and a stale number in a docstring is worse
than no number: it reads as a specification. Run the case you care about --
`--sweep` for the scenario grid, `--conservative_sweep` for the risk trade,
`--rate_regime_stress_test` for the rate one -- and read the output.

KNOWN BROKEN: 3chunk does not fully hold its allocation. The transitions are one-way
  EMPTYING mandates ("f15-f21 must be empty by FY10"), so nothing keeps money
  in the destination funds afterwards. Three mechanisms now push back -- global
  drawing, protecting under-allocated funds from the cash draw, and aiming the
  harvest at the over-allocated side so its proceeds are a genuine transfer --
  and together they lift the low-rate share substantially.

  Chunk 2 essentially hits its target. CHUNK 3 DOES NOT, and it is not
  reachable: closing the gap means shifting a large block of value at a high
  gain fraction, and the realised gain that implies is several times the band
  available across the whole decade. The target is arithmetically out of reach
  tax-free, exactly as the FY4-FY10 liquidation mandate was in an earlier
  version.

  So 3chunk's tax and corpus remain NOT comparable to 1chunk and 2chunk. The
  honest options are to accept a slower drift into the target mix, or to price
  the tax of hitting it on a deadline. There is no free version.

SURPLUS ROUTING -- TWO CONSTRAINTS, GUARDING DIFFERENT THINGS

Harvest surplus is routed by two rules that both bind:

  DEBT_FLOOR_X   liquidity. Surplus first rebuilds the combined debt corpus to
                 a set number of years of net need. Only what is left over may
                 go to equity.
  EQUITY_MAX_SHARE  allocation. Equity is additionally capped as a share of the
                 purse on deployment.

THE FLOOR ALONE IS NOT SUFFICIENT, and the reason is the point of the whole
sleeve, inverted. If a loose floor is the only constraint, almost everything is
spare, equity balloons to over half the corpus, and the tax bill rises sharply
-- ALL of it SLAB tax on a starved debt book, with equity tax at zero
throughout. s.112A caps the tax-free equity drawdown at the ANNUAL EXEMPTION
regardless of sleeve size, so a sleeve several times larger draws exactly the
same tax-free cash as a small one. Every rupee of equity past that adds ZERO
tax-free capacity while displacing debt, whose capacity scales with the slab
band and the harvest. Equity must be sized by what its exemption can absorb,
not by what happens to be spare.

A GOTCHA WORTH REMEMBERING: every rupee of surplus must land somewhere. Two
successive versions of the routing silently DISCARDED the above-floor remainder
when equity declined it, which vanished a large slice of corpus and produced a
completely convincing false conclusion. The control that catches it is cheap: with
EQUITY_ENABLED = False the routing must reproduce the no-equity run EXACTLY.
Run it after any change here.

WATER-FILLING WHEN THE FREE BANDS RUN OUT

Once every purse's tax-free capacity is spent, the plain path lets each purse
raise its share and pays whatever tax falls out. That is arbitrary: it can take
a rupee from personal debt at a 31% slab while the HUF's 5% bracket and both
s.112A exemptions sit unused.

`run_waterfill` draws each source's FREE capacity -- capped at what the year
still needs, never more -- then fills the remaining shortfall one slice at a
time from whichever source is cheapest AT THAT MOMENT, across four taps --
personal debt, HUF debt, personal equity, HUF equity. Ties go to the personal
purse.

THE FREE DRAWS ARE CAPPED AT need -- and this is a third instance of the "every
rupee must land somewhere" leak recorded above under SURPLUS ROUTING. The free
draws once took each source's FULL tax-free capacity regardless of the year's
need, routinely 8-16 above it (equity alone ~5). `run_waterfill` reinvests
nothing (its surplus is 0), so that excess was liquidated straight out of the
corpus and lost. It never showed as a wrong committed number, because the leak
sank this candidate's TERMINAL VALUE below the plain path in exactly the late
years where it had driven tax to zero -- so the water-filling branch quietly
never won. Capping each free draw at the running shortfall keeps the spare
capacity invested and lets the terminal comparison be honest.
When free capacity is SHORT of need -- the tax-paying case the water-fill loop
exists for -- the gain cap binds first and the amount cap is inert, so nothing
changes there.

THE CAP CLOSED ONLY HALF OF IT. Stopping the draw at `need` stopped the corpus
walking out of the door, but it also stopped this candidate doing the basis
reset `run_year` does every year through fund_year's surplus -- so whatever was
left of the personal band simply expired, and the two candidates were still not
compared on equal footing. The residue is now harvested and put straight back
through the same floor-then-equity routing. It moves little, because in the
years the water-fill runs at all the free draw has usually spent the band
already.

THE SAME HARVEST ON THE HUF SIDE IS NOT TAKEN. It looks better where the plan
is comfortable, but resetting the HUF's basis lowers its gain fraction, which
raises the tax-free CASH its nil band buys, which makes it the cheapest tap in
every year and drains it. Where other income is high the HUF is then exhausted
years early and the last years fall on the personal slab, costing far more than
the harvest gained. The harvest is not the defect -- the water-fill has no
pacing rule for the HUF at all, where the plain path has RATION_K -- but until
it has one, the harvest is a lever that makes the missing rule bite harder.

AND THE MISSING PACING RULE WAS BUILT, MEASURED, AND NOT KEPT. Two versions
were tried: RATION_K applied to the water-fill, and HUF_BAND_RESERVE turned on
with an annuity present value. Both are worse than no rule at all, and the
reason is structural rather than a tuning failure. The water-fill exists to
take the cheapest marginal rupee; a pacing rule is a standing instruction to
NOT take it. Holding the HUF back pushes the same cash onto the personal purse
years earlier, into the s.87A claw-back, and the claw-back costs far more than
the nil band that pacing preserves. The reserve version keeps the HUF alive a
few years longer and still ends at zero, buying that reprieve with both tax and
corpus.

It is NOT universally worse. Where other income is high enough that the
personal purse is already deep in slab and the HUF is the only cheap source,
the ration version wins on both tax and corpus. No single rule dominates, so
none is imposed. The HUF running dry before the horizon is usually the optimum,
not a defect -- which is the opposite of what it looks like in the trace, and
is why it is written down here.

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
at the chunk rate. Measured, the tax-minimising path can save tax and cost
several times as much corpus. Each purse is projected forward at
its own rate to the end of the plan and the larger total wins, so that trade is
correctly refused.

TAX_EPS guards the trigger. EPS is 1e-12, and a tax bill of nine nanopaise --
which a 35-year run really does produce -- once triggered a restructure that
cost real corpus to save nothing.

THE EQUITY SLEEVE (s.112A)

MEASURED CONFIGURATION SWEEP -- do not re-tune without a reason. Bucket share
across a wide range, cap basis (market vs cost), one-bucket-per-year, bucket
count, and an annual deployment throttle were all tested.
NONE moves the 35-year answer beyond the noise floor, and the simplest
configuration wins. The sleeve's internal structure is inert here because the
surplus that would feed fresh cohorts is concentrated in the first decade and
has all but dried up long before the horizon. Sequential filling cannot
separate vintages that
were never created, and a throttle can only redistribute money that is not
there. This is the same constraint that made a late vintage cadence inert on
the debt side.

Capping on cumulative COST rather than market value keeps the door open and
admits far more equity, and buys corpus, but costs tax -- surplus routed to
equity starves the debt purse. Market-value capping is the better trade.

A separate folio F_EQ per purse, N_EQ_FUNDS buckets, funded from harvest
surplus only -- never by selling debt to buy equity. No lot is sold before it
qualifies as long term. Equity LTCG is a SEPARATE tax head: a flat rate above
its own annual exemption, and not eligible for the s.87A rebate, so it neither
consumes the slab band nor is sheltered by it.

THE TRIGGER IS JOINT, AND THAT IS WHERE THE VALUE IS. Personal + HUF corpus
must exceed EQUITY_TRIGGER_X years of the post-tax amount the two purses
jointly fund by redemption. An earlier per-purse version never opened the HUF
sleeve at all -- its own corpus never cleared the multiple of its own draw --
so only one s.112A exemption was ever in play. Moving to the joint test was
where most of the sleeve's value turned out to be.

SEQUENTIAL BUCKET FILLING IS INERT HERE, and the reason is worth recording.
Buckets are filled one at a time to EQ_BUCKET_SHARE of the excess over the
trigger, the pointer carrying across years and a filled bucket never topped up
until the cycle wraps. The intent was vintage separation inside the sleeve, so
a draw could take a one- or two-year-old bucket at a low gain fraction instead
of the sleeve average.

Measured, it lands below the noise floor. It fails for the same reason the accelerated late vintage cadence failed on the
debt side: THE SLEEVE STOPS BEING FED AT FY8. All deployment happens in a short
early window and never resumes, so every bucket is filled inside a few years of
its neighbours and all of them are decades old by the time they are drawn.
Observed gain fractions on equity draws are near the sleeve average, not the
low figure the design intended. The machinery is correct; there is
simply nothing for it to separate. It would start to matter if the sleeve were
topped up through the horizon.

EQUITY_MAX_SHARE IS A DEPLOYMENT CAP, NOT A HOLDING. It limits what is paid IN;
nothing trims back. Equity compounds faster than debt for three decades, so the
holding drifts well above the cap. Raising the cap trades tax for corpus
monotonically, and the shipped value sits close to the tax optimum: tax rises
with the cap because money in equity is money the debt purse cannot use to
manage its band, while the s.112A exemption stays fixed regardless of sleeve
size. Whether the resulting equity holding is acceptable at the end of a plan
is a risk question this model cannot answer -- see below.

AND THERE IS NO HOLDING CEILING AT ALL. THIS IS DELIBERATE, AND IT IS THE ONE
THING ABOUT THIS MODEL MOST LIKELY TO MISLEAD ITS OWNER. The drift is mild only
when equity barely beats debt. Widen that gap -- a low-rate debt world against
a healthy equity assumption -- and a deployment cap around an eighth of the
purse ends holding something closer to two thirds of it, climbing past a
quarter around the third decade and past half in the last few years. Both
numbers are true and they are not in conflict: the cap governs INFLOW and the
debt floor governs LIQUIDITY IN YEARS OF SPENDING, and the floor is satisfied
throughout -- debt cover stays comfortably above a decade of need the whole
way. Nothing is broken. But nobody setting a one-eighth deployment cap expects
a two-thirds equity book at the horizon, so it is written down here rather
than left to be discovered.

WHY NO CEILING IS ADDED. A holding ceiling means trimming, trimming means
realising gain on the way down, and the s.112A exemption is fixed per year, so
a forced trim in the late years is bought at the full s.112A rate on the
excess plus the loss of whatever the trimmed capital would have compounded to. The model would also
choose the trim points using a projection with NO DRAWDOWN IN IT, which is the
one input that would justify a ceiling in the first place. A deterministic
model cannot price sequence risk, so it must not be the thing that sets the
equity ceiling. That is an owner's judgement, expressed today through the debt
floor -- which is denominated in years of spending and therefore does bite --
and it should stay that way until the model can simulate a bad decade.

IT MAKES 3chunk WORSE ON TAX. Surplus diverted into equity is surplus the debt
purse no longer has for its transitions. Corpus still rises, so this is a
genuine trade rather than a defect.

SIGMA IS NOT MODELLED. EQUITY_SIGMA is carried as a documented constant and
used nowhere. This model is deterministic; a standard deviation cannot do
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

  The fix costs tax where the band binds. Most of that is at or under the
  noise floor; 3chunk at the long horizon is not, and 3chunk is the scenario
  already flagged above.

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
import csv
import datetime
import multiprocessing
import random
import sys
import time

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
PERSONAL_INFLOWS = ((390.0, 1),)
# The HUF receives one or more external inflows. Amounts and years are given
# separately -- --huf_corpus 190/60/40 with --huf_year 5/8/12 -- and are zipped
# into (amount, FY) pairs. Each inflow is spread across the funds the PERSONAL
# purse holds in that year, in the same proportion, so the HUF starts life with
# the same portfolio shape rather than an arbitrary one.
HUF_INFLOWS = ((190.0, 4),)

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
REQUIREMENT_SCHEDULE = ((40.0, 1), (45, 6), (50, 11), (52.5, 16),
                        (55, 21), (57.5, 26), (60, 31))

# ---- the debt return ------------------------------------------------------
# The rate is a property of the FUND, fixed for its life, set by the chunk the
# fund was first chosen for. The portfolio rate then falls as the transitions
# shift the mix -- the blend is an OUTPUT, not an input.
INTEREST_RATES = (0.06, 0.0525)   # one, two or three rates. What each chunk's funds
CHUNK_SPREAD = 0.0025       # earn is resolved by chunk_rates() below.

# ---- income, by head ------------------------------------------------------
# Split so each head carries its own statutory treatment. Only the TAXABLE
# portion matters to the band; the gross is what OTHER_INCOME_STEP grows.
TAXABLE_OTHER_INCOME = 0.50     # interest and the like -- fully taxable
TAXFREE_OTHER_INCOME = 10.50    # PPF, tax-free bonds, return of capital
SALARY_INCOME = 1.35            # taxable less the s.16(ia) standard deduction
RENTAL_INCOME = 1.80            # taxable less the s.24(a) 30% deduction
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
PLAN_TOL = 1e-2             # tolerance the yearly conservation identity must
                            # close to. Everything that leaves a purse is cash
                            # drawn, tax paid, or exit load; anything else is a
                            # leak, and five have now been found by checking
                            # exactly that. tests/test_swp_conservation.py
                            # pins it.

# ---- the equity sleeve (s.112A) --------------------------------------------
# Equity LTCG is a SEPARATE tax head: a flat rate above its own annual
# exemption, and NOT eligible for the s.87A rebate. So it neither consumes the
# slab band nor is sheltered by it -- the exemption is additive capacity on top
# of everything else, once per assessee.
EQUITY_ENABLED = True
EQUITY_RETURN = 0.100       # deterministic CAGR, --equity_cagr
EQUITY_SIGMA = 0.125        # NOT USED -- deterministic model. See docstring.
N_EQ_FUNDS = 20             # eq_f1 .. eq_f20, filled SEQUENTIALLY not equally
EQ_BUCKET_SHARE = 0.05      # each bucket is filled to this share of the excess

# ── DECISION RATES ─────────────────────────────────────────────────────────
# The rates used to SCORE candidates, which need not be the rates the world
# is modelled to deliver. terminal() pays real tax today to buy projected
# growth tomorrow, so the projection's rate is a policy input in its own
# right. Scoring at a conservative floor instead of the modelled rates GAINS
# corpus in the low-return states and gives some back in the high ones, and
# holds lifetime tax roughly flat across the whole range instead of climbing
# with the return. Insurance, with the premium paid in the states where the
# plan is already comfortable. --conservative_sweep prices it for a given
# parameter set; the numbers move with the plan, so they are not quoted here.
#
# LAMBDA blends the two. terminal() is LINEAR in the projection multipliers,
# so blending the multipliers is identical to blending the two scores and
# costs nothing -- no extra candidate evaluations, no change in runtime.
# The conservative rates default to a FLOOR, not to a haircut: whichever is
# LOWER, the modelled rate or the floor below. A plan already modelled at 7%
# equity has nothing to be conservative about, and a haircut would score it at
# 4.5% and start selling equity to buy debt. min() cannot invert.
DECISION_EQUITY_FLOOR = 0.08  # scoring-only equity rate, capped at EQUITY_RETURN
DECISION_DEBT_FLOOR = 0.05    # scoring-only debt rate, capped at chunk 1's rate
DECISION_EQUITY_CAGR = None   # explicit override; None = the floor above
DECISION_DEBT_RATE = None     # explicit override; None = the floor above
CONSERVATIVE_WEIGHT = 0.6     # 0.0 = score purely at the modelled rates,
                              # 1.0 = purely at the conservative ones
                            # over the floor. 20 x 5% = the whole excess.
# ---- turnover -------------------------------------------------------------
# Fraction of the INITIAL fund count moved at each chunk boundary. One entry per
# boundary: 3chunk uses both, 2chunk uses the first. Turnover was previously
# implicit in how many funds each transition emptied -- 7 of 21 at each of
# 3chunk's boundaries, 10 of 21 at 2chunk's. It now drives the fund groups.
N_BASE_FUNDS = 21
TURNOVER = (1.0 / 3.0, 1.0 / 3.0)

PLAN_TO_AGE = 90            # what --dob models to. Not a life expectancy --
                            # a plan that runs out at the median age fails half
                            # the time, so this is deliberately past it.
MAX_HORIZON = 60            # the statutory ladders are projected only as far
                            # as LADDER_PROJECTED_YEARS (computed below, once
                            # the blocks exist) and are frozen at their last
                            # block beyond that; see build_ladders.spread.
MIN_HORIZON = 20            # below this the tier arithmetic stops meaning
                            # anything and the plan is not a retirement plan

# The floor steps DOWN over the horizon in three tiers. The liability shrinks
# as the plan runs, so holding the opening multiple to the end traps surplus
# against a debt that no longer exists. Tier boundaries scale with the horizon:
# the first two are min(10, N//3) years each, the third is the remainder.
#     20yr -> 6 / 6 / 8      25yr -> 8 / 8 / 9
#     30yr -> 10 / 10 / 10   35yr -> 10 / 10 / 15
DEBT_FLOOR_MULTIPLES = (20.0, 20.0, 18.0)

DEBT_FLOOR_X = 20.0         # retained for reference. The combined DEBT corpus
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
                            # A loose floor alone lets equity past half the
                            # corpus and the bill is then almost all SLAB tax
                            # on a starved debt book, while the oversized
                            # sleeve draws no more tax-free cash than a small
                            # one would. Both constraints together, far less.
EQUITY_MIN_HOLD = 1         # years before a lot qualifies as LTCG (s.2(42A))

# per 5-year block, so both can be re-projected like the slab ladders
EQ_EXEMPT_BLOCKS = [1.25, 1.25, 1.50, 1.50, 1.75, 1.75, 2.00]   # s.112A exemption
EQ_RATE_BLOCKS = [0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125]  # s.112A rate

# How far the ladders above are actually projected. Past this the last block
# repeats: allowances stop growing, so the tax is if anything overstated.
LADDER_PROJECTED_YEARS = 5 * min(len(EXEMPT_BLOCKS), len(NILBAND_BLOCKS),
                                 len(EQ_EXEMPT_BLOCKS), len(EQ_RATE_BLOCKS))

# ── SAMPLED RETURN PATHS (Monte Carlo) ─────────────────────────────────────
# When set, these REPLACE the flat assumptions year by year. They affect what
# the world DELIVERS and nothing else: terminal() still scores candidates at
# the flat EQUITY_RETURN and the portfolio debt rate, blended by
# CONSERVATIVE_WEIGHT, because a plan cannot see next year's return. That
# separation is the whole point -- it is what makes the result a test of the
# STRATEGY under uncertainty rather than of clairvoyance.
#
# DEBT_PATH[i] is the realised chunk-1 rate for year i+1; every fund keeps its
# own spread against chunk 1, so a 3chunk book still earns its ladder, shifted.
EQUITY_PATH = None
DEBT_PATH = None

MC_EQUITY_MEAN = 0.12       # Indian equity, long-run CAGR
MC_EQUITY_SIGMA = 0.20      # one sigma either side gives -8% to +32%
MC_DEBT_MEAN = 0.0575       # short and medium debt funds, 4.5% to 7.0%
MC_DEBT_SIGMA = 0.0125      # one sigma either side gives that range
MC_DEFAULT_PATHS = 500      # what a bare --monte_carlo runs. Enough to put a
                            # usable number on the fifth percentile, which is
                            # the only part of the distribution worth having.
MC_DEFAULT_SEED = 4242      # fixed, so two strategies meet identical worlds

LIQUIDATION_YEARS = 1       # how many years the final wind-down is spread
                            # over. 1 is closing on the last day; each extra
                            # year buys a fresh set of annual allowances.

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

Without it the HUF debt is exhausted before the horizon and the last years of
nil band go unused. With it, that capacity is preserved.

OFF BY DEFAULT, because it loses money: turning it on lowers tax and lowers
corpus. Corpus is already NET of tax, so the version with less tax is the worse plan.
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

Measured on 3chunk, it buys a few points of allocation fidelity for real tax,
and still falls far short of the target. Left OFF by default because a partial fix that costs real money is
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
        """Five years per block, padded with the LAST block if the horizon
        outruns the projection.

        The statutory ladders are projected as far as someone was willing to
        project them. Beyond that the honest assumption is that indexation
        STOPS, not that it continues at the same pace -- freezing the last
        block understates the allowances and therefore overstates the tax,
        which is the direction to be wrong in. Extend the *_BLOCKS lists when
        you have a view on the years past the projection.
        """
        out = []
        for k in range(n_blocks):
            out += [blocks[min(k, len(blocks) - 1)]] * 5
        return out[:horizon]

    base = (TAXABLE_OTHER_INCOME + TAXFREE_OTHER_INCOME
            + SALARY_INCOME + RENTAL_INCOME)
    # The s.16(ia) deduction is a NOMINAL statutory allowance, so it is
    # subtracted AFTER the head is scaled, not before. Scaling a
    # post-deduction figure inflates the deduction itself -- at 35 years and
    # OTHER_INCOME_STEP the scale reaches 1.22, quietly turning a 0.75
    # allowance into 0.92. The rental deduction is a PERCENTAGE and is
    # unaffected either way.
    oi, toi = [], []
    for k in range(horizon):
        gross = base + OTHER_INCOME_STEP * k
        scale = gross / base if base > EPS else 0.0
        oi.append(gross)
        toi.append(TAXABLE_OTHER_INCOME * scale
                   + max(0.0, SALARY_INCOME * scale - SALARY_STD_DEDUCTION)
                   + RENTAL_INCOME * scale * (1.0 - RENTAL_DEDUCTION))
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

def settle_equity_tax(bill, e_p, e_h, nav_eq, fy, eq_exempt, eq_rate,
                      g_eqp, g_eqh):
    """Pay the s.112A bill and charge the gain the payment itself realises.

    Selling equity to pay equity tax realises more gain, which is taxable
    once the annual exemption is spent, so the bill and the sale chase each
    other. Three passes close it to well under PLAN_TOL. Returns
    (total tax, cash raised, extra personal gain, extra HUF gain); whatever
    the sleeves cannot cover comes back as an unmet balance for the caller
    to take out of the debt books.
    """
    paid = xp = xh = 0.0
    due = bill
    # Six passes, not three. Three closed a 35-year plan comfortably, but at a
    # 60-year horizon the equity bill is large enough that the residue after
    # three passes reaches 1e-2 -- PLAN_TOL exactly, with no headroom left.
    for _ in range(6):
        want = due - paid
        if want <= EPS:
            break
        c, gn = e_p.sell(nav_eq, fy, amount=want)
        paid += c
        xp += gn
        if due - paid > EPS:
            c, gn = e_h.sell(nav_eq, fy, amount=due - paid)
            paid += c
            xh += gn
        due = (equity_tax(g_eqp + xp, eq_exempt, eq_rate)
               + equity_tax(g_eqh + xh, eq_exempt, eq_rate))
    return due, paid, xp, xh


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


# ── SURCHARGE, s.115BAC(1A) ────────────────────────────────────────────────
# Bands are on TOTAL income; the rate applies to the TAX, before cess. Static,
# not projected forward like the s.87A ceiling: unchanged since 2013, and
# freezing them overstates tax rather than understating it.
SURCHARGE_BANDS = ((50.0, 0.10), (100.0, 0.15), (200.0, 0.25))

# s.111A, s.112, s.112A and dividends carry a hard 15% ceiling even when total
# income is past 200. That is why the 200 boundary is the easy one -- the
# special-rate portion does not change rate across it, so only the slab
# portion jumps. The 50 and 100 boundaries are the awkward ones, because there
# min(rate, 0.15) is not binding and BOTH portions jump.
SPECIAL_SURCHARGE_CAP = 0.15


def surcharge_rate(total_income):
    """Band rate for a total income. Strict >: at exactly 50 the rate is 0."""
    rate = 0.0
    for threshold, band in SURCHARGE_BANDS:
        if total_income > threshold:
            rate = band
    return rate


def _with_surcharge(slab_amt, special_amt, total_income):
    """Tax plus surcharge, before cess and before marginal relief."""
    rate = surcharge_rate(total_income)
    return (slab_amt * (1.0 + rate)
            + special_amt * (1.0 + min(rate, SPECIAL_SURCHARGE_CAP)))


def surcharged(slab_amt, special_amt, slab_income, special_income,
               basic_exemption):
    """Tax plus surcharge WITH marginal relief, before cess.

    At a threshold T: recompute the bill as though total income were exactly
    T -- which puts it in the LOWER band, since the comparison is a strict
    greater-than -- and cap the liability at that plus the excess. Checked at
    every crossed threshold with the lowest cap winning; normally only the
    nearest binds, but taking the minimum costs nothing and cannot be wrong.

    TWO CHOICES ARE ENCODED HERE AND NEITHER IS STATUTORY.

    WHICH INCOME IS SHAVED to bring the total down to T: slab before special.
    That matters, because slab income at the margin is taxed at 30% and s.112A
    at 12.5%, so shaving slab gives a SMALLER threshold bill, a smaller cap,
    and therefore MORE relief. It is the taxpayer-favourable reading and the
    one in the worked examples this was verified against. Shaving
    proportionally, or special first, both yield a larger bill.

    THE SPECIAL-RATE TAX IS SCALED PRO-RATA when special income has to be
    shaved too, which happens only when the excess above T exceeds the whole
    slab income. Exact would mean re-running the exemption against a smaller
    gain; pro-rata is within a rounding of it.

    Both bite only within a few lakh above each threshold. VERIFIED: the two
    published worked examples reproduce to the paisa -- 11.9600 at 50.20 and
    30.5032 at 100.40 when fed their own base slab figures -- the function is
    continuous across all three boundaries with and without special income,
    and monotone over 600,000 sampled incomes to 1200 lakh.
    """
    total = slab_income + special_income
    bill = _with_surcharge(slab_amt, special_amt, total)
    for threshold, _band in SURCHARGE_BANDS:
        if total <= threshold:
            continue
        excess = total - threshold
        kept_slab = max(0.0, slab_income - excess)
        spill = excess - (slab_income - kept_slab)
        kept_special = max(0.0, special_income - spill)
        at_t = _with_surcharge(
            slab_tax(kept_slab, basic_exemption),
            (special_amt * kept_special / special_income
             if special_income > EPS else 0.0),
            threshold)
        bill = min(bill, at_t + excess)
    return bill


def _split_bill(slab_amt, special_amt, slab_income, special_income,
                basic_exemption):
    """Surcharge, marginal relief and cess on the COMBINED liability, then
    split back into (slab part, special part).

    It has to be one calculation. The surcharge RATE is set by total income
    across both heads, the 15% CAP applies only to the special head, and
    marginal relief is computed on the combined figure -- none of the three can
    be evaluated with one head in hand. The split back is pro-rata and is a
    REPORTING choice only: the total is exact either way, but in a
    marginal-relief year the personal and equity lines become an allocation
    rather than a measurement.
    """
    gross = slab_amt + special_amt
    if gross <= EPS:
        return 0.0, 0.0
    total = surcharged(slab_amt, special_amt, slab_income, special_income,
                       basic_exemption) * CESS
    share = slab_amt / gross
    return total * share, total * (1.0 - share)


def personal_tax(slab_income, exempt_ceiling, basic_exemption=BRACKET_WIDTH,
                 special_income=0.0, eq_exemption=0.0, eq_rate=0.0):
    """Slab tax with the s.87A rebate and its marginal relief.

    Two separate thresholds, and they are not the same number: the BASIC
    EXEMPTION anchors the slab ladder, while the s.87A CEILING is the income
    below which the rebate wipes the bill out entirely.

    `special_income` is s.112A gain. It is NOT slab-taxed and never enters
    slab_tax -- but it DOES count toward TOTAL INCOME for the rebate's
    eligibility test, and that asymmetry is the whole point of this argument.
    s.112A gain is excluded from the rebate OFFSET and included in the
    THRESHOLD, so it acts purely as ballast: it can drag total income past the
    ceiling and disqualify the rebate on ordinary income, while never being
    relievable itself. Slab income of 11 with 1.5 of equity gain loses the
    rebate on the whole 11.

    THE ONE PART THAT IS NOT SETTLED. The marginal relief proviso caps the tax
    at the excess of TOTAL income over the ceiling, and total income includes
    the s.112A gain -- so the excess is measured on the full figure here. Any
    reading that measured it on slab income alone would give MORE relief.
    Confirm with a professional before relying on the narrow band just above
    the ceiling; everywhere else the two readings agree.

    Rebate and surcharge can never both apply: the ceiling reaches 20 on the
    projected ladder and the first surcharge threshold is 50.
    """
    return _split_bill(
        _slab_after_rebate(slab_income, exempt_ceiling, basic_exemption,
                           special_income),
        max(0.0, special_income - eq_exemption) * eq_rate,
        slab_income, special_income, basic_exemption)[0]


def _slab_after_rebate(slab_income, exempt_ceiling,
                       basic_exemption=BRACKET_WIDTH, special_income=0.0):
    """Slab tax after the s.87A rebate and its relief, BEFORE surcharge and
    cess. Split out because the surcharge needs it as an input, and because
    the HUF reuses it with a ceiling of zero -- no rebate, same ladder."""
    if slab_income + special_income <= exempt_ceiling:
        return 0.0
    return min(slab_tax(slab_income, basic_exemption),
               slab_income + special_income - exempt_ceiling)


def equity_tax(gain, exemption, rate, slab_income=0.0, exempt_ceiling=0.0,
               basic_exemption=BRACKET_WIDTH):
    """s.112A long-term capital gain on listed equity.

    A flat rate above an annual exemption, per assessee. It is NOT eligible for
    the s.87A rebate, so it neither consumes the debt band nor is wiped out by
    it -- but it DOES share a surcharge computation with the slab head, because
    the band is set by total income and marginal relief is computed on the
    combined bill. Pass the purse's slab income to get that right. At zero it
    degrades to the pre-surcharge behaviour, which is exactly correct whenever
    total income is under 50.
    """
    return _split_bill(
        _slab_after_rebate(slab_income, exempt_ceiling, basic_exemption, gain),
        max(0.0, gain - exemption) * rate,
        slab_income, gain, basic_exemption)[1]


def huf_tax(gain, nil_band, special_income=0.0, eq_exemption=0.0,
            eq_rate=0.0):
    """HUFs get the basic exemption but NOT the s.87A rebate.

    They DO face the same s.115BAC(1A) surcharge on the same bands, on their
    own total income -- which is why the equity terms are here too. The ladder
    is anchored on nil_band, so this is continuous at the threshold.
    """
    return _split_bill(slab_tax(gain, nil_band),
                       max(0.0, special_income - eq_exemption) * eq_rate,
                       gain, special_income, nil_band)[0]


# ═══════════════════════════════════════════════════════════════════════════
#  Fund -- one FIFO lot queue
# ═══════════════════════════════════════════════════════════════════════════

class Fund:
    __slots__ = ("lots", "loss", "total_units")

    def __init__(self):
        self.lots = []                      # [units, purchase_nav, buy_fy]
        # Cumulative exit load charged on this fund. It rides on the FUND, not
        # on a global counter, because the grid searches sell against hundreds
        # of clones per year and only the winning lineage is committed. A
        # clone inherits the running total, so close-minus-open across a
        # committed substitution is exactly the load that really happened.
        self.loss = 0.0
        # units() is called about a MILLION times in a 35-year run -- profiling
        # puts it and value(), which calls it, at over half the runtime. The
        # running total makes it O(1). Every mutation of `lots` must maintain
        # it, which is why lots must never be appended to from outside.
        self.total_units = 0.0

    def clone(self):
        """Shallow-structure deep-value copy. The grid searches clone state
        hundreds of times per run; copy.deepcopy walks the object graph
        reflectively and dominates runtime. Each lot is a 3-element list, so a
        slice copies it fully -- no nested references to chase."""
        f = Fund.__new__(Fund)
        # Most funds in a book are empty most of the time -- 21 funds across
        # up to 8 folios, and only the live ones hold lots -- so the branch
        # pays for itself many times over across a Monte Carlo run.
        f.lots = [lot[:] for lot in self.lots] if self.lots else []
        f.loss = self.loss
        f.total_units = self.total_units
        return f

    def invest(self, amount, nav, fy):
        if amount > EPS:
            self.lots.append([amount / nav, nav, fy])
            self.total_units += amount / nav

    def units(self):
        return self.total_units

    def value(self, nav):
        return self.units() * nav

    def gain(self, nav):
        """Unrealised gain if every lot were sold at nav."""
        if not self.lots:
            return 0.0
        return sum(u * (nav - basis) for u, basis, _ in self.lots)

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
            self.total_units -= take / nav
        self.lots = [l for l in self.lots if l[0] > EPS]
        if not self.lots:
            # Re-anchor on empty. Decrementing accumulates float drift over
            # thirty-five years of partial sales; emptying the fund is the
            # natural place to reset it to a figure that cannot drift.
            self.total_units = 0.0
        self.loss += load
        return gross, gain, load


# ═══════════════════════════════════════════════════════════════════════════
#  Folio -- N funds, plus a rotation pointer for incoming money
# ═══════════════════════════════════════════════════════════════════════════

class Folio:
    def __init__(self, n_funds):
        self.funds = [Fund() for _ in range(n_funds)]
        self.ptr = 0

    def clone(self):
        fo = Folio.__new__(Folio)
        fo.funds = [f.clone() for f in self.funds]
        fo.ptr = self.ptr
        return fo

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

    def clone(self):
        s = Sleeve.__new__(Sleeve)
        s.funds = [f.clone() for f in self.funds]
        s.ptr = self.ptr
        s.in_current = self.in_current
        return s

    def loss(self):
        """Exit load charged across this sleeve's whole lineage."""
        return sum(f.loss for f in self.funds)

    def gain(self, nav):
        """Unrealised gain in the whole sleeve at nav."""
        return sum(f.gain(nav) for f in self.funds)

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
        if left > EPS:
            # EVERY rupee must land somewhere. bucket_target caps how much a
            # bucket takes in a cycle, so a large deployment can exhaust one
            # full cycle with money still in hand; dropping it destroys value.
            share = left / n
            for f in self.funds:
                f.invest(share, nav, fy)
            # in_current tracks how much has gone into the bucket at ptr THIS
            # cycle; the spread above put `share` into that bucket too, so
            # leaving it stale overstates the room left there next time.
            self.in_current = min(bucket_target, self.in_current + share)

    def sell(self, nav, fy, amount=None, gain_cap=None, dearest=False):
        """LTCG-qualified lots only.

        Cheapest bucket first when raising CASH -- least gain per rupee. Dearest
        first when HARVESTING, where the object is the opposite: extinguish as
        much gain as the exemption allows per rupee churned.
        """
        cutoff = fy - EQUITY_MIN_HOLD
        # Empty funds report front_frac == +inf, which reverse=dearest would
        # otherwise sort to the FRONT of the harvest queue. Mask them to the
        # back in both directions so a real high-gain bucket is always reached
        # first. Purely an ordering nicety -- Fund.sell no-ops on empties, so
        # results are unchanged; this just skips dead work.
        order = sorted(self.funds,
                       key=lambda f: (f.front_frac(nav) if f.units() > EPS
                                      else (-1.0 if dearest else float("inf"))),
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
        for cash, which over a long horizon leaves most of the cumulative
        exemption simply forfeited.

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
        probe = self.clone()
        return probe.sell(nav, fy, gain_cap=gain_cap)[0]


# ═══════════════════════════════════════════════════════════════════════════
#  Book -- one purse: an ordered list of folios
# ═══════════════════════════════════════════════════════════════════════════

class Book:
    def __init__(self, cap, n_funds):
        self.cap, self.n_funds = cap, n_funds
        self.folios = [Folio(n_funds)]
        self.receiver = 0

    def clone(self):
        b = Book.__new__(Book)
        b.cap, b.n_funds = self.cap, self.n_funds
        b.folios = [fo.clone() for fo in self.folios]
        b.receiver = self.receiver
        return b

    def loss(self):
        """Exit load charged across this book's whole lineage."""
        return sum(f.loss for fo in self.folios for f in fo.funds)

    def gain(self, navs):
        """Unrealised gain across every folio and fund at navs."""
        return sum(f.gain(navs[k])
                   for fo in self.folios for k, f in enumerate(fo.funds))

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
        # PROBE HIGH-GAIN-FIRST FIRST, and the ordering is not arbitrary. For
        # a given amount of cash, high_gain_first realises the MOST gain any
        # ordering can produce and low_gain_first the least. So if the whole
        # need can be taken high-gain-first and still fits the band, two things
        # follow at once and neither needs testing: the low-gain draw would
        # also fit, and the bisection's answer is `need` exactly. That collapses
        # two probes and twenty halvings into a single probe. Only when the
        # high-gain draw BREACHES the band is the cheap ordering worth
        # measuring, because only then is it in question whether ANY ordering
        # fits.
        _fp = self.clone()
        _fa, _fga, _ = _fp.draw(navs, fy, folio_idxs=lv, amount=need,
                                prefer=prefer, high_gain_first=True,
                                protect=protect)
        if _fa >= need - 1e-6 and _fga <= band + 1e-9:
            _feasible, lo, hi, best = True, need, need, need
        else:
            probe = self.clone()
            got, gmin, _ = probe.draw(navs, fy, folio_idxs=lv, amount=need,
                                      prefer=prefer, high_gain_first=False,
                                      protect=protect)
            _feasible = got >= need - 1e-6 and gmin <= band + 1e-9
            lo, hi, best = 0.0, need, 0.0

        if _feasible:
            # 20 halvings of a need of ~50 resolves to 5e-5, five thousand
            # times finer than PLAN_TOL. 35 was 1.5e-9 and cost 30% of the
            # runtime for precision nothing downstream can use; verified
            # byte-identical output on every scenario and horizon.
            for _ in range(20 if best < need else 0):
                mid = (lo + hi) / 2
                trial = self.clone()
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

def pay_external_bill(book, bill, base_gain, navs, fy,
                      exempt_ceiling=0.0, taxable_other=0.0,
                      basic_exemption=BRACKET_WIDTH, special_income=0.0,
                      eq_exemption=0.0, eq_rate=0.0):
    """Sell debt units to pay a bill that is NOT slab tax -- an s.112A bill the
    sleeves could not cover.

    pay_tax_bill cannot be used for this and using it was a real defect. That
    function converges on `new_tax - total_tax_paid`, where new_tax is the
    year's WHOLE personal slab bill. When the amount handed to it is a slab
    bill those two are the same quantity, so the loop is sound. When it is an
    equity bill they are different tax heads, and the first pass leaves
    `current_tax_needed` equal to the entire slab bill -- which the caller has
    already settled -- so the book is drained a second time for it. Measured,
    a residual bill of a few millionths of a rupee could drag well over a lakh
    of debt out behind it. Conservation did not catch it, and could not: the
    over-draw is reported as tax, so the identity still closes. It is tax
    charged for no reason, not value lost.

    Here the target is the bill PLUS only the incremental slab tax the debt
    sales themselves create, measured against the slab tax already standing at
    base_gain. Returns (cash drawn, gain realised).
    """
    if bill <= EPS or book.value(navs) <= EPS:
        return 0.0, 0.0
    base_slab = personal_tax(taxable_other + base_gain, exempt_ceiling,
                             basic_exemption, special_income,
                             eq_exemption, eq_rate)
    drawn_tot = gain_tot = 0.0
    want = bill
    # Forty for the same reason as pay_tax_bill: the residual shrinks by the
    # marginal rate, which the surcharge pushes past two thirds.
    for _ in range(40):
        if want <= EPS:
            break
        drawn, add_gain, _ = book.draw(navs, fy, amount=want,
                                       high_gain_first=False)
        if drawn <= EPS:
            break
        drawn_tot += drawn
        gain_tot += add_gain
        inc_slab = max(0.0, personal_tax(taxable_other + base_gain + gain_tot,
                                         exempt_ceiling, basic_exemption,
                                         special_income, eq_exemption,
                                         eq_rate)
                       - base_slab)
        want = max(0.0, bill + inc_slab - drawn_tot)
    return drawn_tot, gain_tot


def pay_tax_bill(book, initial_tax, base_gain, navs, fy, is_huf=False,
                 nil_band=0.0, exempt_ceiling=0.0, taxable_other=0.0,
                 basic_exemption=BRACKET_WIDTH, special_income=0.0,
                 eq_exemption=0.0, eq_rate=0.0):
    """Sell units to pay `initial_tax`, converging on the tax-on-tax circularity.

    `base_gain` is the gain the year has ALREADY realised. It must be passed in:
    an earlier version recomputed the bill from the tax-sale gain alone, so the
    loop exited after one pass and real tax was recorded but never removed
    from the corpus. Conservation now holds to the 10-iteration tolerance
    rather than leaking.
    """
    if initial_tax <= EPS or book.value(navs) <= EPS:
        return 0.0, 0.0

    current_tax_needed = initial_tax
    total_tax_paid = 0.0
    total_tax_gain = 0.0

    # FORTY, not ten. The loop shrinks the outstanding bill by the MARGINAL
    # tax rate each pass, so ten iterations is only enough while that rate is
    # modest. Measured in the 60-year stress case the ratio is about 0.68 --
    # a high gain fraction meeting the s.87A claw-back and then the surcharge
    # -- which leaves 0.68^10, roughly 2%, of a 7.5 bill outstanding: 0.028 of
    # tax reported and never drawn. Forty passes take the same ratio to 3e-7.
    # The break makes the extra passes free in every ordinary year.
    for _ in range(40):
        if current_tax_needed <= EPS:
            break
        drawn_cash, add_gain, _ = book.draw(navs, fy, amount=current_tax_needed, high_gain_first=False)
        total_tax_paid += drawn_cash
        total_tax_gain += add_gain

        total_realized_gain = base_gain + total_tax_gain
        # The bill this loop converges on must be the SAME bill the caller
        # reports, or the gap is tax recorded and never drawn. It was not:
        # special_income was accepted by the signature and then dropped, and
        # once the surcharge landed the two diverged by the whole surcharge.
        # FY52 of the 60-year stress case reported 0.028 that never left.
        if is_huf:
            new_tax = huf_tax(total_realized_gain, nil_band, special_income,
                              eq_exemption, eq_rate)
        else:
            new_tax = personal_tax(taxable_other + total_realized_gain,
                                   exempt_ceiling, basic_exemption,
                                   special_income, eq_exemption, eq_rate)

        current_tax_needed = max(0.0, new_tax - total_tax_paid)

    return total_tax_paid, total_tax_gain


# ═══════════════════════════════════════════════════════════════════════════
#  The simulation
# ═══════════════════════════════════════════════════════════════════════════

def simulate(scenario="1chunk", horizon=HORIZON, cap=MAX_FOLIOS,
             vintage_every=VINTAGE_EVERY, no_vintage=False, verbose=False):
    if horizon > MAX_HORIZON:
        raise ValueError(
            f"horizon {horizon} is above MAX_HORIZON ({MAX_HORIZON}).")
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
        _base_r = chunk_rates(1)[0]
        _shift = (DEBT_PATH[i] - _base_r) if DEBT_PATH else 0.0
        for k in range(nf):
            navs[k] *= (1.0 + spec["rate"](k) + _shift)
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
            nav_eq *= (1.0 + (EQUITY_PATH[i] if EQUITY_PATH
                              else EQUITY_RETURN))
        eq_exempt, eq_rate = lad["eq_exempt"][i], lad["eq_rate"][i]
        # OPEN OF YEAR: after growth and inflows, before anything is sold.
        # The identity closed against this is the only check that sees value
        # VANISHING rather than merely being misreported, which is how the
        # sleeve leak, the unpaid s.112A bill and the unpaid bill on an
        # exhausted book were all found.
        _v_open = (personal.value(navs) + huf.value(hnavs)
                   + eq_p.value(nav_eq) + eq_h.value(nav_eq))
        _l_open = (personal.loss() + huf.loss()
                   + eq_p.loss() + eq_h.loss())

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
            probe = huf.clone()
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

        def run_year(huf_offer, eq_use=0.0):
            """Play the year out on copies, for a given HUF and equity draw.

            `eq_use` is cash taken from the equity sleeves. Equity is a LAST
            RESORT, not a habit: it compounds at 9% against debt's 7%, so
            spending it costs corpus. The caller engages it only when the
            debt-only answer would pay tax.

            Returns everything needed to commit the winner, so the chosen split
            is executed exactly once rather than replayed.
            """
            p2, h2 = personal.clone(), huf.clone()
            e2p, e2h = eq_p.clone(), eq_h.clone()
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

            # ---- LAST RESORT: the debt books could not fund the year -------
            # Equity is normally drawn only through the EQ_GRID search, which
            # runs only when there is tax to save. Once BOTH debt books are
            # empty there is no tax, so that search never fires -- and before
            # this the year was simply left UNFUNDED while the sleeve went on
            # compounding. Silent, and it flattered the corpus enormously: at
            # a 50-year horizon the plan stopped paying the requirement
            # altogether from FY42 and still reported a growing corpus.
            # The draw is uncapped by the s.112A exemption because it is
            # forced; the gain flows into eq_gain_p / eq_gain_h and is taxed
            # and settled by the machinery already below.
            _unfunded = max(0.0, need_left - cash_p2 - cash_h2)
            if _unfunded > EPS and EQUITY_ENABLED:
                _c, _gn = e2p.sell(nav_eq, fy, amount=_unfunded)
                eq_cash += _c
                eq_gain_p += _gn
                _unfunded -= _c
                if _unfunded > EPS:
                    _c, _gn = e2h.sell(nav_eq, fy, amount=_unfunded)
                    eq_cash += _c
                    eq_gain_h += _gn

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
            txp0 = personal_tax(toi + gpt, exempt_c, band_h, eq_gain_p,
                                eq_exempt, eq_rate)
            gain_h2 += gain_turn_h
            txh0 = huf_tax(gain_h2, band_h, eq_gain_h, eq_exempt, eq_rate)
            _dp, egp = pay_tax_bill(p2, txp0, gpt, navs, fy, is_huf=False,
                                    exempt_ceiling=exempt_c, taxable_other=toi,
                                    basic_exemption=band_h,
                                    special_income=eq_gain_p,
                                    eq_exemption=eq_exempt, eq_rate=eq_rate)
            _dh, egh = pay_tax_bill(h2, txh0, gain_h2, hnavs, fy, is_huf=True,
                                    nil_band=band_h,
                                    special_income=eq_gain_h,
                                    eq_exemption=eq_exempt, eq_rate=eq_rate)
            txp = personal_tax(toi + gpt + egp, exempt_c, band_h, eq_gain_p,
                                eq_exempt, eq_rate)
            txh = huf_tax(gain_h2 + egh, band_h, eq_gain_h,
                          eq_exempt, eq_rate)
            # A bill charged against an EXHAUSTED book was reported and never
            # taken out of the corpus: pay_tax_bill returns (0, 0) the moment
            # book.value is zero. Measured, a purse that empties late in the
            # plan logged slab tax that never left the corpus. The sleeve
            # settles the balance, and the gain that raises feeds the s.112A
            # computation immediately below.
            _sp = max(0.0, txp - _dp)
            if _sp > EPS and e2p.value(nav_eq) > EPS:
                _c, _gn = e2p.sell(nav_eq, fy, amount=_sp)
                eq_gain_p += _gn
            _sh = max(0.0, txh - _dh)
            if _sh > EPS and e2h.value(nav_eq) > EPS:
                _c, _gn = e2h.sell(nav_eq, fy, amount=_sh)
                eq_gain_h += _gn
            txe = (equity_tax(eq_gain_p, eq_exempt, eq_rate,
                              toi + gpt, exempt_c, band_h)
                   + equity_tax(eq_gain_h, eq_exempt, eq_rate,
                                gain_h2, 0.0, band_h))
            if txe > EPS:
                txe, _pd, _xp, _xh = settle_equity_tax(
                    txe, e2p, e2h, nav_eq, fy, eq_exempt, eq_rate,
                    eq_gain_p, eq_gain_h)
                eq_gain_p += _xp
                eq_gain_h += _xh
                # TAX_EPS, not EPS. EPS is 1e-12, and a settlement that
                # lands a hundredth of a paisa short is float dust, not an
                # unpaid bill -- one such residue of 0.000006 used to drag
                # 1.7750 of debt out of the book behind it.
                if txe - _pd > TAX_EPS:
                    # The debt sale that settles an equity bill realises DEBT
                    # gain, which is slab-taxable, so the draw must cover the
                    # bill AND the slab tax the draw itself creates -- but
                    # nothing more. See pay_external_bill for why pay_tax_bill
                    # is the wrong instrument here.
                    _d2, _e2 = pay_external_bill(
                        p2, txe - _pd, gpt, navs, fy,
                        exempt_ceiling=exempt_c, taxable_other=toi,
                        basic_exemption=band_h, special_income=eq_gain_p,
                        eq_exemption=eq_exempt, eq_rate=eq_rate)
                    gpt += _e2
                    txp = personal_tax(toi + gpt, exempt_c, band_h,
                                       eq_gain_p)
            return dict(tax=txp + txh + txe, p=p2, h=h2, ep=e2p, eh=e2h,
                        sold=sold2, cash_p=cash_p2 + eq_cash, cash_h=cash_h2,
                        surplus=surp2, gain_p=gpt + egp, gain_h=gain_h2 + egh,
                        txp=txp, txh=txh, txe=txe, eq_cash=eq_cash,
                        eq_gain=eq_gain_p + eq_gain_h,
                        eq_gain_p=eq_gain_p, eq_gain_h=eq_gain_h)

        # The ration-implied split first. It is free and usually optimal.
        _yrs_left = max(0, horizon - fy)
        # The PORTFOLIO's debt rate, value-weighted across what is actually
        # held, not chunk 1's. chunk_rates(1)[0] is the ONE-chunk resolution
        # of --interest_rate, so in 2chunk it read 6.00% while half the book
        # earned 5.25%, and in 3chunk it read the top of the spread against a
        # book paying down to 4.50%. It has three consumers -- the terminal
        # projection, the cap on the conservative leg of the scoring blend,
        # and the annuity factor in the spillover reserve -- and all three
        # were reading a rate the plan does not earn.
        # Flat over BOTH books. The previous form walked personal's folios and
        # reached into huf at the same index, which silently dropped any HUF
        # folio beyond personal's count. That cannot happen while the two open
        # folios on the same schedule, but they do NOT in the on-drain vintage
        # mode, where each book opens when it is itself drawn. Cheap insurance
        # against a rate weighted on part of the portfolio.
        _dw, _drt = [], []
        for _bk, _nvs in ((personal, navs), (huf, hnavs)):
            for _fo in _bk.folios:
                for _k, _f in enumerate(_fo.funds):
                    _v = _f.value(_nvs[_k])
                    if _v > EPS:
                        _dw.append(_v)
                        _drt.append(spec["rate"](_k))
        _dtot = sum(_dw)
        _dr = (sum(v * r for v, r in zip(_dw, _drt)) / _dtot if _dtot > EPS
               else chunk_rates(1)[0])
        _rd_opt = (1.0 + _dr) ** _yrs_left
        _re_opt = (1.0 + EQUITY_RETURN) ** _yrs_left
        _dcr = (min(_dr, DECISION_DEBT_FLOOR) if DECISION_DEBT_RATE is None
                else DECISION_DEBT_RATE)
        _ecr = (min(EQUITY_RETURN, DECISION_EQUITY_FLOOR)
                if DECISION_EQUITY_CAGR is None else DECISION_EQUITY_CAGR)
        _rd_con = (1.0 + _dcr) ** _yrs_left
        _re_con = (1.0 + _ecr) ** _yrs_left
        _lam = 1.0 - min(1.0, max(0.0, CONSERVATIVE_WEIGHT))
        _rd = _lam * _rd_opt + (1.0 - _lam) * _rd_con
        _re = _lam * _re_opt + (1.0 - _lam) * _re_con

        def terminal(c):
            """Each purse projected to the end of the plan at its own rate.

            EVERY candidate search compares on this, not on tax. A tax-only
            comparison will spend equity compounding at EQUITY_RETURN to avoid
            a smaller bill on debt compounding at the chunk rate, which is a
            bad trade whenever the growth differential over the remaining
            horizon exceeds the tax saved. Corpus is already net of tax, so
            terminal value is the score and tax is only one term in it.
            """
            return ((c["p"].value(navs) + c["h"].value(hnavs)) * _rd
                    + (c["ep"].value(nav_eq) + c["eh"].value(nav_eq)) * _re)

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
                r = (personal_tax(toi + g_p + d, exempt_c, band_h, g_eqp)
                     - personal_tax(toi + g_p, exempt_c, band_h, g_eqp)) / d
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

            This draws each source's FREE capacity -- capped at what the
            year still needs, never more -- then water-fills the remaining
            shortfall one slice at a time, always from whichever source is
            cheapest per rupee of cash AT THAT MOMENT. Ties go to the personal
            purse. Whatever is left of the PERSONAL band afterwards is
            harvested and put straight back, so the band is not wasted and no
            rupee leaves the plan. The HUF band is deliberately left alone;
            see the module docstring for the measurement that decided it.
            """
            p2, h2 = personal.clone(), huf.clone()
            e2p, e2h = eq_p.clone(), eq_h.clone()

            # Cash is tracked PER PURSE, not as one total, so the reported
            # cash_p / cash_h split stays real. Lumping it into cash_p once
            # made the table show the HUF contributing nothing while its debt
            # book visibly drained.
            cash_p = cash_h = eq_cash = 0.0
            g_p = g_h = g_eqp = g_eqh = 0.0
            # Draw each source's FREE (tax-free) capacity to fund `need`,
            # cheapest first -- but never MORE than the year needs. The amount
            # cap is the point: an UNCAPPED free draw raised every source's
            # full tax-free capacity, routinely 8-16 ABOVE need (equity alone
            # ~5), and because run_waterfill reinvests nothing (surplus is 0)
            # that excess was liquidated straight out of the corpus and simply
            # lost. The leak sank this candidate's terminal value below the
            # plain path in exactly the late years where it drove tax to zero,
            # so the whole water-filling branch could never win. Capping each
            # draw at the running shortfall keeps spare capacity invested and
            # makes the terminal comparison fair. When free capacity is short
            # of need -- the tax-paying case the water-fill loop below exists
            # for -- the gain_cap binds first and the amount cap is inert, so
            # this changes nothing there. Debt is tapped before equity so the
            # higher-compounding sleeve is spent last.
            c, gg, _ = p2.draw(navs, fy, amount=need, gain_cap=band_left)
            cash_p += c; g_p += gg
            if h2.value(hnavs) > EPS:
                # Net off the HUF's transition gain, exactly as band_left does
                # for the personal purse. Passing band_h raw let the free draw
                # overshoot the nil band in transition years.
                band_h_left = max(0.0, band_h - gain_turn_h)
                c, gg, _ = h2.draw(hnavs, fy,
                                   amount=max(0.0, need - (cash_p + cash_h)),
                                   gain_cap=band_h_left)
                cash_h += c; g_h += gg
            if EQUITY_ENABLED:
                c, gg = e2p.sell(nav_eq, fy,
                                 amount=max(0.0, need - (cash_p + cash_h)),
                                 gain_cap=eq_exempt)
                cash_p += c; eq_cash += c; g_eqp += gg
                c, gg = e2h.sell(nav_eq, fy,
                                 amount=max(0.0, need - (cash_p + cash_h)),
                                 gain_cap=eq_exempt)
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

            # ---- residual personal free-band harvest, reinvested -------
            # The draws above stop at `need`, so whatever is left of the
            # personal band would otherwise expire unused. `run_year` spends
            # that residue on a basis reset and routes the proceeds back
            # through the debt floor into equity; this does the same, so the
            # two candidates are compared on equal footing. Nothing leaves the
            # plan: every rupee raised here is put straight back. min_gain_frac
            # skips freshly seeded lots exactly as Book.fund_year does, so this
            # is a reset and not churn. In a year that reached the water-fill
            # loop the free draw has normally spent the band already and
            # _rem_p is zero, which is why this moves so little.
            _surp_p = 0.0
            _rem_p = max(0.0, band_left - g_p)
            if _rem_p > EPS and p2.value(navs) > EPS:
                _pr, _pg, _ = p2.draw(navs, fy, gain_cap=_rem_p,
                                      high_gain_first=True, min_gain_frac=0.01)
                _surp_p, g_p = _pr, g_p + _pg
            if _surp_p > EPS:
                # Surplus routing, identical in shape to run_year: rebuild the
                # debt floor first, then equity, then anything equity declines
                # returns to debt. EVERY rupee must land somewhere. Note the
                # name -- `short` is already the water-fill shortfall here.
                _floor_short = max(0.0, _floor_x * need
                                   - (p2.value(navs) + h2.value(hnavs)))
                _to_eq_p = _surp_p - min(_surp_p, _floor_short)
                if EQUITY_ENABLED:
                    _room_p = max(0.0, EQUITY_MAX_SHARE
                                  * (p2.value(navs) + e2p.value(nav_eq))
                                  - e2p.value(nav_eq))
                    _to_eq_p = min(_to_eq_p, _room_p)
                else:
                    _to_eq_p = 0.0
                if _to_eq_p > EPS:
                    e2p.invest(_to_eq_p, nav_eq, fy, bucket_target)
                p2.put(_surp_p - _to_eq_p, navs, fy,
                       _dest(p2, navs, fy, eligible))

            gpt = g_p + gain_turn
            ght = g_h + gain_turn_h
            txp0 = personal_tax(toi + gpt, exempt_c, band_h, g_eqp,
                                eq_exempt, eq_rate)
            txh0 = huf_tax(ght, band_h, g_eqh, eq_exempt, eq_rate)
            # The tax must come OUT of the books, exactly as the plain path
            # does it. Reporting a bill without paying it is the conservation
            # leak that test_surplus_is_never_discarded exists to catch.
            _dp, egp = pay_tax_bill(p2, txp0, gpt, navs, fy, is_huf=False,
                                    exempt_ceiling=exempt_c, taxable_other=toi,
                                    basic_exemption=band_h,
                                    special_income=g_eqp,
                                    eq_exemption=eq_exempt, eq_rate=eq_rate)
            _dh, egh = pay_tax_bill(h2, txh0, ght, hnavs, fy, is_huf=True,
                                    nil_band=band_h, special_income=g_eqh,
                                    eq_exemption=eq_exempt, eq_rate=eq_rate)
            gpt += egp
            ght += egh
            txp = personal_tax(toi + gpt, exempt_c, band_h, g_eqp,
                                eq_exempt, eq_rate)
            txh = huf_tax(ght, band_h)
            # A bill charged against an EXHAUSTED book was reported and never
            # taken out of the corpus: pay_tax_bill returns (0, 0) the moment
            # book.value is zero. Measured, a purse that empties late in the
            # plan logged slab tax that never left the corpus. The sleeve
            # settles the balance, and the gain that raises feeds the s.112A
            # computation immediately below.
            _sp = max(0.0, txp - _dp)
            if _sp > EPS and e2p.value(nav_eq) > EPS:
                _c, _gn = e2p.sell(nav_eq, fy, amount=_sp)
                g_eqp += _gn
            _sh = max(0.0, txh - _dh)
            if _sh > EPS and e2h.value(nav_eq) > EPS:
                _c, _gn = e2h.sell(nav_eq, fy, amount=_sh)
                g_eqh += _gn
            txe = (equity_tax(g_eqp, eq_exempt, eq_rate,
                              toi + gpt, exempt_c, band_h)
                   + equity_tax(g_eqh, eq_exempt, eq_rate,
                                ght, 0.0, band_h))
            if txe > EPS:
                txe, _pd, _xp, _xh = settle_equity_tax(
                    txe, e2p, e2h, nav_eq, fy, eq_exempt, eq_rate,
                    g_eqp, g_eqh)
                g_eqp += _xp
                g_eqh += _xh
                # TAX_EPS, not EPS. EPS is 1e-12, and a settlement that
                # lands a hundredth of a paisa short is float dust, not an
                # unpaid bill -- one such residue of 0.000006 used to drag
                # 1.7750 of debt out of the book behind it.
                if txe - _pd > TAX_EPS:
                    # The debt sale that settles an equity bill realises DEBT
                    # gain, which is slab-taxable, so the draw must cover the
                    # bill AND the slab tax the draw itself creates -- but
                    # nothing more. See pay_external_bill for why pay_tax_bill
                    # is the wrong instrument here.
                    _d2, _e2 = pay_external_bill(
                        p2, txe - _pd, gpt, navs, fy,
                        exempt_ceiling=exempt_c, taxable_other=toi,
                        basic_exemption=band_h, special_income=g_eqp,
                        eq_exemption=eq_exempt, eq_rate=eq_rate)
                    gpt += _e2
                    txp = personal_tax(toi + gpt, exempt_c, band_h, g_eqp,
                                eq_exempt, eq_rate)
            return dict(tax=txp + txh + txe, p=p2, h=h2, ep=e2p, eh=e2h,
                        sold={}, cash_p=cash_p, cash_h=cash_h,
                        surplus=_surp_p,
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
            # 50% gain fraction it holds back twice what is needed. That
            # reserved more than the HUF was worth, so spill_max was zero and
            # the spillover search never ran at all for most of the horizon.
            _hf = 1.0
            _hpool = [(k, f) for fo in huf.folios
                      for k, f in enumerate(fo.funds) if f.units() > EPS]
            if _hpool:
                _hw = sum(f.value(hnavs[k]) for k, f in _hpool)
                _hf = (sum(f.value(hnavs[k]) * f.front_frac(hnavs[k])
                           for k, f in _hpool) / _hw) if _hw > EPS else 1.0
                _hf = max(0.05, min(1.0, _hf))
            # The reserve is the value needed TODAY to keep realising band_h
            # of gain every remaining year -- so it is an annuity present
            # value, not a plain sum. The plain sum ignored that the HUF goes
            # on compounding while it is drawn, and over-reserved by roughly
            # 1/annuity-factor -- demanding more than the purse held, so
            # spill_max was zero and the spillover search did not run at all
            # across most of the middle of the plan. Fixing
            # it can remove the personal slab bill outright in the years the
            # HUF still has room.
            _n = max(0, horizon - fy)
            _r = _dr
            _ann = ((1.0 - (1.0 + _r) ** -_n) / _r) if _n > 0 and _r > EPS else _n
            reserve = max(0.0, _ann * band_h / _hf)
            spill_max = min(need, max(0.0, huf.value(hnavs) - reserve))
            if spill_max > offer + EPS:
                for j in range(1, SPILL_GRID + 1):
                    x = offer + (spill_max - offer) * j / SPILL_GRID
                    alt = run_year(x)
                    if terminal(alt) > terminal(best) + TAX_EPS:
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
                    if terminal(alt) > terminal(best) + TAX_EPS:
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
            if terminal(alt) > terminal(best) + TAX_EPS:
                best = alt

        personal, huf = best["p"], best["h"]
        eq_p, eq_h = best["ep"], best["eh"]

        # ---- spend whatever s.112A exemption the year did not use ----------
        eq_harv_gain = 0.0
        _harv = {"p": 0.0, "h": 0.0}
        if EQUITY_ENABLED:
            for _tag, _sl, _used in (("p", eq_p, best.get("eq_gain_p", 0.0)),
                                     ("h", eq_h, best.get("eq_gain_h", 0.0))):
                _left = max(0.0, eq_exempt - _used)
                if _left > EPS and _sl.value(nav_eq) > EPS:
                    _g, _gn = _sl.harvest(nav_eq, fy, _left, bucket_target)
                    eq_harv_gain += _gn
                    _harv[_tag] += _gn
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

        _v_close = (personal.value(navs) + huf.value(hnavs)
                    + eq_p.value(nav_eq) + eq_h.value(nav_eq))
        _year_load = (personal.loss() + huf.loss()
                      + eq_p.loss() + eq_h.loss()) - _l_open
        rows.append(dict(
            fy=fy,
            value_open=_v_open,
            value_close=_v_close,
            exit_load=_year_load,
            # The trailing + 0.0 canonicalises negative zero. Without it the
            # column prints -0.0 on some paths and 0.0 on others for the same
            # arithmetic, which breaks byte-for-byte snapshot comparison for
            # no reason at all.
            residual=(_v_open - cash_p - cash_h
                      - (tax_p_final + tax_h_final + best.get("txe", 0.0))
                      - _year_load - _v_close) + 0.0,
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

    # ---- what it would cost to close the plan --------------------------
    # The horizon is an arbitrary stopping point, and stopping there hides the
    # tax embedded in whatever is still held -- most of it in the equity
    # sleeve, whose whole advantage during the plan was that s.112A was never
    # forced. This charges the WHOLE remaining corpus as one realisation in
    # the final year, ON TOP of what that year already realised, at that
    # year's bands and other income. It is the marginal cost of closing on the
    # last day, not a separate tax year: the annual allowances are already
    # part-spent, which is exactly why liquidating in one go is expensive.
    # LIQUIDATION_YEARS spreads it. One year is the marginal cost of closing on
    # the last day. More years is a WIND-DOWN: each further year brings a fresh
    # s.87A ceiling, a fresh HUF nil band and a fresh s.112A exemption, and
    # whatever has not been sold yet goes on earning. Nothing is spent during
    # the wind-down -- the requirement ladder ends at the horizon -- so this
    # measures the cost of turning the corpus into cash and nothing else.
    #
    # The slices are equal in VALUE, not in units: each year sells what is left
    # divided by the years remaining, so the book empties exactly on the last
    # one. Gain per slice is therefore not uniform, because FIFO takes the
    # oldest and highest-gain lots first -- which is the honest ordering and
    # front-loads the bill.
    _ln = max(1, LIQUIDATION_YEARS)
    _lad2 = build_ladders(horizon + _ln - 1) if _ln > 1 else lad
    _wp, _wh = personal.clone(), huf.clone()
    _wep, _weh = eq_p.clone(), eq_h.clone()
    _wnv = list(navs)
    _wne = nav_eq
    _shift0 = (DEBT_PATH[i] - chunk_rates(1)[0]) if DEBT_PATH else 0.0
    liq_p = liq_h = liq_e = 0.0
    _lq_gain = _lq_cash = 0.0
    for _wk in range(_ln):
        _wi = i + _wk
        if _wk:
            # a year passes; whatever is still held keeps earning. The sampled
            # path has run out by definition, so the wind-down uses the flat
            # assumption with the final year's spread preserved.
            for _wf in range(nf):
                _wnv[_wf] *= (1.0 + spec["rate"](_wf) + _shift0)
            if EQUITY_ENABLED:
                _wne *= (1.0 + EQUITY_RETURN)
        _rem = _ln - _wk
        _wtoi = _lad2["taxable_other"][_wi]
        _wexc, _wnb = _lad2["exempt"][_wi], _lad2["nilband"][_wi]
        _wee, _wer = _lad2["eq_exempt"][_wi], _lad2["eq_rate"][_wi]
        _amt = None if _rem == 1 else _wp.value(_wnv) / _rem
        _cp, _gp, _ = _wp.draw(_wnv, fy + _wk, amount=_amt)
        _amt = None if _rem == 1 else _wh.value(_wnv) / _rem
        _ch, _gh, _ = _wh.draw(_wnv, fy + _wk, amount=_amt)
        _gep = _geh = _cep = _ceh = 0.0
        if EQUITY_ENABLED:
            _amt = None if _rem == 1 else _wep.value(_wne) / _rem
            _cep, _gep = _wep.sell(_wne, fy + _wk, amount=_amt)
            _amt = None if _rem == 1 else _weh.value(_wne) / _rem
            _ceh, _geh = _weh.sell(_wne, fy + _wk, amount=_amt)
        _lq_cash += _cp + _ch + _cep + _ceh
        _lq_gain += _gp + _gh + _gep + _geh
        if _wk == 0:
            # The first slice lands in the final PLAN year, so it is charged on
            # top of what that year already realised: the allowances are
            # part-spent and only the increment is attributable to closing.
            _egp0 = best.get("eq_gain_p", 0.0) + _harv["p"]
            _egh0 = best.get("eq_gain_h", 0.0) + _harv["h"]
            # Each purse's surcharge band is set by ITS OWN total income --
            # slab plus s.112A -- so both heads have to be handed to both
            # calls. This is the year the bands actually bite: annual income
            # never approaches 50, and a one-day liquidation clears 200.
            _pslab = toi + gain_p_total + tax_p_extra_gain + _gp
            _hslab = gain_h + tax_h_extra_gain + _gh
            liq_p += max(0.0, personal_tax(_pslab, exempt_c, band_h,
                                           _egp0 + _gep, eq_exempt, eq_rate)
                         - tax_p_final)
            liq_h += max(0.0, huf_tax(_hslab, band_h, _egh0 + _geh,
                                      eq_exempt, eq_rate) - tax_h_final)
            liq_e += max(0.0, (equity_tax(_egp0 + _gep, eq_exempt, eq_rate,
                                          _pslab, exempt_c, band_h)
                               + equity_tax(_egh0 + _geh, eq_exempt, eq_rate,
                                            _hslab, 0.0, band_h))
                         - best.get("txe", 0.0))
        else:
            liq_p += personal_tax(_wtoi + _gp, _wexc, _wnb, _gep,
                                  _wee, _wer)
            liq_h += huf_tax(_gh, _wnb, _geh, _wee, _wer)
            liq_e += (equity_tax(_gep, _wee, _wer, _wtoi + _gp, _wexc, _wnb)
                      + equity_tax(_geh, _wee, _wer, _gh, 0.0, _wnb))

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

    # A plan can simply run out. Silence would be the worst possible answer:
    # an exhausted plan reports a small corpus and a small tax bill, both of
    # which look like success.
    #
    # WHEN IT RUNS OUT, IT RUNS OUT MID-YEAR. The engine is annual, so the
    # month is DERIVED rather than simulated: the requirement is assumed drawn
    # in twelve equal monthly instalments and the whole year's tax in MARCH,
    # the last month of the Indian financial year. A year that funded every
    # month and then could not pay its bill therefore fails in month twelve,
    # not earlier. Worth naming, because nothing else in this model has a
    # month in it.
    #
    # Everything after the failure is zeroed -- holdings, cash, tax, the lot.
    # Simulating past the point where the plan could not pay is arithmetic
    # about a plan that no longer exists, and it produced the most misleading
    # output this model has ever printed: a corpus climbing while the
    # requirement went unfunded.
    _sf = [max(0.0, r["net_need"] - r["personal_cash"] - r["huf_cash"])
           for r in rows]
    exhausted_fy = exhausted_month = 0
    _ex = next((k for k, v in enumerate(_sf) if v > PLAN_TOL), None)
    if _ex is not None:
        _r = rows[_ex]
        _frac = ((_r["personal_cash"] + _r["huf_cash"]) / _r["net_need"]
                 if _r["net_need"] > EPS else 0.0)
        exhausted_fy = _r["fy"]
        exhausted_month = max(0, min(12, int(_frac * 12.0)))
        for _later in rows[_ex + 1:]:
            for _k, _v in _later.items():
                if _k != "fy" and isinstance(_v, (int, float)):
                    _later[_k] = 0.0
        tax_p_tot = sum(x["tax_personal"] for x in rows)
        tax_h_tot = sum(x["tax_huf"] for x in rows)
        tax_eq_tot = sum(x["tax_equity"] for x in rows)
        liq_p = liq_h = liq_e = 0.0
        _lq_gain = _lq_cash = 0.0
        _sf = [max(0.0, x["net_need"] - x["personal_cash"] - x["huf_cash"])
               for x in rows]
    return dict(tax=tax_p_tot + tax_h_tot + tax_eq_tot,
                tax_personal=tax_p_tot, tax_huf=tax_h_tot,
                tax_equity=tax_eq_tot,
                shortfall=sum(_sf),
                exhausted_fy=exhausted_fy, exhausted_month=exhausted_month,
                shortfall_years=sum(1 for v in _sf if v > PLAN_TOL),
                first_shortfall_fy=next((r["fy"] for r, v in zip(rows, _sf)
                                         if v > PLAN_TOL), 0),
                liq_tax=liq_p + liq_h + liq_e,
                liq_personal=liq_p, liq_huf=liq_h, liq_equity=liq_e,
                liq_gain=_lq_gain, liq_years=_ln,
                liq_proceeds=_lq_cash, liq_net=_lq_cash - (liq_p + liq_h
                                                           + liq_e),
                p_debt=rows[-1]["p_debt"], p_eq=rows[-1]["p_eq"],
                huf_debt=rows[-1]["huf_debt"], huf_eq=rows[-1]["huf_eq"],
                equity=rows[-1]["value_equity"],
                cover_end=((rows[-1]["p_debt"] + rows[-1]["p_eq"]
                            + rows[-1]["huf_debt"] + rows[-1]["huf_eq"])
                           / rows[-1]["requirement"]
                           if rows[-1]["requirement"] > EPS else 0.0),
                corpus=(rows[-1]["p_debt"] + rows[-1]["p_eq"]
                        + rows[-1]["huf_debt"] + rows[-1]["huf_eq"]),
                p_debt_folios=rows[-1]["p_debt_folios"],
                p_eq_folios=rows[-1]["p_eq_folios"],
                huf_debt_folios=rows[-1]["huf_debt_folios"],
                huf_eq_folios=rows[-1]["huf_eq_folios"],
                folios=rows[-1]["p_debt_folios"], rows=rows)


def _mc_path(seed, horizon):
    """One sampled path of annual returns.

    Normal, and independent year to year -- no mean reversion, and no
    correlation between the sleeve and the book. Both of those are real and
    both would SOFTEN the tails, so this is the pessimistic reading of the
    assumption, which is the right way round for a plan.

    Equity is floored at -95% and debt at 0% so a deep draw cannot take a NAV
    to zero or below. At the shipped sigmas the equity floor binds beyond five
    standard deviations and the debt floor beyond four, so neither is doing
    real work; they are there so that a wide sigma cannot produce nonsense.
    """
    rng = random.Random(seed)
    eq = [max(-0.95, rng.gauss(MC_EQUITY_MEAN, MC_EQUITY_SIGMA))
          for _ in range(horizon)]
    dt = [max(0.0, rng.gauss(MC_DEBT_MEAN, MC_DEBT_SIGMA))
          for _ in range(horizon)]
    return eq, dt


def _mc_run(job):
    """One path, start to finish. Top level so multiprocessing can pickle it."""
    seed, scenario, horizon, cap, vintage, no_vintage = job
    eq, dt = _mc_path(seed, horizon)
    globals()["EQUITY_PATH"], globals()["DEBT_PATH"] = eq, dt
    try:
        r = simulate(scenario, horizon, cap, vintage_every=vintage,
                     no_vintage=no_vintage)
    finally:
        globals()["EQUITY_PATH"] = globals()["DEBT_PATH"] = None
    return (r["corpus"], r["tax"], r["liq_tax"], r["cover_end"],
            r["shortfall"], r["first_shortfall_fy"], seed)


def _mc_progress(done, total, t0, width=32):
    """A bar on stderr, so stdout stays a clean report you can pipe.

    imap_unordered gives results as they land, which is what makes a live
    count possible at all -- Pool.map returns nothing until every path is
    finished. When stderr is not a terminal the bar would be thousands of
    lines of carriage returns in a log, so it degrades to one line per tenth.
    """
    el = time.time() - t0
    rate = done / el if el > 0 else 0.0
    eta = (total - done) / rate if rate > 0 else 0.0
    if sys.stderr.isatty():
        fill = int(width * done / total)
        print(f"\r  [{'#' * fill}{'.' * (width - fill)}] {done}/{total}  "
              f"{el:5.0f}s elapsed, {eta:5.0f}s left ", end="", file=sys.stderr)
        if done == total:
            print(file=sys.stderr)
    elif done == total or done % max(1, total // 10) == 0:
        print(f"  {done}/{total} paths, {el:.0f}s elapsed, {eta:.0f}s left",
              file=sys.stderr)


def _cw_run(job):
    """One (weightage, realised equity rate) cell of the conservative sweep.

    Top level so multiprocessing can pickle it. The 49 cells are independent
    of one another for exactly the reason the Monte Carlo paths are, so the
    same pool does the job -- the only difference is that each cell sets two
    globals rather than a sampled path.
    """
    weight, eq, scenario, horizon, cap, vintage, no_vintage = job
    globals()["CONSERVATIVE_WEIGHT"] = weight
    globals()["EQUITY_RETURN"] = eq
    r = simulate(scenario, horizon, cap, vintage_every=vintage,
                 no_vintage=no_vintage)
    return weight, eq, r["corpus"], r["tax"], r["liq_tax"], r["cover_end"]


def _pct(vals, q):
    """Nearest-rank percentile. No interpolation: with 100 paths an
    interpolated p5 invents a number no path actually produced."""
    if not vals:
        return 0.0
    xs = sorted(vals)
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


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
    ap.add_argument("--scenario", default="2chunk", choices=sorted(SCENARIO_NAMES))
    ap.add_argument("--horizon", type=int, default=None,
                    help=f"years to model (default {HORIZON}). Ignored if "
                         f"--dob is given.")
    ap.add_argument("--dob", default=None, metavar="YYYY-MM-DD",
                    help=f"date of birth. The horizon becomes the number of "
                         f"years from the START OF THE CURRENT FINANCIAL YEAR "
                         f"to age {PLAN_TO_AGE}, so the plan is modelled to "
                         f"that age and no further. Overrides --horizon.")
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
    ap.add_argument("--decision_equity_cagr", "--decision-equity-cagr",
                    type=float, default=None,
                    help="equity rate used to SCORE candidates, not to grow "
                         "the sleeve. Default: the modelled --equity_cagr.")
    ap.add_argument("--decision_debt_rate", "--decision-debt-rate",
                    type=float, default=None,
                    help="debt rate used to SCORE candidates, not to grow the "
                         "books. Default: chunk 1's rate.")
    ap.add_argument("--conservative_weightage", "--conservative-weightage",
                    type=float, default=None, metavar="W",
                    help="how much of the SCORE is judged at the conservative "
                         "rates: W=0 scores purely at the modelled rates, W=1 "
                         "purely at the conservative ones. Linear in the "
                         "projection, so it costs no runtime. Default 0.6.")
    ap.add_argument("--monte_carlo", "--monte-carlo", type=int, nargs="?",
                    const=MC_DEFAULT_PATHS, default=0, metavar="N",
                    help=f"run N sampled return paths and report the "
                         f"distribution of corpus, tax and cover. Bare, it "
                         f"runs {MC_DEFAULT_PATHS}. Paths are independent, so "
                         f"this runs across several cores.")
    ap.add_argument("--liquidation_years", "--liquidation-years", type=int,
                    default=None, metavar="N",
                    help="spread the closing liquidation over N years instead "
                         "of one. Each year brings a fresh s.87A ceiling, HUF "
                         "nil band and s.112A exemption, and what is not yet "
                         "sold goes on earning. Nothing is spent during the "
                         "wind-down.")
    ap.add_argument("--mc_csv", "--mc-csv", default=None, metavar="PATH",
                    help="write one row per path: seed, corpus, tax, liq_tax, "
                         "cover, shortfall. Rows are ordered by seed, so two "
                         "runs on the same --mc_seed can be differenced path "
                         "by path -- which resolves a small effect that the "
                         "percentile table cannot.")
    ap.add_argument("--mc_equity_mean", type=float, default=None,
                    help=f"mean annual equity return (default "
                         f"{MC_EQUITY_MEAN})")
    ap.add_argument("--mc_equity_sigma", type=float, default=None,
                    help=f"sd of annual equity return (default "
                         f"{MC_EQUITY_SIGMA})")
    ap.add_argument("--mc_debt_mean", type=float, default=None,
                    help=f"mean annual debt rate (default {MC_DEBT_MEAN})")
    ap.add_argument("--mc_debt_sigma", type=float, default=None,
                    help=f"sd of annual debt rate (default {MC_DEBT_SIGMA})")
    ap.add_argument("--mc_seed", type=int, default=MC_DEFAULT_SEED,
                    help="base seed. The same seed gives the same paths, so "
                         "two strategies can be compared on identical worlds.")
    ap.add_argument("--mc_workers", type=int, default=0,
                    help="processes to use. Default is HALF the cores: this "
                         "is a 15W part, the package is power-limited long "
                         "before it is core-limited, and hyperthread siblings "
                         "share execution units on work like this. Measured, "
                         "six workers finished 200 paths in 62s against 80s "
                         "on twelve -- faster AND leaving the machine usable.")
    ap.add_argument("--conservative_sweep", "--conservative-sweep",
                    dest="conservative_sweep", action="store_true",
                    help="sweep the conservative weightage against realised "
                         "equity CAGR and report corpus, lifetime tax and "
                         "years of cover at the horizon for each cell")
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
    for _flag, _const in (("mc_equity_mean", "MC_EQUITY_MEAN"),
                          ("mc_equity_sigma", "MC_EQUITY_SIGMA"),
                          ("mc_debt_mean", "MC_DEBT_MEAN"),
                          ("mc_debt_sigma", "MC_DEBT_SIGMA")):
        if getattr(a, _flag) is not None:
            globals()[_const] = getattr(a, _flag)
    if a.decision_equity_cagr is not None:
        globals()["DECISION_EQUITY_CAGR"] = a.decision_equity_cagr
    if a.decision_debt_rate is not None:
        globals()["DECISION_DEBT_RATE"] = a.decision_debt_rate
    if a.conservative_weightage is not None:
        globals()["CONSERVATIVE_WEIGHT"] = a.conservative_weightage
    if a.liquidation_years is not None:
        if a.liquidation_years < 1:
            sys.exit(f"FATAL: --liquidation_years must be at least 1. Got "
                     f"{a.liquidation_years}.")
        globals()["LIQUIDATION_YEARS"] = a.liquidation_years

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

    if a.dob:
        try:
            _y, _m, _d = (int(p) for p in a.dob.replace("/", "-").split("-"))
            _born = datetime.date(_y, _m, _d)
        except (ValueError, TypeError):
            sys.exit(f"FATAL: --dob must be YYYY-MM-DD. Got {a.dob!r}.")
        # Age at the START of the current Indian financial year, which begins
        # on 1 April. A plan is modelled in whole financial years, so this is
        # the only anchor that makes the horizon an integer without inventing
        # a part-year.
        _today = datetime.date.today()
        _fy_start = datetime.date(_today.year if _today.month >= 4
                                  else _today.year - 1, 4, 1)
        _age = (_fy_start.year - _born.year
                - ((_fy_start.month, _fy_start.day)
                   < (_born.month, _born.day)))
        if _age < 0:
            sys.exit(f"FATAL: --dob {a.dob} is in the future.")
        if _age >= PLAN_TO_AGE:
            sys.exit(f"FATAL: --dob {a.dob} gives an age of {_age} at "
                     f"FY start, which is already {PLAN_TO_AGE} or more. "
                     f"There is no horizon left to model.")
        a.horizon = PLAN_TO_AGE - _age
        if not a.quiet:
            print(f"--dob {a.dob}: age {_age} at the start of FY"
                  f"{_fy_start.year}-{(_fy_start.year + 1) % 100:02d}, so the "
                  f"horizon is {a.horizon} years to age {PLAN_TO_AGE}.")
    if a.horizon is None:
        a.horizon = HORIZON
    if a.horizon < MIN_HORIZON:
        sys.exit(f"FATAL: --horizon {a.horizon} is below the minimum of "
                 f"{MIN_HORIZON}.")
    if a.horizon > MAX_HORIZON:
        sys.exit(f"FATAL: --horizon {a.horizon} is above the maximum of "
                 f"{MAX_HORIZON}.")
    if a.horizon > LADDER_PROJECTED_YEARS and not a.quiet:
        print(f"NOTE: the statutory ladders are projected to "
              f"{LADDER_PROJECTED_YEARS} years. From FY"
              f"{LADDER_PROJECTED_YEARS + 1} the last block repeats, so the "
              f"s.87A ceiling, the HUF nil band and the s.112A exemption stop "
              f"growing. Extend EXEMPT_BLOCKS, NILBAND_BLOCKS, "
              f"EQ_EXEMPT_BLOCKS and EQ_RATE_BLOCKS to change that.")

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

    if a.monte_carlo:
        _n = a.monte_carlo
        _w = a.mc_workers or max(1, (multiprocessing.cpu_count() or 2) // 2)
        _jobs = [(a.mc_seed + k, a.scenario, a.horizon, a.max_folios,
                  a.vintage, a.no_vintage) for k in range(_n)]
        print(f"MONTE CARLO -- {_n} sampled paths, {a.scenario}, "
              f"{a.horizon} years")
        print(f"equity  N({MC_EQUITY_MEAN:.2%}, {MC_EQUITY_SIGMA:.2%}) a year")
        print(f"debt    N({MC_DEBT_MEAN:.2%}, {MC_DEBT_SIGMA:.2%}) a year, "
              f"each fund keeping its own spread against chunk 1")
        print("Decisions are still scored at the FLAT assumptions, blended by "
              "the conservative")
        print("weightage. The plan cannot see the path it is on, so this "
              "tests the STRATEGY")
        print("under uncertainty and not clairvoyance.\n")
        _t0 = time.time()
        _res = []
        if _w > 1 and _n > 1:
            with multiprocessing.Pool(_w) as _pool:
                for _done, _one in enumerate(
                        _pool.imap_unordered(_mc_run, _jobs, chunksize=1), 1):
                    _res.append(_one)
                    _mc_progress(_done, _n, _t0)
        else:
            for _done, _job in enumerate(_jobs, 1):
                _res.append(_mc_run(_job))
                _mc_progress(_done, _n, _t0)
        _el = time.time() - _t0
        _corp = [x[0] for x in _res]
        _tax = [x[1] for x in _res]
        _liq = [x[2] for x in _res]
        _cov = [x[3] for x in _res]
        _ruined = [x for x in _res if x[4] > PLAN_TOL]
        print(f"{'':>18}{'p5':>10}{'p25':>10}{'p50':>10}{'p75':>10}"
              f"{'p95':>10}{'mean':>10}")
        print("-" * 78)
        for _label, _v in (("end corpus", _corp), ("lifetime tax", _tax),
                           ("liquidation tax", _liq),
                           ("cover, years", _cov)):
            print(f"{_label:>18}" + "".join(f"{_pct(_v, q):>10.1f}"
                                            for q in (0.05, 0.25, 0.5,
                                                      0.75, 0.95))
                  + f"{sum(_v) / len(_v):>10.1f}")
        print()
        if a.mc_csv:
            # Ordered by seed, NOT by completion. imap_unordered returns paths
            # as they land, so an unsorted file would differ run to run and
            # could not be differenced against another. The seed is the join
            # key: same --mc_seed, same paths, so row N here and row N there
            # are the same world under two strategies.
            with open(a.mc_csv, "w", newline="") as _fh:
                _wr = csv.writer(_fh)
                _wr.writerow(["seed", "corpus", "tax", "liq_tax", "cover",
                              "shortfall"])
                for _x in sorted(_res, key=lambda t: t[6]):
                    _wr.writerow([_x[6], f"{_x[0]:.6f}", f"{_x[1]:.6f}",
                                  f"{_x[2]:.6f}", f"{_x[3]:.6f}",
                                  f"{_x[4]:.6f}"])
            print(f"wrote {a.mc_csv} -- {_n} rows, ordered by seed\n")
        if _ruined:
            _fy = sorted(x[5] for x in _ruined)
            print(f"RUIN: {len(_ruined)} of {_n} paths "
                  f"({len(_ruined) / _n:.1%}) failed to fund the requirement. "
                  f"Median first failure FY{_fy[len(_fy) // 2]}, earliest "
                  f"FY{_fy[0]}. Median shortfall "
                  f"{_pct([x[4] for x in _ruined], 0.5):.1f}.")
        else:
            print(f"RUIN: none of {_n} paths failed to fund the requirement.")
        print("\nRead p5 first. The median path is not the plan you are "
              "buying insurance against.")
        print(f"{_n} paths on {_w} worker(s) in {_el:.1f}s "
              f"({_el / _n * 1000:.0f} ms a path).")
        return 0

    if a.conservative_sweep:
        _dd = (min(chunk_rates(1)[0], DECISION_DEBT_FLOOR)
               if DECISION_DEBT_RATE is None else DECISION_DEBT_RATE)
        _ws = (0.0, 0.25, 0.4, 0.5, 0.6, 0.75, 1.0)
        _eqs = (0.07, 0.08, 0.09, 0.10, 0.11, 0.125, 0.14)
        _true, _tw = EQUITY_RETURN, CONSERVATIVE_WEIGHT
        print("CONSERVATIVE WEIGHTAGE SWEEP")
        print(f"Conservative scoring rates: debt {_dd:.2%}, equity the LOWER")
        print("of the column rate and the equity floor. W is how much of the")
        print("score is judged at those rates: 0.00 purely at what the model")
        print("delivers, 1.00 purely at the conservative ones. Every other")
        print("parameter is as printed above; only the REALISED equity CAGR")
        print("moves across the columns.\n")
        _res = {}
        _cw_jobs = [(_w, _eq, a.scenario, a.horizon, a.max_folios,
                     a.vintage, a.no_vintage)
                    for _w in _ws for _eq in _eqs]
        _cw_workers = a.mc_workers or max(
            1, (multiprocessing.cpu_count() or 2) // 2)
        _t0cw = time.time()
        if _cw_workers > 1 and len(_cw_jobs) > 1:
            with multiprocessing.Pool(_cw_workers) as _pool:
                for _done, _one in enumerate(
                        _pool.imap_unordered(_cw_run, _cw_jobs, chunksize=1), 1):
                    _res[(_one[0], _one[1])] = dict(
                        corpus=_one[2], tax=_one[3], liq_tax=_one[4],
                        cover_end=_one[5])
                    _mc_progress(_done, len(_cw_jobs), _t0cw)
        else:
            for _done, _job in enumerate(_cw_jobs, 1):
                _one = _cw_run(_job)
                _res[(_one[0], _one[1])] = dict(
                    corpus=_one[2], tax=_one[3], liq_tax=_one[4],
                    cover_end=_one[5])
                _mc_progress(_done, len(_cw_jobs), _t0cw)
        globals()["EQUITY_RETURN"], globals()["CONSERVATIVE_WEIGHT"] = _true, _tw
        _cw_el = time.time() - _t0cw
        for _title, _key, _wd, _p in (("END CORPUS", "corpus", 10, 1),
                                     ("LIFETIME TAX", "tax", 10, 3),
                                     ("YEARS OF COVER AT HORIZON",
                                      "cover_end", 10, 1)):
            print(_title)
            print(f"{'W':>8}" + "".join(f"{e:>10.1%}" for e in _eqs)
                  + f"{'worst':>10}")
            print("-" * (18 + 10 * len(_eqs)))
            for _cw in _ws:
                _row = [_res[(_cw, e)][_key] for e in _eqs]
                print(f"{_cw:>8.2f}"
                      + "".join(f"{v:>{_wd}.{_p}f}" for v in _row)
                      + f"{min(_row):>{_wd}.{_p}f}")
            print()
        print("Read the WORST column first. The plan is only in danger in the")
        print("low-return states, and years of cover at the horizon is the")
        print("number that says whether it ran aground. A weightage that")
        print("gives up a little in the 12.5% and 14% columns to buy cover in")
        print("the 7% and 8% ones is the trade this sweep exists to price.")
        print(f"{len(_cw_jobs)} cells on {_cw_workers} worker(s) in "
              f"{_cw_el:.1f}s.")
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
          f"(personal {r['tax_personal']:.3f} + HUF {r['tax_huf']:.3f}"
          f" + equity {r['tax_equity']:.3f})")
    print(f"end corpus   {r['corpus']:.1f}")
    print(f"  p_debt   {r['p_debt']:>9.1f}   folios {r['p_debt_folios']}")
    print(f"  p_eq     {r['p_eq']:>9.1f}   folios {r['p_eq_folios']}"
          f"   buckets {r['rows'][-1]['p_eq_buckets']} of {N_EQ_FUNDS}")
    print(f"  huf_debt {r['huf_debt']:>9.1f}   folios {r['huf_debt_folios']}")
    print(f"  huf_eq   {r['huf_eq']:>9.1f}   folios {r['huf_eq_folios']}"
          f"   buckets {r['rows'][-1]['huf_eq_buckets']} of {N_EQ_FUNDS}")
    if r["shortfall"] > PLAN_TOL:
        _mn = ["April", "May", "June", "July", "August", "September",
               "October", "November", "December", "January", "February",
               "March"]
        _m = r["exhausted_month"]
        _done = (r["exhausted_fy"] - 1) * 12 + _m
        print(f"THE MONEY RAN OUT in month {_m} of FY{r['exhausted_fy']}"
              f"{' (' + _mn[_m - 1] + ')' if 1 <= _m <= 12 else ''}. "
              f"{a.horizon * 12 - _done} of the {a.horizon * 12} months in "
              f"the horizon could not be funded.")
        print("  Everything after that point is zero. The requirement is "
              "assumed drawn in twelve")
        print("  equal monthly instalments and the year's tax in March, so a "
              "year that funds every")
        print("  month and still cannot pay its bill fails in month 12.")
        return
    print(f"Final liquidation tax = {r['liq_tax']:.3f}")
    _ly = r["liq_years"]
    if _ly == 1:
        _when = (f"charged in FY{a.horizon} on top of that year's own bill")
    else:
        _when = (f"spread over FY{a.horizon} to FY{a.horizon + _ly - 1}, the "
                 f"first slice on top of FY{a.horizon}'s own bill and each "
                 f"later year on fresh allowances")
    print(f"  (personal {r['liq_personal']:.3f} + HUF {r['liq_huf']:.3f}"
          f" + equity {r['liq_equity']:.3f} on {r['liq_gain']:.1f} of"
          f" gain, {_when})")
    print(f"Cash in hand after liquidation = {r['liq_net']:.1f}"
          f"   (proceeds {r['liq_proceeds']:.1f} less the tax above)")
    if a.csv:
        write_csv(r, a.csv)
        print(f"wrote {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())