# HANDOVER REVISION — evidence, method, and outstanding audit items

Companion to `HANDOVER.md`. The two have different jobs and should not be
merged:

- **`HANDOVER.md` §4** states what is true and what to build. Read that first.
- **This document** carries the measurements behind it, the approaches that
  failed and why, and the codebase audit items not yet actioned.

Everything about the `fd_tax` / HUF-transfer term has been removed. That whole
direction is void: under the scheme now adopted, tax savings stay in the
personal purse and are never transferred to the HUF, so there is no transfer
term in the cash identity, no `max(0, ...)` floor, no piecewise regime, and no
cancellation of tax out of cash in hand. The identity is simply

    net cash in hand = W_personal + W_HUF + other income - tax_p - tax_h

Tax reduces cash in hand, always, monotonically. Minimising tax and easing
withdrawal pressure are one goal, not two that trade off by year.

---

## 1. The model, and why the identity check matters more than the tax total

All figures come from a standalone model, not the engine. It holds FIFO lots as
`(units, basis_nav, buy_year)`, grows NAV annually, solves each year's
withdrawal against its own tax by fixed point, and applies exit load to lots
under `stcg_holding_months`.

Every run verifies, in every year:

    withdrawals + other income - tax - exit loads  ==  the stated requirement

**This caught two bugs that the tax totals hid completely.**

- A two-purse split that delivered 44.64 against a requirement of 42.5 from
  FY12 onward. The tax figure looked excellent precisely *because* it was
  over-delivering.
- A transition scheduler whose switch gain was not fed back into the
  withdrawal solve, so the year's tax exceeded what the withdrawal had been
  grossed up for and cash fell short.

Both are the same shape as B1d-ii's `converged = not shortfalls`: a solver
reporting success while failing the constraint it exists to satisfy. **Any
successor must carry this check per-year, not in aggregate.** A lifetime total
can be right while individual years are wrong in both directions.

A third bug was caught by a different signal: the folio-count sensitivity came
back non-monotonic (4 folios worse than 5, 5 worse than 6). More folios cannot
be worse, since a folio can always be ignored. Chasing it found that a probe
routine was appending a folio and the snapshot/restore was not removing it,
duplicating value. **Non-monotonicity in a parameter that should be monotone is
a bug signal worth acting on.**

## 2. Parameters

The live plan, not the scrubbed fixtures. All Rs lakh, 30 years.

| | |
|---|---|
| personal corpus | 350 at FY1 |
| requirement | 35 (FY1-5), 37.5, 42.5, 45, 47.5, 50 (FY26-30) |
| other income | 15.8 at FY1, increment ramping 0.50 -> 0.15 per year |
| assessable share of it | held at 5.95/15.8 |
| HUF | inherited 190 at FY5, 7.0% from FY6 |
| personal exempt limit | 12 -> 14 -> 16 -> 18 -> 20 -> 20 |
| HUF nil band | 4 -> 5 -> 6 -> 7 -> 8 -> 9, **old regime**, no s.87A |
| return paths | 1-chunk 7.25%; 2-chunk 7.5% then 7.0% |

The two return paths compound almost identically over 30 years
(1.0725^30 = 8.1643 vs 1.075^15 x 1.07^15 = 8.1636), which makes them directly
comparable. Any end-corpus difference between them is **timing**, not
chunking: 7.5% falls in the early years when the corpus is largest relative to
withdrawals, so fixed-rupee withdrawals bite a smaller fraction.

## 3. The measurements

### 3.1 Levers, 1-chunk

| configuration | lifetime tax | end corpus |
|---|---|---|
| personal purse only, no harvesting | 69.67 | — |
| both purses, greedy per-year split | 6.55 | — |
| both purses, greedy split, single-folio harvesting | 16.47 | 1914.4 |
| both purses, **rationed** split, 1 folio | 0.29 | 1917.9 |
| both purses, rationed split, 2 folios | 0.04 | 1919.3 |
| both purses, rationed split, **3 folios** | **0.00** | 1920.2 |
| 4 or 5 folios | 0.00 | 1920.0 |

### 3.2 Chunk transitions, 2-chunk, 25% turnover at an FY16 boundary

| when the 25% moves | tax | end corpus |
|---|---|---|
| all in FY16 | 50.38 | 1804.6 |
| evenly over FY12-20 | 23.91 | 1872.2 |
| evenly over FY6-20 | 7.55 | — |
| **capacity-driven, FY4-26** | **0.19** | **1934.1** |
| control: no turnover at all | 0.19 | 1934.1 |

The last two rows are the finding: a capacity-driven transition is **free** to
one decimal on both tax and corpus.

