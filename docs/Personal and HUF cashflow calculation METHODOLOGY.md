# METHODOLOGY — two-purse SWP tax minimisation

**Part I (§0–12)** — how the standalone model works, why each rule is the
rule, and how to apply it to inputs it has never seen. Validated: it
reproduces every published reference figure.

**Part II (§13–20)** — the ENGINE: what it actually does today, what has been
measured on it, and every remaining phase in implementation detail. Read
Part II first if you are here to write code.

Companion to `HANDOVER_merged.md`.

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

### G7. Turnover sets the window LENGTH, not the cost

**Corrected.** This section previously said cost was roughly linear in
turnover and concluded that reducing turnover was worth as much as spreading
it. That is false, and the evidence against it was already in §7 G6.

Linearity holds only when the whole move is **compressed into one year**:
0% → 0.30, 25% → 50.38, 50% → 102.38, 75% → 154.09, 100% → 160.70.

Spread against capacity the cost is ~0 at **every** turnover. Measured across
windows, with the fraction of the target that actually completes in brackets:

| window | even instalments | capacity-driven |
|---|---:|---:|
| FY16 only | 50.38 | **0.19** (3% moved) |
| FY12–20 | 23.91 | **0.19** (25% moved) |
| FY8–20 | 14.93 | **0.19** (54% moved) |
| FY6–20 | 7.55 | **0.19** (77% moved) |
| FY4–26 | 1.06 | **0.19** (100% moved) |

The capacity-driven column does not move. **A capacity-driven transition never
pays tax; the window decides whether it FINISHES, not what it costs.** Doubling
turnover does not double the bill — it doubles the years needed to complete at
the same bill.

Therefore: **do not reduce turnover in order to reduce tax.** Reduce turnover
only to shorten the window, and the reason to want a shorter window is the
real, untaxed cost of sitting between two allocations (§12) — not the tax.

Establishing the true turnover still matters, because two allocations that
share funds need a shorter window than their naive difference suggests.

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

---
---

# PART II — THE ENGINE

Part I describes a standalone model that reaches zero tax. Part II describes
the application, which does not, and exactly what stands between them.

---

## 13. What the engine does today

**HEAD `7a1daa5`, 804 tests, ruff clean.** Both tax-minimisation gates are at
zero, so **the engine computes the same numbers it computed before any of this
work started.** Six patchers have landed; none has moved a snapshot.

| lever | built? | live? | why not |
|---|---|---|---|
| vintage folios | yes, tested | **no** | `folio_cohort_threshold = 0.0` → one folio per fund |
| HUF ration | yes, tested | **no** | `huf_ration_k = 0.0` → `huf_withdrawal_chunks` still drives (`engine.py:1173`) |
| capacity-driven transition | **no** | no | retired in `bfbf226`; nothing replaced it. Boundary is a hard cut |
| personal water-fill on slab headroom | **no** | no | `_execute_ltcg_harvest` targets the *equity LTCG exemption*, not slab headroom |

### What has landed, and what each is for

| patch | commit | content |
|---|---|---|
| 01 | — | `PlanSolution.overdeliveries` + delivery-precedence guard. **Found the defect in 05.** |
| 02 | — | `free_gain_capacity(chunk, entity, assessable_other, gain_realised)` — per-entity tax-free headroom |
| 03 | `9e2d058` | `cash_within_gain_budget(bucket, nav, gain_budget)` + `_cash_consumption_order` |
| 04 | `5ba659c` | `_huf_ration` / `_huf_annual_wd`, gated |
| 05 | `7a1daa5` | ration ceiling at the year's demand — a defect fix |
| 06 | `cfae423` | scoped `test_the_optimiser_resolves_income_inside_the_fy_loop` |

The two primitives from 02 and 03 are the shared machinery every remaining
phase needs. They are tested and live; only their callers are missing.

---

## 14. Measurements on the engine — the evidence base

Everything here came from a run. The harness is two throwaway scripts;
**commit them** (§20).

### 14.1 The tax is concentrated in a handful of years

