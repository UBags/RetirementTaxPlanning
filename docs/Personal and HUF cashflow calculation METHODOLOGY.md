# METHODOLOGY — two-purse SWP tax minimisation

Companion to `HANDOVER_merged.md`. That document says what is true about the
**codebase**; this one says how the **model** works, why each rule is the rule,
and how to apply it to inputs it has never seen.

Written so that none of it has to be rediscovered. Where a number appears it
came from a run, and the run is named. Where a constant was fitted rather than
derived, it says so.

**Status:** reproduces the published reference panels. Reference implementation
`two_purse_model.py`. Plain Python, no engine imports. The engine has never been
run against any of it.

---

## 0. What is verified, and what is not

Reproduced exactly, against panels published in `Previous_conversation.docx`:

| configuration | model | published |
|---|---|---|
| `c0` — 2-chunk, 1 folio, 25% turnover capacity-driven | 0.17 / **1934.1** | 0.19 / 1934.1 |
| 1-chunk, rationed, 2 folios | 0.04 / **1919.4** | 0.04 / 1919.3 |
| 1-chunk, rationed, 3 folios — **(b)** | 0.00 / **1920.2** | 0.00 / 1920.2 |
| 1-chunk, rationed, 4–5 folios | 0.00 / 1920.1 – 1920.0 | 0.00 / 1920.0 |
| 2-chunk, 3 folios, 25% turnover — **(c)** | 0.00 / **1934.3** | 0.00 / 1934.3 |

Row-level agreement on `c0`: FY1 and FY4 exact to all published digits; FY5–FY30
within 0.10 on any column; HUF FY6 `76.43 / 5.00 / 67.16` against a published
`76.43 / 5.00 / 67.17`.

Every end-corpus target lands. The one systematic divergence is **single-folio
lifetime tax** (0.17 against 0.29 and 0.19) — the configuration where the fitted
ration constant binds hardest. Multi-folio tax is zero in both, so the
divergence does not touch the headline result.

**Not verified:** nothing here is pinned by a test in the repo; the engine has
not been run against it; there is no Monte Carlo on any figure.

---

## 1. The problem

Deliver a fixed **net, post-tax** cash requirement `R(t)` for `T` years across
two taxable entities, at minimum lifetime tax, and measure what is left.

```
net cash in hand = W_personal + W_HUF + other_income − tax_p − tax_h − exit_loads
```

No transfer term. Tax savings stay in the personal purse; the HUF receives its
inheritance and nothing else. Tax reduces cash in hand always and monotonically,
so **minimising tax and easing withdrawal pressure are one goal**, not two that
trade off.

**Check the identity per year, never in aggregate.** A lifetime total can be
right while individual years are wrong in both directions. This caught two
over-delivery bugs during the original modelling that the tax totals hid
completely — one delivering 44.64 against a 42.5 requirement from FY12 onward.
The reference implementation asserts it every year; worst residual across all 30
years of (b) and (c) is 8.5e-14.

---

## 2. The central idea

Two quantities behave completely differently, and almost every mistake in this
problem comes from confusing them.

**Tax-free headroom is a FLOW.** `H_p(t) = E(t) − a(t)·OI(t)` for the personal
purse, `H_h(t) = N(t)` for the HUF. It arrives each year and **expires unused at
year end**. It cannot be saved.

**Cost basis is a STOCK.** It persists until the units are sold.

**Harvesting is the only conversion between them**: sell units, realise gain
against this year's expiring headroom, rebuy at today's NAV. Expiring flow
becomes permanent stock.

Four consequences follow, and they are the whole method:

1. **Never leave headroom unused.** It is free and the only way to bank it is to
   convert it into basis.
2. **The conversion is worth little early and much late.** Harvesting in FY1
   steps basis from NAV 1.000 to 1.073 — a 7% step, requiring 89.5 of churn.
   By FY20 the same headroom needs 2.40 of churn. Headroom is *largest* early
   and *worth least* early. They are anti-correlated; do not assume more or
   earlier harvesting is better.