Turnover sensitivity, all moved in one year: 0% -> 0.30, 25% -> 50.38,
50% -> 102.38, 75% -> 154.09, 100% -> 160.70. Roughly linear in turnover, which
is why reducing turnover is worth as much as spreading it.

### 3.3 Everything combined

2-chunk, 25% turnover, capacity-driven FY4-26, rationed HUF, by folio count:
2 folios 0.04 / 1934.2; **3 folios 0.00 / 1934.3**; 4 and 5 folios identical.

Combining the folio work with the transition work gains almost nothing
(0.19 -> 0.00, 1934.1 -> 1934.3). **They do not add.** Both draw on the same
annual headroom.

## 4. What did not work, and why — the more useful half

### 4.1 Single-folio harvesting is worse than not harvesting

16.47 with it, 6.55 without. Not merely useless.

The gain fraction on withdrawals was **identical in every year** with and
without: 7%, 13%, 30%, 50%, 65%, 75%, 83%, 85%, 86%, 87%, 88%. Selling the
oldest units and rebuying appends the fresh-basis units to the *back* of the
FIFO queue, while the front remains the rest of the same original lot at the
same basis. Over 30 years only ~200 of the original 350 units are ever
consumed, because the corpus grows faster than it is drawn — so the front never
advances past original-basis stock.

Measured directly: harvesting realised 54.6 of *additional* tax-free gain over
30 years, and the gain from withdrawals moved from 390.5 to 390.2.

It was also actively harmful, because it shifted the greedy split and drained
the HUF two to four years earlier.

### 4.2 The existing glide path smooths brackets; it does not harvest

Measured on the golden fixtures, with a glide path built by
`build_glide_path(chunks, spread_years)`:

| | FY14 | FY15 | FY16 | FY17 |
|---|---|---|---|---|
| unglided tax | 0.00 | 0.00 | 54.21 | 3.19 |
| glided tax | 4.98 | 5.77 | 6.58 | 7.09 |

It takes two years that cost **zero** and makes them cost 10.75. Lifetime tax
does fall (57.40 -> 24.42) but purely by spreading across progressive slabs,
not by using free capacity. FY14 and FY15 each had roughly 21 of unused
headroom and it realised gain straight past it.

Its per-year budget is `total_transition_tax / spread_years` — an even split of
the tax *bill*. What is needed is each year's remaining tax-free *headroom*.
Replace, do not repair.

### 4.3 Early harvesting is nearly worthless

Harvesting in FY1 steps basis from NAV 1.000 to 1.073 — a 7% step. Held to
FY16 that gives a 65% gain fraction instead of 67%.

Headroom is largest early, when the withdrawal is small; the step-up is worth
least early, when NAV has barely moved. **They are anti-correlated.** Do not
assume more harvesting is better, or that harvesting earlier is better.

The churn required makes this vivid: realising 6.05 of gain in FY1 needs
89.5 of units sold — a quarter of the corpus — for that 7% step. By FY20 the
same headroom needs only 2.40 of churn.

### 4.4 A greedy per-year split is the wrong policy

Covered in `HANDOVER.md` §4.2. The detail worth keeping: under the greedy rule
the HUF emptied at FY25 (multi-folio), FY27 (single-folio) or FY29 (no
harvesting), and *every* rupee of late-plan tax traced to the years after it ran
dry, when the personal purse carried everything alone at an 85-88% gain
fraction.

## 5. Multi-folio detail

### 5.1 The two orderings

Within a folio FIFO is forced, so the only lever is which folio. The rules point
opposite ways:

- **cash withdrawal** -> lowest front-lot gain fraction (minimise taxable gain
  per rupee raised);
- **harvest and transition** -> highest front-lot gain fraction (maximise gain
  realised per rupee churned; retire the units that cost most later).

A single "pick the cheapest" scan implements half of this and gets the wrong
half.

### 5.2 How many, and how long a cohort

Monotone and saturating at three: 0.29 / 0.04 / 0.00 / 0.00 / 0.00 for one
through five. Cohort length barely matters (3-year and 5-year blocks give the
same answer), so take 5-year blocks — fewer folio openings, further from
anything resembling an artificial arrangement.

### 5.3 The real prize is margin, not headline tax

Personal gain fraction at FY30: **64% with three folios, 88% with one.**

Without it the zero sits exactly on the exemption line. Worked example, FY30:
withdraw 27.81 with 24.40 of gain (88%), add 9.5 assessable other income, and
you are at 33.9 against a limit of 20 — tax. With fresh basis, withdraw 24.77
with 10.50 of gain (42%), add 9.5, and you are at 20.00 — exactly on the line,
zero tax, no buffer.