| scenario | lifetime tax | where it lands | lifetime free headroom |
|---|---:|---|---:|
| `s0_1chunk` | 0.00 | — | 564.1 |
| `s1_2chunk` | 74.78 | **FY16 56.20** (46.10 rebalancing) + FY17 6.05 | 564.1 |
| `s2_3chunk` | 51.61 | boundary years | 564.1 |
| `s3_4chunk` | 106.76 | **FY9 8.47, FY24 66.02, FY25 13.50, FY26 1.31** | 564.1 |
| `hist_8yr` | 40.90 | boundary years | 564.1 |

**Twenty-six to twenty-eight years out of thirty pay exactly zero.** The whole
problem is two to four years wide. Against 564.1 of lifetime free headroom,
the capacity to absorb it exists several times over — **zero tax is reachable
in principle**, which is what Part I §7 G7's window table already implied.

### 14.2 Not every boundary is expensive

`s3_4chunk` has three boundaries and one of them — FY17 — costs **nothing**.
Its two allocations overlap heavily, so true turnover is near zero. A
scheduler that opens a window at every boundary would churn for no reason.
**Size each boundary from the actual fund-list difference.**

### 14.3 The HUF ration, measured on all five — it makes things WORSE

| scenario | tax k=0 | tax k=1.2 | Δtax | corpus k=0 | corpus k=1.2 | Δcorpus |
|---|---:|---:|---:|---:|---:|---:|
| `s0_1chunk` | 0.000 | 0.167 | +0.17 | 1708.6 | 1708.8 | +0.2 |
| `hist_8yr` | 40.898 | 41.582 | +0.68 | 1760.5 | 1741.8 | −18.7 |
| `s1_2chunk` | 74.784 | 77.692 | +2.91 | 1211.7 | 1157.1 | −54.6 |
| `s2_3chunk` | 51.614 | 55.349 | +3.74 | 1304.0 | 1252.6 | −51.4 |
| `s3_4chunk` | 106.763 | 113.094 | +6.33 | 1339.2 | 1270.7 | −68.5 |

Five of five worse on tax; four of five worse on corpus. Across the four
`main` scenarios the damage rises monotonically with chunk count — 0.17, 2.91,
3.74, 6.33 for 1, 2, 3, 4 chunks.

No gentler setting helps. On `s1_2chunk`, k = 0.0 / 0.3 / 0.6 / 1.2 gives tax
74.784 / 75.688 / 76.409 / 77.692 and corpus 1211.7 / 1197.2 / 1179.9 /
1157.1 — **monotone in both, in the wrong direction.**

**Mechanism.** HUF tax falls in every scenario (the ration works). Personal
tax rises by more. Drawing the HUF harder leaves the personal corpus *larger*
at each boundary, and boundary rebalancing tax scales with the corpus
reallocated. There is also no return arbitrage to collect here: both purses
hold the **same funds at the same returns**, unlike Part I §7 G4 where the HUF
returns 7.0% against personal 7.25–7.5%.

**Therefore the ration is blocked on the transition, not the other way round.
This reverses `HANDOVER_merged` §4.2 / §4.3.**

### 14.4 The fixtures are not the live plan

`tests/golden/` carries monetary fields scaled down from an **older** plan —
FY1 requirement 20 against the live 35, HUF windfall 185 against 190 — with
the statutory bands left unscaled. Their tax-free headroom is therefore
generous relative to their portfolio in a way the live plan's is not.

**They can never reproduce Part I's 0.00 / 1934.3 and must not be expected
to.** They are behavioural pins: use them to detect unintended change, never
to size anything. Comparing engine output against Part I needs a **sixth
scenario carrying the live parameters** — corpus 350, requirement 35→50,
other income 15.80 ramping, HUF 190 at FY5 — added as a *comparison* fixture,
not a regression pin. Do that after Phase 07, when there is something worth
comparing.

---

## 15. Phase 07 — capacity-driven transition. **LIVE. Do this first.**

The dominant term by an order of magnitude: 46.10 of rebalancing tax inside
FY16 on `s1_2chunk`, 48.23 inside FY24 on `s3_4chunk`.

### 15.1 The site