3. **Placement matters as much as quantity.** Banked basis is worthless if the
   withdrawal never reaches it. Under FIFO in a single pool, rebought units go
   to the *back* of the queue while the front remains original-basis stock. Over
   30 years only ~200 of the original 350 units are ever consumed, so the front
   never advances. Measured: gain fraction identical in every year with and
   without harvesting — 7%, 13%, 30%, 50%, 65%, 75%, 83%, 88%. **Single-folio
   harvesting is worse than no harvesting** (16.47 against 6.55): all the churn,
   none of the benefit, and it shifts the split and drains the HUF earlier.
4. **Separate folios fix placement.** FIFO is computed per folio, so a folio of
   fresh basis presents that basis at its own front. Cash draws take it; the
   harvest takes the opposite folio. That is the entire reason folios exist.

**A nil band with no corpus behind it is unusable.** This is the second-order
version of the same idea and it is what the HUF ration protects: the HUF's
`N(t)` is only realisable while the HUF still holds units. Draining it early
destroys every later year's band.

---

## 3. Notation

| symbol | meaning |
|---|---|
| `t` | plan year, 1…T |
| `R(t)` | required **net** cash in hand |
| `OI(t)` | other income received in cash |
| `a(t)` | assessable share of other income |
| `E(t)` | personal exempt limit (the s.87A rebate threshold) |
| `N(t)` | HUF nil band |
| `H_p(t)` | personal headroom `= max(0, E(t) − a(t)·OI(t))` |
| `H_h(t)` | HUF headroom `= N(t)` while the HUF holds units |
| `D(t)` | cash demand on the corpora `= R(t) − OI(t)` |
| `f` | gain fraction of a lot `= (NAV_now − NAV_buy) / NAV_now` |
| `V_h(t)` | HUF value after growth, before withdrawal |
| `k` | HUF ration constant (§9.1) |

A lot is `(units, basis_nav, buy_year)`. A folio is a FIFO list of lots. A purse
is an ordered set of folios.

---

## 4. Order of operations within a year

**Reproduce this exactly.** The ordering is not cosmetic — §5.3 is a real
priority rule and getting it wrong changes every column.

```
 1  record p_start = personal value at last year's NAV
 2  NAV_p ×= (1 + r(t))
 3  record p_pre = personal value            ("Corp pre-wd")
 4  HUF: if this is the inheritance year, credit it with NO growth;
        otherwise NAV_h ×= (1 + r_h)
 5  record h_pre
 6  compute H_p(t), N(t), D(t)
 7  HUF offer = min( k·V_h(t)/(T−t+1),  HUF tax-free cash capacity )
 8  PERSONAL CASH   draw min(D − offer, capacity within H_p)
                    cheapest folio first, spilling, hard-capped on gain
 9  HUF CASH        draw D − (net personal cash), grossed up for exit load
                    and tax, cheapest folio first
10  HUF HARVEST     fill the remainder of N(t) from the dearest folio, rebuy
11  ESCALATION      if still short, personal draws beyond H_p and pays
                    marginal-relief tax (§8)
12  TRANSITION      dearest folio, gain budget = H_p − gain already realised
                    on cash, rupee-capped by "still to move", rebuy
13  HARVEST         dearest folio, gain budget = whatever remains, rebuy
14  record year-end values
```

Steps 8 and 9 are a two-way dependency resolved by ordering: the HUF *offers*
first (step 7) so personal knows its residual, then personal draws, then the HUF
*settles* the actual residual. Step 9 needs a small fixed point because the HUF's
own tax and exit load reduce the cash its gross draw delivers.

### Panel column definitions

Getting these wrong makes a correct model look broken.

- **`Withdrawn`** = cash draw **+ harvest**. It excludes the transition.
- **`Re-inv fresh`** = the harvest, rebought.
- **`Switch moved`** = the transition, sold and fully rebought. Separate column.
- **`Taxbl gain`** = *all* gain realised, including `Switch gain`.
- **net cash from a purse** = `Withdrawn − Re-inv fresh − exit load − tax`.
- **`Still to move`** = transition target less cumulative `Switch moved`.