A rupee more of income, requirement, or return tips it over. The folios buy the
buffer. That is what they are for.

### 5.4 Legal basis and practical constraints

Capital gains are computed FIFO **per folio**, not across a PAN. CBDT Circular
704 (1995) establishes FIFO; the account-level boundary is what makes each
folio an independent ledger. Redeeming from a younger folio is tax planning.

Three constraints:

- **AMC consolidation.** Most SAIs reserve the right to consolidate folios
  under the same PAN, name and bank account. Few deliberate folios, possibly
  across different platforms.
- **GAAR (s.95).** A theoretical risk where the sole purpose is a tax benefit.
  Meaningful 3-5 year cohorts, not monthly micro-folios. Three folios in 5-year
  blocks is well inside the guidance.
- **Ledger per cohort** in the engine, not one pool per fund.

## 6. s.64(2) — current position and what would re-open it

Income from property a member transfers to his HUF is clubbed back into the
member's income rather than taxed at HUF rates. The whole strategy rests on the
HUF's bands being genuinely separate, so this is load-bearing.

**Currently resolved favourably, on two independent grounds:**

1. The 190 windfall is inherited **directly by the HUF under a will**. It comes
   from the testator, not from a member, so s.64(2) does not attach and neither
   does s.56(2)(x).
2. Under the scheme adopted, tax savings **stay in the personal purse**. No
   member capital enters the HUF at all, so no tainted layer is ever created.

**What would re-open it:** any member contribution to the HUF, in any form.
Three points considered and worth recording:

- A transfer is **capital, not income** — there is nothing to club in the
  transfer year. The "income on income" exception applies to the second tier
  (earnings on already-clubbed earnings), not to earnings on the transferred
  corpus itself, which on the ordinary reading stay clubbed for as long as the
  HUF holds them.
- Not selling is **deferral, not extinction**. Gain accrues inside the units
  and is realised whenever they are eventually redeemed.
- Routing gifts through adult daughters relocates the taint rather than
  removing it — since 2005 a daughter is a coparcener, so s.64(2) attaches to
  her. It works only if her marginal rate is lower, it burdens her with tax on
  income she may never receive, and a pre-arranged annual chain invites
  substance-over-form scrutiny.

If a member transfer is ever contemplated, put it to the CA **first**, and be
aware the model would then need to track transferred-in capital separately from
the HUF's own accretions, by vintage. Structural change, not a parameter.

## 7. Codebase audit items still outstanding

### 7.1 The golden fixtures are scrubbed, and the tax bands are not

`tests/golden/golden_projects.py` opens:

```
# Generated by convert_golden_fixtures.py — do not hand-edit.
# Structurally faithful, numerically scrubbed copies of the golden
# scenarios. All monetary fields scaled by 0.6137; fund names
# generalised. Statutory tax chunks left intact.
```

Every fixture value divides by 0.6137 to an exact round number: corpus 350,
FY1 requirement 20, rent 7, pension 1.8, windfall 185. Meanwhile the 12 exempt
limit, the 4 HUF nil band, the 1.25 LTCG exemption and the flat 0.75 standard
deduction are unscaled.

The portfolio is 61% of life-size against full-size bands, so tax-free headroom
is systematically **overstated** relative to the gain being harvested. The
fixtures are fine as behavioural pins — every snapshot is a valid regression
guard. They are **not** usable for sizing anything driven by headroom, which is
the entire subject of §4.

Decide one of: scale the statutory chunks by the same factor in the generated
fixtures (changes every snapshot; own commit, full review), or keep them
scrubbed for snapshots and size against the live plan locally. Either way it
should be recorded rather than left implicit.

Related: `tests/golden/conftest.py`'s module docstring still describes
`Uddipan_project.swp.json` and "the originals under `Uddipan/`", which predates
the scrub; and `FIXTURE_DIR` at line 34 is now dead (only `mc_history.json`
lives there, and `test_mc_isolation.py` computes its own path). `ruff`'s F841
covers locals, not module-level names, so Phase 2.3 will not catch it.

### 7.2 RESOLVED — the glided engine path

The glide-path transition scheduler was retired in `bfbf226`, so there is
nothing left to pin. Measured before removal, it took two years costing ZERO
tax and made them cost 10.75 (`s1_2chunk` FY14-15): it smoothed gain across
progressive slabs rather than using free capacity, while ~21L of headroom sat
unused in each of those years. Its per-year budget was
`total_transition_tax / spread_years` — an even split of the tax BILL, where
what is needed is each year's remaining tax-free HEADROOM.