| what | where |
|---|---|
| personal boundary rebalance | `engine.py:1435` — `old_chunk_fy = fy - 1`, `old_funds = state.get_funds_for_year(old_chunk_fy)` |
| HUF mirror | `engine.py:2058` — `old_chunk_fy_h`, `old_funds_h` |
| dead residue to remove | `glide_costs_by_fy` — initialised `~1358`, read `~2482`, **never written** |
| available, no callers since `bfbf226` | `FolioSet.sell_to_tax_budget`, `FolioSet.pick_for_harvest` |
| available, landed by 02/03 | `free_gain_capacity`, `cash_within_gain_budget` |

`glide_path.py::build_glide_path` exists but its only callers are in
`test_swp_engine.py`. Nothing on the live path builds or consumes a glide
path. Phase 07 builds into an empty socket.

### 15.2 The algorithm

**Per boundary N**, before the run starts:

1. **True turnover** = value that must actually move, from the difference
    between the old and new allocations' fund lists and target weights. Not
    the naive "everything changes". If it is ~0, **open no window** (§14.2).
2. **Window** `N−M … N+M`. Choose the **smallest M that completes at zero
    tax**, not the M that minimises cost — cost does not rise with window
    length (Part I §7 G7). Search M upward from 0. The window may be
    asymmetric; nothing requires N−M and N+M to be equal, and the reference
    run used FY4–26 for a boundary at 16.

**Per year, in this order** — this ordering is not cosmetic (Part I §5.3):

3. **Ordinary withdrawal** takes its headroom first.
4. **Transition** takes what remains, capped by what is still to move.
5. **Harvest** takes whatever is left after that.

**Where windows overlap** — `s3_4chunk` has three boundaries in 30 years —
shared headroom is **allocated nearest-deadline-first and spent once**, never
offered to each window. Double-spending produces a scheduler that reports
success while over-committing, which is the B1d-ii shape.

6. **Carry the balance forward.** Instalments track capacity, not a schedule.
    In the reference run they went 4.15, 10.26, 6.96, 5.44, 2.41, 1.20.

### 15.3 Predicted diff — write this down before running

**Amended after measurement — patchers 07a–07e, HEAD `725bb46`.** The original
prediction assumed windows could open anywhere. They cannot. §15.2's policy is
now fixed: windows are centred on N per `N ± (M ± m)` and hard-capped so a
window never crosses a neighbouring boundary. Each chunk's portfolio is the
minimum-risk portfolio achieving THAT chunk's target return, so it only does
its job while actually held during that chunk; a window running past a
neighbouring boundary is a mandate breach, not a tax optimum. What follows is
the measured consequence.

| scenario | tax now | after 07 | boundary | M | window |
|---|---:|---:|---|---:|---|
| `s0_1chunk` | 0.00 | 0.00 | — | — | one chunk, nothing to schedule |
| `s1_2chunk` | 74.78 | ~0 | FY16 | 9 | FY7–25 |
| `s2_3chunk` | 51.61 | ~0 | FY11 | 5 | FY6–16 |
| | | | FY21 | 4 | FY17–25 |
| `s3_4chunk` | 106.76 | **~25.5** | FY9 | 2 | FY7–11 |
| | | | FY17 | 0 | FY17 |
| | | | FY24 | 7, clamped | FY17–30 |
| `hist_8yr` | 40.90 | ~0 | FY16 | 6 | FY10–22 |

Four corrections to the original prediction:

1. **`s3_4chunk` FY24 does NOT reach zero.** M clamps at 7 against its band of
   FY17–30, funding 127.2 of the 237.2 it must realise and leaving **110.0
   unfunded** — 79.4 personal, 30.6 HUF. Priced, that residual costs 30.83
   dumped in one year and **25.51 spread across the band**. Treating
   "FY24 → ~0" as an acceptance test would fail a correct implementation.
2. **FY17 does open a window, at M = 0.** The original said no window should
   open there. It books 11.4 of gain on 1.1% turnover and absorbs all of it
   within its own year. The correct pin is "no window WIDER than M = 0", and
   the general rule is not "turnover ≈ 0" but **"year N covers its own gain"**,
   which needs no threshold.
3. **Headroom is per purse and is not fungible.** The personal and HUF
   rebalances are separate operations — `engine.py:1435` and `2058` — each
   bounded by its own entity's bands. Pooling them understates the window:
   `hist_8yr` FY16 needs M = 6, not the M = 5 a pooled calculation reports.