---

## 5. The decision rules

Each was reverse-engineered from the published panels and then checked
arithmetically. Re-verify rather than assume.

### 5.1 Personal water-fill

**Realise exactly `H_p(t)` of gain every year. Never more, never less.**

*Never more*, because under s.87A marginal relief the marginal rate on the first
rupee above `E(t)` is effectively **100%** — tax equals the excess until the
normal slab charge catches up. The next rupee of personal gain is the most
expensive rupee available anywhere in the plan.

*Never less*, because headroom expires. The value may be small (§2.2) but it is
never negative.

Verified at FY1, 2, 6, 11, 16, 21, 30 — exact every time (6.05, 5.86, 7.16,
8.38, 9.72, 11.18, 10.50), each summing with assessable other income to
precisely 12 / 14 / 16 / 18 / 20.

A structural consequence worth knowing: **the `Taxbl gain` column is identical
across configurations.** In (b) and (c) it is the same 30 numbers. Realised gain
is set by the statute, not by the portfolio, the return path, the folio count, or
whether a transition is running.

### 5.2 The HUF ration

**Redeem exactly `N(t)`'s worth of gain. Take `k·V_h(t)/(T−t+1)` as cash. Rebuy
the rest.** A cap, not a schedule.

Worked check, FY6: the HUF holds only the FY5 windfall, one year old, so its gain
fraction is `1 − 1/1.07 = 6.54%`. Realising the 5.00 band requires selling
`5.00 / 0.0654 = 76.4`. The published panel says `Withdrawn 76.43`,
`Re-inv fresh 67.17`. The rule holds.

The ration exists because a greedy per-year split drains the HUF as fast as it
looks locally cheap; it then empties around FY25–29 and the personal purse
carries everything alone at an 85–88% gain fraction, which is where every rupee
of late-plan tax comes from. **Rationing is 98% of the tax reduction**
(16.47 → 0.29); folios are the remaining 2% and buy margin, not headline.

### 5.3 Headroom priority — one pool, spent in a fixed order

**Cash withdrawal → transition → harvest.**

Cash is non-negotiable, the transition has a deadline, the harvest is
free-floating. Verified on the published panel at FY6: the cash draw of 10.07
consumes `10.07 × 0.35205 = 3.55` of the 7.16 headroom; the transition takes the
remaining 3.61, which at that gain fraction is `3.61 / 0.35205 = 10.26` — the
published `Switch moved`.

The signature of this rule is a **column of zeros**. In (c) the `Re-inv fresh`
column is zero for FY8–FY21, fourteen consecutive years, because the transition
consumes all available capacity. The transition and the harvest **compete; they
do not add**. Combining a capacity-driven transition with three folios moves
lifetime tax only 0.19 → 0.00 and corpus 1934.1 → 1934.3.

Where two transition windows overlap, their shared capacity **must be allocated
between them, not offered to each**. Double-spending produces a schedule that
reports success while over-committing.

### 5.4 Folio orderings — deliberately opposite

Within a folio FIFO is forced, so the only lever is *which folio*.

- **cash** → folio with the **lowest** front-lot gain fraction. Minimises taxable
  gain per rupee raised.
- **harvest and transition** → folio with the **highest** front-lot gain fraction.
  Maximises gain realised per rupee churned, and retires the units that would
  cost most later.

A single "pick the cheapest" scan implements half of this and gets the wrong
half. Both operations must **spill** onward when one folio cannot cover the
amount; silently stopping at the first folio under-delivers.

**A capacity that spills cannot be computed from the front lot.** The most cash a
purse can raise within a gain cap is *not* `cap / front_fraction`, because once a
draw crosses into a dearer folio the marginal gain per rupee changes. Measure it
by simulating the draw on a copy. Getting this wrong made more folios come out
*worse* than fewer — see §10.2.

### 5.5 Exit load

**1% on units sold within `stcg_holding_months` (12).** In an annual model that
means sold in the same plan year they were bought.