341 lines removed across three files and **no snapshot moved**, because the
goldens pass `glide_path=None` and none of that code ever executed under test.
Two live defects went with it by deletion rather than repair: `min(...)` where
`max(...)` was meant, so with three boundaries the HUF throttled its FY24
transition against the FY9 budget; and the `ValueError` that expression raises
when the dict is non-empty but every key exceeds `fy`.

### 7.3 RESOLVED — and it was worse than an inconsistency

Recorded here as "`tax_saved` uses the full personal tax while `net_p` debits
only `swp_only_tax` — two quantities on two sides of one identity." True, but
it understated the problem. **The transfer was debited twice, and a rupee a
year was destroyed.**

Per year, with `tax_saved = X`:

| | |
|---|---|
| departing the personal corpus | `W + X` — the withdrawal, plus the transfer debit |
| arriving with the user | `W + OI - X` — `net_p` subtracted `this_fy_saved` |
| arriving in the HUF | `X` |
| arriving from outside | `OI` |

Arriving: `W + OI`. Departing: `W + X + OI`. **`X` unaccounted, every year.**
The solver withdrew an extra `X` so the user still received the requirement,
and the engine separately debited `X` as the transfer — each defensible alone,
both together charging the user for a corpus-to-corpus movement that never
touched their cash.

`s0_1chunk` at FY30: personal `937.891 -> 1162.282` (+224.39), HUF
`656.255 -> 546.309` (-109.95), total `1594.146 -> 1708.591` (+114.44). The HUF
loses the ~23L of transfers it no longer receives, compounded; personal gains
twice that, because it stops sending the transfer AND stops paying the
duplicate. The +114 is the destroyed stream recovered.

**The guard that should have caught it still cannot.**
`test_conservation.py::test_monthly_corpus_flow_balances` asserts
`corpus_end == corpus_start - withdrawal`. Both outflows were recorded as
withdrawals, so its residual stayed at 5.7e-14 the whole time. It balances the
corpus against *itself* and never crosses the boundary between the corpus and
the user's hand — which is where the money was going missing. Its docstring
claims it catches "exactly how the tax and HUF-transfer leaks manifested." It
does not, and nothing else in the suite does either.

**Write this test before Phase 4**, which rewrites how withdrawals are chosen:

    what leaves both corpora, plus other income
      == cash delivered + tax paid + transfers out

### 7.4 Ad-hoc inflow / outflow tables

`WindfallEntry` already carries `target: "personal" | "huf"`, `WindfallDialog`
offers both, and `engine.py:1615` invests a personal windfall. So the **inflow**
side exists for both purses. Missing:

- `engine.py:1621` `if amt > 0` and `engine.py:2180` `if amt_h > 0` discard
  negative amounts silently;
- both branches end in `FIFOBucket.invest(...)`. **An outflow is not a negative
  inflow** — it must consume lots, realise gain into the year's accumulators,
  pay exit load inside `stcg_holding_months`, and split STCG/LTCG. It has to
  route through the redemption path;
- the decision, now taken: an outflow is **not** part of cash in hand, but the
  gain it realises is real and enters the identity. The solver must carry it as
  exogenous tax load while excluding the outflow from the requirement, or every
  year containing one reports a shortfall;
- a personal outflow may be **funded from the HUF** (distributions to members
  carry no further incidence). The reverse confers nothing;
- clamp an outflow exceeding the corpus and report a shortfall, following the
  precedent at `engine.py:1673`;
- `dialogs.py:513` still reads "by FY year (1–30)"; the ceiling has been 40
  since A3-iii. A hardcoded horizon in a label, invisible to tests;
- no golden scenario exercises a personal windfall at all — all five fixtures
  carry the same single `{'year': 5, 'target': 'huf'}` entry.

### 7.5 Calendar-year anchors

Requested for the tax tables (both entities), the requirement table and the
ad-hoc tables. Do it **at the presentation and persistence boundary only** —
convert in the dialogs and in `to_dict`/`from_dict`, keep the engine on
offsets. Done that way every golden snapshot stays byte-identical, which is
what makes a change this wide safe, exactly as in A1.

- **Pin the labelling convention first.** FY2026-27 runs 1 Apr 2026 – 31 Mar
  2027, and the live plan starts **1 March 2026, which is FY2025-26** — so plan
  year 1 is a one-month stub that labels as *2025* under an FY-start
  convention. A "2025-26" span label is least ambiguous. Declare it in
  `RetirementTaxPlanning.configuration`; no fiscal-year keys exist there yet.