4. **The residual is spread, never dumped.** Part I §8: once tax is
   unavoidable it is still a choice where to pay it. Almost all of the 5.3
   saving is the HUF — 5.89 → 1.59, a narrow nil band rewarding thin annual
   slices — while personal moves only 24.94 → 23.92.

Unchanged from the original: total corpus rises materially in `s1`, `s2`,
`s3` and `hist_8yr`; `s0_1chunk` does not move; all ten snapshots move.

**Every figure here is a first-order screen on TODAY's corpus path, not a
prediction of the post-fix run.** Spreading a transition changes the
withdrawals, which changes the spare profile. The FY24–26 zeros in the spare
column are the current hard cut's own damage, and some of that will reappear
as capacity once the cut is gone.

**Falsifier: if any scenario's lifetime tax rises, it does not ship.**

### 15.4 Tests it needs

- a window completes its turnover by its end year
- a zero-turnover boundary opens no window (pin `s3_4chunk` FY17)
- overlapping windows spend shared headroom once — construct two boundaries
   whose windows overlap and assert total gain ≤ total headroom
- the withdrawal's own gain is charged before the transition's
- per-year cash identity holds; `overdeliveries` stays empty
- instalments are not a fixed fraction

---

## 16. Phase 08 — personal water-fill on slab headroom. **LIVE.**

### 16.1 What is wrong now

`_execute_ltcg_harvest` budgets against `ec_harvest.exempt_limit` — the
**equity LTCG exemption**, ~1.25–2.50 — and iterates `eq_fund_names` only.
The fixtures and the live plan are debt-heavy, and `engine.py:1224` confirms
debt gains are what get taxed at slab (`total_slab_income = assessable_other +
gain_d`). **So the personal harvest does nothing on this plan at all.**

### 16.2 What it should be

Budget = `free_gain_capacity(state.get_debt_tax_chunk(fy, "individual"),
"individual", assessable_other + gain_d_already_realised)`, iterate **debt**
funds, and book the realised gain into `accumulator["gain_d"]` — not
`gain_e`, which is a different tax regime entirely.

Keep the existing step-basis-in-place mechanic (`Lot(lot.units, nav, m)`):
it avoids the exit-load question and is equivalent to sell-and-rebuy at one
NAV. Keep the `stcg_holding_months` guard.

Runs in March, **after** the year's withdrawals are known, so the withdrawal's
own gain has already consumed its share (Part I §5.3, §7 G6).

### 16.3 Expected effect

Raises personal cost basis, lowering the late-plan gain fraction from ~88%
toward ~64% (Part I §5.3 measured this as the real prize: margin, not
headline tax). On its own it may move lifetime tax little — headroom is
largely unused in non-boundary years anyway — but it is what makes Phase 09
worth anything.

---

## 17. Phase 09 — raise `folio_cohort_threshold`. **LIVE.**

One-line config change; the entire diff is snapshot movement.

- **Measure the threshold on the fixtures.** Do not carry 0.05 across from
   Part I §9 — that was fitted to the standalone model's drawdown shape. The
   engine docstring's "0.50 settles on three, 0.10 on five" disagrees with the
   model's "0.50 opens two", which is itself evidence the dial is
   workload-dependent.
- **Monotonicity is the guard.** More folios can never be worse; a folio can
   always be ignored. Non-monotonicity means a bug (Part I §10.2).
- **Do this after 08.** Folios park fresh basis where a cash draw will reach
   it; without a water-fill creating that basis they have little to place.

---

## 18. Phase 10 — raise `huf_ration_k`, delete `huf_withdrawal_chunks`. **LIVE, conditional.**

- **Re-measure after 07.** §14.3 measured it worse on five of five, and the
   mechanism was boundary tax. Phase 07 removes the boundary tax. Whether the
   ration then pays is an open empirical question — do not assume either way.
- Sweep k lexicographically: minimise shortfall, then tax, then end corpus
   (Part I §7 G4). Expect a plateau, not a point.
- If it is still worse, **it does not ship**, and that is recorded as a
   finding rather than worked around.