It fires in exactly one place in the reference plan: the HUF is funded at FY5 and
draws in FY5, so `8.72 × 1% = 0.087`. That is the published panel's otherwise
unexplained gap — `8.64 + 8.72 − 0.087 = 17.27`, the reported net. It is
confirmed independently by the personal column, which reads 8.64 only if the load
is charged.

Harvesting oldest-first satisfies the holding test automatically and avoids the
load, so it never fires on a harvest.

### 5.6 Tax functions

**Personal — new regime with s.87A.** Slab charge on `a(t)·OI(t) + realised
gain`, zero at or below `E(t)`, and above it capped by **marginal relief** at
`income − E(t)`. The marginal rate immediately above the threshold is therefore
~100%.

**Cess.** Both charges are grossed by `1 + CESS` (4%). `engine.py`'s
`compute_slab_tax` applies it; the published reference panels do **not** — their
HUF tax of 0.01 on a 0.21 overshoot is exactly 5%. The reference implementation
now applies it, which is why single-folio `c0` reads 0.174 here against 0.19 in
the published panel. Any comparison against those panels must set `CESS = 0.0`.

**HUF — old regime, no s.87A.** `0–N` nil, `N–2N` at 5%, `2N–4N` at 20%, above
`4N` at 30%. Confirmed against the published panel: overshoots of 0.21, 0.46,
0.70, 1.01, 1.23 against a band of 9.00 produced 0.01, 0.02, 0.04, 0.05, 0.06 —
exactly 5%.

**This asymmetry drives the entire answer.** The HUF pays 5% on its first rupee
above the band; the personal purse pays ~100% on its first rupee above the limit.
Shortfalls therefore go to the HUF, always, until the HUF exceeds `2N`.

---

## 6. Screening diagnostics — run these before solving

Cheap, and they tell you whether zero is even reachable.

### 6.1 The critical gain fraction

```
f*(t) = ( H_p(t) + H_h(t) ) / D(t)
```

**The highest gain fraction at which year `t` can still be funded tax-free.**

- `f*(t) ≥ 1` → the year is free for any basis whatsoever. Nothing to manage.
- `f*(t) < 1` → you must hold units at a gain fraction below `f*(t)`.
- `f*(t)` small → you need a lot of fresh basis, and folios become load-bearing.

For the reference plan `f*` runs 0.31 (FY1) → 0.63 (FY6) → 0.79 (FY30). It
*rises* over the plan, because the HUF's band joins at FY5 and the statutory
ladders step up faster than the requirement.

### 6.2 The natural gain fraction and the crossover

`f_nat(t) = 1 − NAV(0)/NAV(t)` — the gain fraction of the original vintage if
nothing is ever harvested. It rises monotonically toward 1.

**The crossover year** is the first `t` where `f_nat(t) > f*(t)`. Before it,
the plan is free with no management at all. After it, zero tax depends entirely
on basis management.

The gap between the crossover and the first year that actually pays tax
**measures what the management buys**:

| scenario | crossover | first taxed year | years bought |
|---|---|---|---|
| reference | FY20 | never | ≥ 11 |
| requirement × 1.2 | FY4 | FY20 | 16 |
| requirement × 1.4 | FY3 | FY13 | 10 |

Use it as a screen, not a prediction: crossover says *when you start needing the
machinery*, not when you start paying.

---

## 7. Generalisation rules

For an arbitrary starting corpus, other-income profile and requirement profile.
Each rule below is followed by the evidence for it.

### G1. Lifetime tax does not depend on the starting corpus

Measured across `C = 250 / 350 / 450 / 600` at four requirement scales: the tax
figure is **identical in every column** — 0.00, 4.01, 142.44 — and only the end
corpus moves.

Why: the demand `D(t) = R(t) − OI(t)` is in rupees and does not involve `C`. The
gain realised is `D(t) × f`, and `f` is a property of the NAV path and the
harvest history, not of how many units you hold.

**Therefore: the corpus determines *feasibility* (whether the plan survives to
`T`), not the tax bill.** Do not size the corpus to reduce tax; size it to avoid
exhaustion. Larger corpora only bought extra columns of the grid at all, never a
lower tax figure.