- **Migration needs no version flag**: chunk `year_from`/`year_to` <= 40 is an
  offset, >= 2000 is a calendar year; `annual_requirements` keys `'1'..'40'` vs
  `'2026'..`. Convert on load using `investment_date`.
- **Statutory vs structural.** Convert the six tax-chunk lists and
  `fd_rate_chunks` (facts about calendar years); leave `allocation_chunks`,
  `return_chunks`, `split_chunks` plan-relative (facts about plan stages).
  `huf_withdrawal_chunks` is being deleted. Two conventions in one schema is
  fine only if the field names declare which.
- **This fixes a real defect**: today, changing the plan start date silently
  re-dates every tax band.

### 7.6 Smaller items

- `best_cash_split()` (B1d-i, `12646dd`) is **orphaned** — called only from
  `tests/test_tax_allocator.py`. Its consumer was B1d-ii, which was reverted.
  Mark it as held for the §4 work or archive it, but do not leave it looking
  wired.
- B1d-iii is unstarted: `optimize_withdrawal_split` still at `engine.py:2596`,
  `allocate_funds_v0.9.py` still at the project root. `archive/` now exists with
  a README, so the destination is ready.
- `HANDOVER.md` §8's item about `archive/` not existing is stale — it exists.
  `docs/` is absent from the bundle entirely. `LICENSE` is still absent while
  every source header references it.
- `tests/golden/test_engine_golden.py`'s module docstring still says
  `Engine.run()` is "1,304 lines"; §7 says ~1500.

## 8. What is not verified

- **Every figure in §3 comes from a standalone model, not the engine.** Nothing
  in it is pinned by a test. The engine has not been run against any of it.
- **The engine's withdrawal response is non-smooth and unexplained.** Asked for
  0.10 more in `s0_1chunk` FY7-FY10 it withdraws 0.17. The same years measured
  1.000 at a 0.5 perturbation, so it is a discontinuity being straddled, not a
  derivative — most likely `weight_drift_threshold = 0.015` or
  `drift_cap_personal = 0.0015` flipping which funds the monthly loop draws
  from. `PLAN_TOL = 1e-2` stopped the solver caring; it did not make the
  behaviour go away, and Phase 4 rewrites exactly that machinery.
- **The Phase 3 snapshot review was narrower than its commit message says.**
  `s0_1chunk_annual.csv` was read in full; the other four annual files and all
  five MC bootstrap files were reviewed only by diffstat. The message claims all
  ten were read value by value. Correct that in the next commit rather than
  leaving it standing.
- The 2-chunk model runs treat turnover as sell-and-rebuy at a single NAV. Real
  transitions move between funds with different NAVs and return paths; the tax
  arithmetic is unaffected, the corpus path is approximate.
- HUF slabs above the nil band are extrapolated from the old-regime structure
  (0-N nil, N-2N at 5%, 2N-4N at 20%, above 4N at 30%). That reproduces today's
  regime exactly at N=2.5 but is a projection at N=9.
- The assessable share of other income is held constant at 5.95/15.8. If the
  0.5-to-0.15 increment lands disproportionately in taxable or tax-free
  components, personal headroom moves and every zero in §3 moves with it.
- No Monte Carlo on any of the §3 figures. They assume the stated return
  realises exactly. The margin in §5.3 is the only defence against that and it
  has not been stress tested.

## 9. How Phase 3 was actually found, and what it costs

Worth recording because the method failed and the recovery was luck.

Four hypotheses were wrong in sequence: s.87A marginal relief driving the slope
negative; a secant step fixing it; cross-purse double-counting in the top-up;
and "no HUF column moved" — that last asserted from pytest output truncated at
25 of 37 lines. Each produced a probe, and each probe was built on the previous
wrong conclusion. One probe was itself invalid: it compared a raw
`Engine.run()` against a snapshot produced through `project_plan`, so every
"moved" line in it was solver-versus-no-solver, not before-versus-after.

What found the defect was `pytest --regen-golden` followed by `git diff` on the
ten snapshot CSVs. One pass, complete data, nothing confounded — and the
conservation arithmetic fell straight out of the FY30 totals.

The lesson is in HANDOVER §9.13 and it is not "measure before hypothesising",
though that would have helped. It is that **the snapshot review is the primary
diagnostic, not a formality once the work is done**. Regenerate early and read
the diff; the probes are for questions the diff has already narrowed.

Two things follow for the next phase. The delivery guard (`ac41ad7`) is now the
invariant every change is measured against and must never be allowed to go red.
And the missing conservation test in §7.3 should exist before Phase 4 touches
the withdrawal machinery — it would have turned five rounds of investigation
into one red test.