- **B1d-iii lands with it**: delete `huf_withdrawal_chunks` (nine files),
   archive `optimize_withdrawal_split` (`engine.py:2564`) and
   `allocate_funds_v0.9.py`. Blocked until the ration replaces them.

---

## 19. Independent of the above

None of these block or help Phases 07–10. Do them when convenient.

| item | size | note |
|---|---|---|
| **Phase E** — `build_sensitivity_table` hardcodes `QTableWidget(30, …)` | one line + test | **live defect**: at horizon 40 the table drops FY31–40, at 20 it shows ten blank rows. Reachable today |
| **Phase D** — Monte Carlo horizon | bounded | `MC_FIXED_HORIZON = 30`; `test_gui_horizon` asserts it and that no module carries a bare `range(30)` — those tests are the spec |
| **Phase F** — investment profile, 0–36 month staggered deployment | feature | staggered deployment is itself a vintage-generating mechanism, so it creates folio separation at inception free |
| exit load | decision | structurally zero on every live path; `exit_load_fraction` in `UNUSED_KEYS`. Whether an SWP redemption inside `stcg_holding_months` should bear one is unresolved |
| audit | small | `glide_costs_by_fy` residue; `best_cash_split` orphaned; `dialogs.py` labels "1–30" against a ceiling of 40; no `LICENSE`; `allocate_funds_v0.9.py` at root |
| exporter | small | the bundle picks up new files under `tests/` but **not** new directories such as `docs/` |

---

## 20. Process — what has actually worked, and what has not

### 20.1 Rules that earned their place

1. **Measure the lever on the fixtures BEFORE building it.** Four patchers
    went into the HUF ration before a ten-minute measurement showed it could
    not pay until the transition existed. The harness that showed it could
    have been run first.
2. **Validate on all five scenarios, never one or two.** B1d-ii was declared
    benign on `s0_1chunk` alone and the other four got worse. The ration was
    later declared harmful on two before the other three were checked. Same
    error, both directions.
3. **Run the WHOLE suite before emitting a patcher.** Patcher 05 was verified
    against `tests/golden` plus its own new file; it broke a test 1500 lines
    away in `tests/test_other_income_growth.py`.
4. **Predict the diff before running it.** A wrong prediction is information.
    Of five predictions made for the ration gate, two were wrong and one
    outcome — over-delivery — had not been predicted at all.
5. **Derive hunks mechanically from a verified tree and prove they reproduce
    it byte-for-byte before embedding.** Never hand-write a hunk.
6. **Bound every command's output.** A single unbounded regex dumped the
    entire fixture corpus into a context window.

### 20.2 Gating: useful once, then over-applied

`folio_cohort_threshold = 0.0` let a large representation change land provably
inert, and that was correct. Applying the same pattern to the ration produced
a mechanism that shipped, was tested, and computed nothing.

**From Phase 07: no gates.** Build it, measure it, turn it on in the same
commit, review the predicted diff. If the numbers get worse it does not ship.

### 20.3 The measurement harness — commit it

Two scripts did all the work in §14 and neither lives in the repo:

- **`review.py`** — runs `project_plan` on one scenario at a chosen
   `huf_ration_k`, reporting lifetime tax split by entity, FY30 corpora, HUF
   cash by year, shortfall, over-delivery, convergence.
- **`probe7.py`** — runs one scenario and reports, per FY, `tax_personal`,
   `rebalance_tax_paid` and the free headroom of both entities, plus lifetime
   totals.

Both are ~30 lines. **They belong in `scripts/` under version control.** Every
Phase 4 verification lived in a throwaway script and had to be rebuilt; that
is `HANDOVER` §9.14's second half and it has now happened twice.

### 20.4 Patcher house style

SHA-256 pin before and after; anchors validated before a byte is written;
post-hash embedded so the patcher proves its own output; `--check`,
`--dry-run`, `.bak`, `py_compile` after. **No `--revert`** — `git checkout --`
is the recovery mechanism. Assert exactly one substitution per hunk. Embed
payloads as **base64**, never as `repr()` inside a triple-quoted template —
that fails to parse and has done.

`git add` with explicit paths, never `-A`. The `.gitignore` is an allowlist
whose line 2 is `*`; the reliable check that a new file is admitted is `??` in
`git status --porcelain -uall`.