### G2. The binding input is the *assessable share* of other income

Measured across other-income level × assessable share:

| | share 0.25 | share 0.38 | share 0.60 | share 1.00 |
|---|---|---|---|---|
| OI × 0.8 | 0.00 | 0.00 | 2.96 | 169.31 |
| OI × 1.0 | 0.00 | 0.00 | 4.25 | 149.11 |
| OI × 1.3 | 0.00 | 0.00 | 5.89 | 14.36 |
| OI × 1.6 | 0.00 | 0.00 | 5.94 | 17.36 |

Assessable other income enters `H_p` with a **minus sign** and the cash side with
a **plus sign**, so raising it does two opposite things: it shrinks headroom and
it shrinks demand. At low levels the headroom loss dominates; at high levels the
demand relief dominates, which is why the last column falls from 169 to 17.

**Therefore: s.16(ia) and s.24(a) deductions are worth more than their face
value.** Anything that moves income from assessable to non-assessable widens
`H_p` directly. This is the highest-leverage input in the whole model and it is
usually treated as fixed.

### G3. Lower returns make the tax problem *easier*

A −1% shock across all 30 years leaves lifetime tax at 0.00 for every ration
constant tested, while end corpus falls from ~1920 to ~1410.

Why: a lower return means a lower NAV, hence a lower gain fraction on everything
sold, hence less gain per rupee of cash raised.

**Therefore: return risk is a corpus risk, not a tax risk.** Do not carry a tax
buffer against disappointing returns. Do carry one against rising assessable
income and rising requirements, which move the wrong way.

### G4. Derive the ration constant; do not carry `k = 1.2` across

`k` is a **search parameter, not a constant of nature.** Sweep it and take the
lexicographic optimum: minimise lifetime tax, then maximise end corpus, subject
to the per-year identity holding and neither corpus going negative.

On the reference plan this gives a plateau, not a point:

| k | tax | end corpus | HUF at FY30 |
|---|---|---|---|
| 1.00 | 0.00 | 1917.3 | 236.0 |
| 1.20 | 0.00 | 1920.2 | 182.7 |
| 1.40 | 0.00 | **1922.8** | 133.1 |
| 1.60 | 0.02 | 1925.2 | 89.5 |
| 2.00 | 0.06 | 1929.2 | 14.5 |

Zero tax holds anywhere on `k ∈ [1.0, 1.4]`; end corpus rises across that range,
then tax appears as the HUF is drawn too hard to keep its band funded.

**The reason end corpus rises with `k` is pure return arbitrage**: the HUF earns
7.0% against the personal purse's 7.25–7.5%, so once tax is zero, every rupee
drawn from the *lower*-returning purse instead of the higher one adds to the
terminal total. `k` is the dial for that trade, bounded above by the point where
the HUF can no longer fund its own nil band.

**Generalised rule:** *once the tax constraint is slack, draw preferentially from
the lower-returning purse.* The published `k ≈ 1.2` was conservative — it sits
inside the zero-tax plateau but ~2.6 L short of its top on (b).

### G4a. When the two purses return the same rate, `k` reverts to a survival
constraint only, and the smallest `k` that keeps tax at zero should be preferred,
because it leaves the largest HUF balance as buffer against G2 drift.

### G5. Folio count: three, in ~5-year vintages

Monotone and saturating — 1 / 2 / 3 / 4 / 5 folios give 0.29 / 0.04 / 0.00 /
0.00 / 0.00 on the published run, reproduced here as 0.17 / 0.04 / 0.00 / 0.00 /
0.00. Cohort *length* barely matters; count saturates at three.

Constraints that argue for few, deliberate folios rather than many: AMC
consolidation rights under most SAIs, and GAAR (s.95) where the sole purpose is a
tax benefit. Three folios in 5-year blocks is well inside the guidance.

**Non-monotonicity in folio count is a bug signal, not a finding.** More folios
can never be worse, because a folio can always be ignored. It has flagged a real
defect twice.

### G6. Transition scheduling

Set the target as a fraction of the pre-withdrawal corpus **in the start year**,
then let each year take whatever headroom §5.3 leaves it and carry the balance
forward. **Instalments track capacity, not a schedule** — in (c) they run
10.62 → 9.52 → 8.65 → 8.96 → … → 4.00 → 5.12 → 2.22, rising when the statutory
ladder steps up and falling as the withdrawal's own gain grows into the headroom.
Nothing is a fixed fraction.

**The window length is the price**, and with three folios the window is generous:
zero tax for any start FY1–FY9 on the reference plan, against FY4 for a single
folio. Choose the start by looking *ahead*:

> **Start as late as possible subject to completing the full turnover at zero
> tax, with a margin.** FY8 completes at FY22; FY9 completes at FY28 with no room
> for error; FY10 never completes.

Late is right because **the model prices the switch as sell-and-rebuy at a single
NAV and so cannot see the return-path cost of straddling two allocations.** Where
the transition moves toward the *lower*-returning allocation — as it does here —
every year spent part-transitioned is a real cost the tax figure will not show.
If the transition moves toward a *higher*-returning allocation, this rule
reverses and you should start as early as capacity allows.

### G7. Turnover is roughly linear in cost, so reducing it is worth as much as spreading it

Measured, all moved in one year: 0% → 0.30, 25% → 50.38, 50% → 102.38,
75% → 154.09, 100% → 160.70. If the two allocations share funds, the true
turnover is lower than the naive difference between them, and that should be
established before scheduling anything.

---

## 8. The escalation ladder — when zero is unreachable

The model must **never fail to deliver the requirement**. If a year cannot be
funded tax-free, fund it and record the tax. A solver that under-delivers to
protect a tax figure is the B1d-ii failure in a new costume.

Spend in this order, by marginal cost:

1. **Personal headroom** — 0%.
2. **HUF nil band** — 0%.
3. **HUF `N` to `2N`** — 5%. Almost always the right place for a small shortfall.
4. **Compare, do not assume:** personal above `E` carries a ~100% marginal rate
   under marginal relief until the slab charge catches up, after which it falls
   to the slab rate. HUF above `2N` is 20%. For a *small* shortfall the HUF wins
   decisively; for a *large* one personal eventually wins. Evaluate both and take
   the cheaper — this crossover moves with `E`, `N` and the size of the shortfall,
   so it cannot be hard-coded.
5. **HUF above `4N`** — 30%. Effectively never correct while personal slab space
   exists.

Before accepting tax, exhaust the structural levers in this order, cheapest
first: raise the folio count; widen or shift the transition window; reduce
turnover; re-check G2 for deductions that widen `H_p`.

---

## 9. Calibration constants and their provenance

| constant | value | how obtained |
|---|---|---|
| `RATION_K` | 1.195 | **fitted** to reproduce the published panels. Derived optimum is 1.40 (b) / 1.50 (c) — see G4. |
| cohort threshold | 0.05 | **fitted**. Opens a new folio when the newest folio's front-lot gain fraction exceeds it. At 0.50 only two folios ever open and the ladder stalls at 1918.7. |
| `max_cohorts` | 3 | G5 |
| `exit_load_fraction` | 0.01 | `RetirementTaxPlanning.configuration` |
| `stcg_holding_months` | 12 | `RetirementTaxPlanning.configuration` |
| `CESS` | 0.04 | `configuration.cess_rate`, via `engine.compute_slab_tax` |
| `SPLIT_GRID` | 20 | resolution of the §8 step-4 escalation search |
| transition start | FY8 for (c) | G6 |

The two fitted constants reproduce five of six published targets exactly. **A
different pair reaching the same corpus figures is not ruled out**, and the
single-folio tax gap says the fit is imperfect where the ration binds hardest.
Both should be re-derived on any new parameter set rather than carried across.

---

## 10. Invariants, and the traps that have already cost time

### 10.1 Assert these every run

1. **Per-year cash identity**, to `1e-9`. Not in aggregate.
2. **Realised gain ≤ headroom, or a tax is recorded.** No silent overshoot.
3. **Monotonicity in folio count.** A violation is a bug.
4. **Both corpora non-negative in every year.**
5. **`Still to move` reaches zero**, or the transition is reported incomplete.
6. Personal `Taxbl gain` equals `H_p(t)` exactly whenever the plan is unstressed.

### 10.2 Traps

- **A spilling draw's capacity is not `cap / front_fraction`.** Computing it that
  way made the HUF redeem `band / f_cheapest` — 76, 92, 107, 122 — realising far
  more than its band and paying tax that more folios were supposed to remove.
  Symptom: tax rising with folio count. Measure capacity on a copy.
- **A gain of zero is not a harvesting opportunity.** In the HUF's inheritance
  year the front lot has zero gain, so "redeem the band's worth" is unbounded.
  Guard it, or the purse redeems itself entirely and pays 1% on the lot — 1.90 on
  a 190 windfall, which compounds to ~10 L by FY30 and shows up as a corpus gap
  with no visible cause.
- **The transition and the harvest are the same operation.** A model with no
  transition logic at all reproduces `Switch moved + Withdrawn` exactly, with
  `Re-inv fresh == Switch moved`. This is why the no-turnover control and the
  capacity-driven 25% run both report 0.19 / 1934.1: they are the same arithmetic
  under two labels. Do not read the agreement as coincidence or as validation.
- **Predict before running.** A wrong prediction is information; an absent one is
  not.
- **Truncated output is not evidence, and unbounded output is worse.**

---

## 11. Reference results

Reference plan: personal corpus 350.0 at FY1; requirement 35 / 37.5 / 42.5 / 45 /
47.5 / 50 in five-year steps; other income 15.80 at FY1 with the increment ramping
0.50 → 0.15 linearly over 29 steps; assessable share held at 5.95/15.80; HUF
inherits 190.0 at FY5 with no growth that year, 7.0% thereafter; personal exempt
limit 12 → 14 → 16 → 18 → 20 → 20; HUF nil band 4 → 5 → 6 → 7 → 8 → 9.

| | (b) 1-chunk 7.25% | (c) 2-chunk 7.5→7.0, 25% turnover |
|---|---|---|
| lifetime tax | **0.00** | **0.00** |
| personal at FY30 | 1737.5 | 1751.6 |
| HUF at FY30 | 182.7 | 182.7 |
| total | **1920.2** | **1934.3** |
| folios opened | 3 personal, 3 HUF | 3 personal, 3 HUF |
| transition | — | FY8 → FY22, 120.4 moved |

**The HUF panel is identical in (b) and (c), row for row.** With three folios the
personal purse covers its residual comfortably in both, so the HUF never sees the
difference; the entire 14.1 L gap lives in the personal panel. That gap is the
**return path** — 7.5% falling in the early years when the corpus is largest
relative to withdrawals — not the chunking, and not the transition, which is free.

---

## 12. What this model does not capture

- **The return-path cost of straddling two allocations** during a transition.
  End corpus is 1934.3 for every start year FY1–FY9, because turnover is priced
  as sell-and-rebuy at one NAV. G6's start-year choice rests on reasoning about
  this cost, not on a measured number.
- **Real transitions move between funds with different NAVs and return paths.**
- **No Monte Carlo anywhere.** §5.3's margin is the only thing between a zero and
  a tax bill if the inputs drift, and it is a point estimate.
- **HUF slabs above the nil band are extrapolated** from the old-regime structure.
  Exact at `N = 2.5`, a projection at `N = 9`.
- **The assessable share is held constant.** G2 says this is the most important
  input in the model, so holding it constant is the most consequential
  simplification in it.
- **Monthly timing.** Everything is annual; a real SWP is monthly, and the
  12-month holding test is knife-edge at the boundary.
- **Transaction costs other than exit load**, and any AMC-level constraint on
  operating three folios.
- **The tax treatment is not advice.** s.64(2), CBDT Circular 704, GAAR exposure
  on deliberate folio separation, and the HUF's slab treatment all need a CA.
