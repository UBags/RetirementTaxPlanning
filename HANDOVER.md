# HANDOVER — RetirementTaxPlanning

Replaces the previous handover, which was written when Phase A1 was next and
describes a codebase that no longer exists.

**Baseline: `main` at `12646dd`, 650 tests passing, `ruff check .` clean.**

Phase A is complete. Phase B is one step from complete. The single most
valuable piece of work now outstanding was found by analysis at the end of the
last session and is written up in §4 — it is not in any earlier plan.

---

## 0. Do these first

1. `ruff check .` then `python3 -m pytest -q -n 4`. Expect **722 passing**, no
   warnings summary. Three minutes under `-n 4`: since the cash identity
   changed, `project_plan()` needs twenty to forty passes on some scenarios
   rather than four, and each pass is a full engine run. Run
   `python3 -m pytest`, never bare `pytest` — see §9.12.
2. Read §1 (what changed), then §4 (what to do next), then §9 (gotchas).
   §9 is not optional. Every entry in it cost real time.

### How to work with this codebase

Changes arrive as **patchers**: standalone scripts run from the project root
that apply anchored text edits, with `--check`, `--dry-run`, `--revert`, a
`.bak` per file, and SHA-256 pinning of every target. A patcher validates every
anchor before writing a single byte and refuses a file that has drifted.

Build them by deriving hunks **mechanically** from a verified working tree and
proving they reproduce it byte-for-byte before embedding. Never hand-write a
hunk. §9.6 records two bugs in the generator that only appear on real diffs.

**One step at a time.** Give the command whose result you need, stop, read the
output, then decide the next.

**Every snapshot movement is reviewed before it is committed.** A regenerated
snapshot that nobody read is worth nothing. The review is not a formality: it
caught a genuine defect in B1d-ii after the code, the tests and the patcher
verification had all passed. See §9.9.

---

## 1. What changed, and why

Twenty-eight commits. Tests 382 → 722. Grouped by what they were for.

### Phase A — a flexible calendar and a variable horizon (complete)

| | |
|---|---|
| **A1** `ed4976a` | The horizon becomes a real variable. `AppState.horizon_years` stored, `horizon` clamped into a configured band, `fy_range()` replacing `range(1, 31)`. ~30 hardcoded 30s across five modules. No behaviour change at 30 — every golden snapshot byte-identical. |
| **A2a** `4073929` | The short first financial year is pro-rated. A March start gives FY1 one month; other income and the FD benchmark were booking a full year of both. New `pro_rated_utils.py`. Snapshots moved. |
| **A2b-i** `f7d69e3` | Three percentiles of the liquid-fund series measured and stored (P33/P20/P5), plus `fd_benchmark_spread`. Nothing consumed them yet. |
| **A2b-ii** `36a62a2` | `min_simulation_years = 10`. The floor of 1 was a placeholder. |
| **A2b-iii** `f444c7c` | The FD tax benchmark takes the measured ladder as its default: three equal parts of the horizon at P33/P20/P5 plus spread, replacing a flat 7.00% from nowhere. User-set chunks still win. |
| **A3-i** `e407fb9` | The GUI follows the horizon — `main.py`, `dialogs.py`, `chart_dialog.py`. |
| **A3-ii** `98dd041` | **Plan Period** dialog: start month and plan length become settable at all. Plus chunk-coverage detection and repair. |
| **A3-iii** `a447f6e` | Ceiling raised to 40. Snapshots moved by one header line only. |
| **A3-iv** `0680def` | Two files A3-i missed: `tax_dialog.py`, `needs_assessment.py`. |

### Phase B — solve for cash in hand (one step from complete)

| | |
|---|---|
| **B0** `aab08cb` | Tax the **assessable** figure, not cash received. s.16(ia) on salary and pension, s.24(a) on rent. `OtherIncome.taxable()` was serving two quantities that diverge the moment deductions exist. |
| **B1a** `bbcda19` | `tax_allocator.py` — exact minimum-tax split of a gain between two entities, by breakpoint enumeration. |
| **B1b** `433ddfd` | `net_cash_solver.py` — the net-cash fixed point, with an exact monotonicity check. |
| **B1c-0** `df38b35` | `swp_start_month` removed. It suppressed the first three months of every plan. |
| **B1c** `70c031e` | **`annual_requirements` becomes a net cash-in-hand target.** The largest behaviour change in the sequence. |
| **B1c** `99b6300` | The Monte Carlo goldens pin what the app runs. |
| **B1d-i** `12646dd` | `best_cash_split()` — splitting *cash* rather than gain. |

### Phase C — the cash identity (complete)

| | |
|---|---|
| **C1** `87051b1` | `pytest-xdist` pinned and run in CI. It was used by the documented workflow and credited with finding `a965e4a`, but declared nowhere and exercised by no job. |
| **C2** `ac41ad7` | Per-year requirement-delivery guard across all five golden scenarios. 25 items. The invariant every later phase is measured against. |
| **C3** `bfbf226` | The glide-path transition scheduler retired to a hard cut. Measured, it turned two zero-tax years into 10.75L of tax — it smoothed brackets rather than harvesting capacity. 341 lines removed, no snapshot moved. |
| **C4** `3a3813c` | Secant step in `solve_schedule`, gated so it cannot fire under the old identity. Inert; no snapshot moved. |
| **C5** | The final identity: no transfer to the HUF, rebalancing tax counted as tax paid, `PLAN_TOL = 1e-2`. Uncovered a double debit that predated all of it — see §9.13. All ten snapshots moved. |

| **D1** `?` | Phase 4a: `FolioSet`, cohort ledger per fund, exactly one cohort. Inert; no snapshot moved. |
| **D2** `?` | Phase 4b-i: folio-selection API. Dead code on landing. |
| **D3** `?` | Phase 4b-ii: the config gate, switched off. Fixed a defect in 4b-i where `redeem` dropped the remainder when one folio could not cover the amount. |

### Defects found and fixed along the way


- `a965e4a` — `is_liquid_fund` fell through to the fund NAME when the fund
  TYPE was merely unrecognised, so a Corporate Bond Fund named "…Liquid Fund"
  counted as liquid and the risk-free rate was measured over the wrong
  category. Surfaced by an order-dependent test that had been passing for the
  wrong reason; `pytest -n 4` split the pair across workers and exposed it.
- `4da7da2` — the `pandas` FutureWarning at `get_funds_data.py:760`.

---

## 2. Decisions taken

These were decided, not discovered. Do not revisit them without cause.

**No version flag on `.swp.json`.** The project is in development; saved plans
reinterpret in place. If that changes before release, `annual_requirements` is
the field whose meaning moved.

**The target is `net_cash_total`** — withdrawals from both purses, plus all
other income, less taxes actually paid, less the transfer into the HUF. The
last term is subtracted because that money is invested, not spent; adding it
back would make the solver withdraw more to replace money that had only
changed pocket.

**Horizon 10–40, default 30.** `DEFAULT_HORIZON_YEARS` and
`max_simulation_years` are now different numbers and a test asserts it.

**`investment_date` defaults to today**, so the start is always inside the
current financial year by construction. The Plan Period dialog does **not**
clamp to the current FY — the live plan starts 1 March 2026, which is
FY2025-26, and clamping would silently move an already-running plan.

**The FD benchmark ladder** is three equal parts of the horizon at
`risk_free_rate` / `_mid` / `_low` plus `fd_benchmark_spread`. A rate set in
the dialog always wins; **Reset to defaults** is the only route back.

**`huf_withdrawal_chunks` is to be deleted** and the withdrawal split solved
instead. Not yet done — see §3.

**Monte Carlo stress-tests the plan the deterministic solve chose.** It does
not re-plan per path.

---

## 3. Phase B — what remains

### B1d-iii — delete the dead HUF schedule

Mechanical, no numeric effect. `huf_withdrawal_chunks` reaches nine files.
`optimize_withdrawal_split` and `allocate_funds_v0.9.py` move to `archive/`
with a README — `ruff.toml` line 40 already excludes `archive/` and cites
`archive/README.md`, and neither exists.

**Blocked on B1d-ii**, because `_huf_annual_wd()` is what currently drives every
HUF withdrawal. Delete it while nothing replaces it and HUF draws drop to zero.

### B1d-ii — the solver chooses the split. ATTEMPTED AND REVERTED.

Built, verified, patched, and **reverted at the snapshot review**. It is worth
understanding why before anyone tries again.

It made lifetime tax **worse** in four of five golden scenarios: 24.52 → 24.89,
29.49 → 32.76, and two more. On `s3_4chunk` it also exhausted `max_iter` at 30
passes without converging, landing an order of magnitude worse than the
no-split solve.

Three compounding causes:

1. **The split was optimised against stale gain fractions.** It used the
   previous pass's realised fractions, but drawing from the HUF erodes the
   HUF's cost basis, which changes its fraction — the optimiser chased a
   target that moved in response to its own decision.
2. **The freeze fired on an aggregate.** It triggered when the *lifetime* HUF
   share stopped changing, which can settle while individual years still
   oscillate, locking in a per-year ratio that was never optimal.
3. **The caps were circular** — `cap_first = personal_corpus + total`, where
   the corpus figure is the year-*end* balance, after the withdrawal.

It also shipped a second bug the review exposed: `solve_with_split` computed
`converged = not shortfalls`, and `shortfalls` only counted *under*-delivery.
Over-delivery was invisible, so it reported success while `net_cash_total`
drifted above target in six to thirteen years. `solve_schedule` gets this right
(`abs(...) <= tol`); the new function regressed on it.

**But the deeper lesson is in §4**: the split was the wrong lever entirely.

---

## 4. TAX MINIMISATION — what was measured, and what to build

Everything here was measured with a standalone model, not the engine. It runs
FIFO lots at NAV, solves each year's withdrawal against its own tax, and
verifies in every year that

    withdrawals + other income - tax - loads  ==  the stated requirement

That identity check is not decoration. It caught two over-delivery bugs mid-run
that the tax totals alone hid — the same failure mode as B1d-ii's
`converged = not shortfalls`. **Any successor must carry the same check.** A
schedule that delivers 44.6 against a requirement of 42.5 will look like a
brilliant tax result.

Parameters used (the live plan, not the scrubbed fixtures): personal corpus
350, requirement 35 rising to 50 in five-year steps, other income 15.8 growing
by an increment that ramps 0.50 -> 0.15 per year, assessable share held at
5.95/15.8, HUF seeded with an inherited 190 at FY5 earning 7.0%, statutory
ladders as stored. All figures Rs lakh over 30 years.

### 4.1 The four levers, ranked by what they actually deliver

| lever | lifetime tax |
|---|---|
| baseline: personal purse only | 69.67 |
| split withdrawals across both purses | 6.55 |
| **ration the HUF over the horizon** | **0.29** |
| schedule chunk transitions against free capacity | 50.38 -> 0.19 |
| separate folios by vintage (3 of them) | 0.29 -> 0.00 |

Two of these were not in any earlier plan and neither was predicted.

### 4.2 Ration the HUF. This is the biggest single finding.

A split optimiser that minimises **this year's** tax drains the HUF as fast as
it looks locally cheap. The HUF then empties around FY25-29 and the personal
purse carries everything alone at an 85-88% gain fraction, which is where every
rupee of late-plan tax comes from.

Capping the HUF draw so the corpus lasts the horizon — roughly
`1.2 x huf_value / years_remaining` — takes lifetime tax from 6.55 to 0.29.
Under the greedy rule, multi-folio harvesting was *actively harmful* (15.78 vs
6.55) and the folio-count sensitivity was non-monotonic, which is what exposed
the problem: more folios cannot make things worse, so the fault lay elsewhere.

**Rule: the HUF is a budget spent over thirty years, not a cheaper purse to
drain first.** Any per-year optimiser will get this wrong. Whatever replaces
`optimize_withdrawal_split` needs a horizon-aware constraint, not a better
annual objective.

### 4.3 Schedule chunk transitions against capacity, not against a calendar

A 2-chunk plan (7.5% then 7.0%) with 25% turnover at an FY16 boundary:

| when the 25% moves | tax | end corpus |
|---|---|---|
| all in FY16 | 50.38 | 1804.6 |
| evenly over FY12-20 | 23.91 | 1872.2 |
| capacity-driven, FY4-26 | **0.19** | **1934.1** |

129 of corpus between the first row and the last, for the same portfolio
outcome. A capacity-driven transition is **free**: 25% reallocated gives the
same tax and the same end corpus, to one decimal, as not transitioning at all.

Capacity-driven means: each year, after the cash withdrawal has taken its share
of the exemption, move only as much as the remaining headroom permits, and
carry the balance forward. Instalments track capacity, not a schedule — in the
measured run they went 4.15, 10.26, 6.96, 5.44, 2.41, 1.20 as headroom opened
and closed.

**The window length is the price.** FY4-26 (19 years) completes 25% turnover at
zero tax; FY6-20 costs 7.55; FY12-20 costs 23.91. The transition must start
years before the boundary and finish years after it.

This supersedes the existing glide path entirely. Measured on the golden
fixtures, that glide path turned two zero-tax years into 10.75 of tax
(`s1_2chunk` FY14-15) — it smooths brackets, it does not harvest capacity. It
is to be replaced, not repaired.

### 4.4 One headroom pool, shared

The single most important structural fact. The annual exemption is consumed by
the ordinary SWP withdrawal **first**; the transition and the harvest compete
for what is left. In the measured table the harvest column is **zero from FY4
to FY21** because the transition took all of it.

Consequences:

- Transition and harvest do not add. Combining a capacity-driven transition
  with 3 folios moved lifetime tax only from 0.19 to 0.00.
- With two boundaries, windows overlap — a boundary at FY8 (window FY3-10) and
  one at FY15 (window FY7-17) share FY7-10. That capacity can be spent once. It
  must be **allocated** across the competing windows, not offered to each.
  Double-spending produces a schedule that reports success while over-committing.

### 4.5 Multi-folio: two orderings, deliberately opposite

Within a folio FIFO is forced, so the only lever is **which folio**. Two rules,
and they point opposite ways — which is precisely why separate folios earn
their keep:

- **cash withdrawal** -> the folio whose front lot has the **lowest** gain
  fraction. Minimises taxable gain per rupee raised.
- **harvest** -> the folio whose front lot has the **highest** gain fraction.
  Maximises gain realised per rupee churned, and retires the units that would
  cost most later.

A single "pick the cheapest folio" scan gets only half of this.

**Single-folio harvesting does not work, and is worse than not harvesting.**
Measured: 16.47 with FIFO harvesting against 6.55 without. The gain fraction
was *identical* in every year — 7%, 13%, 30%, 50%, 65%, 75%, 83%, 88% — because
rebuying appends to the back of the queue while the front remains the rest of
the same original lot. The corpus grows faster than it is drawn (350 -> 1900+),
so roughly 200 of the original 350 units are ever consumed and the front never
advances past original-basis stock.

**Three folios is enough.** Monotone and saturating: 1 folio 0.29, 2 folios
0.04, 3 folios 0.00, 4 and 5 folios 0.00. Take 5-year cohorts — cohort length
barely changes the result and longer blocks mean fewer folio openings.

Personal gain fraction on withdrawals falls from 88% to 64% at FY30. That is
the real prize: not the headline tax, but the **margin**. Without it the zero
sits exactly on the exemption line with no buffer; a rupee more of income or
requirement tips it over.

### 4.6 Legality of the folio split

Capital gains are computed FIFO **per folio**, not across a PAN — CBDT Circular
704 (1995) establishes FIFO; the account-level boundary is what makes cohorts
independent ledgers. Redeeming from a younger folio is tax planning, not
evasion. Three practical constraints:

- AMCs reserve the right in their SAI to **consolidate folios** under the same
  PAN, name and bank account. Few deliberate folios, possibly across different
  platforms, rather than many.
- **GAAR (s.95)** is a theoretical risk if the arrangement's only purpose is a
  tax benefit. Meaningful 3-5 year cohorts, not monthly micro-folios. Three
  folios in 5-year blocks is well inside this.
- The engine must hold **a ledger per cohort**, not one pool per fund.

### 4.7 s.64(2) — the constraint that sizes the HUF

Income from property a member transfers to his HUF is clubbed back into the
member's income, not taxed at HUF rates. This matters because the entire
strategy rests on the HUF's separate bands.

Current position, which resolves it favourably: the 190 windfall is **inherited
directly by the HUF under a will**, so it is not a member transfer and s.64(2)
does not attach. Under the scheme now adopted, tax savings stay in the personal
purse rather than being transferred to the HUF, so no tainted capital is created
at all.

**If that ever changes** — any member contribution to the HUF — the exposure
returns, and the model would need to track transferred-in capital separately
from the HUF's own accretions, by vintage. That is a structural change, not a
parameter. It should be confirmed with a CA before any such transfer is made,
not after.

### 4.8 Thumb rules for water-filling

1. Fill both nil bands first, then both 5% bands, and so on — but **subject to
   the HUF ration**, which binds before the marginal-rate logic does.
2. The HUF gets **no s.87A rebate** (`engine.py:82`, the rebate branch is
   `if entity == "individual"`). Under the old regime it steps 5% -> 20% at
   twice the exemption, so HUF income above the nil band gets expensive fast.
   Keep HUF income inside its nil band and stop.
3. Personal free capacity is `exempt_limit - assessable_other_income`, and
   assessable other income **grows**, so the capacity shrinks over the plan.
4. The withdrawal's own gain is charged against the headroom before anything
   else. Harvest and transition get the remainder.
5. Harvest only lots older than `stcg_holding_months`. Selling oldest-first
   satisfies this automatically and avoids the exit load.
6. Early harvesting is nearly worthless — stepping basis from NAV 1.00 to 1.07
   is a 7% step. Headroom is largest early and worth least early. The two are
   anti-correlated; do not assume more harvesting is better.
7. A transition instalment is itself a redemption and partly funds that year's
   cash need. Sell-and-rebuy and sell-for-cash are the same operation.
8. Cross-purse funding is **asymmetric**: HUF -> personal is free (distributions
   to members carry no further incidence); personal -> HUF is a member transfer
   and re-opens 4.7.

### 4.9 What to build, in order

**Status.** Item 4 landed first, deliberately, as `ac41ad7`:
`tests/golden/test_requirement_delivery.py` asserts per year, per scenario,
that the plan delivers the stated requirement. It is the guard every later
phase is measured against and must never be allowed to go red. The
conservation identity that was missing beneath it landed too — see §9.13.

1. **Corpus representation** — **in progress, three quarters done.**
   `FolioSet` (4a `?`), the selection API (4b-i), and the config gate (4b-ii)
   are landed, verified and **inert**: `folio_cohort_threshold = 0.0` opens
   exactly one folio per fund and every operation delegates to it, so nothing
   has changed yet. See §4.10 for what remains.
2. **Horizon-aware split** replacing the per-year optimiser, with the HUF ration
   as a constraint.
3. **Capacity-driven transition scheduler**, with straddling windows and
   explicit allocation of shared headroom where windows overlap. The old glide
   path is already retired (`bfbf226`), so this builds into an empty socket
   rather than around a live one.
4. ~~The identity check as a test on every scenario, every year.~~ Done.

### 4.10 Phase 4 — where it stands, and the three conversions left

Landed and inert. The gate is off, so none of it changes a number yet:

| | |
|---|---|
| **4a** | `FolioSet`: `Dict[folio_id, FIFOBucket]` per fund, presenting the `FIFOBucket` surface so eighty-odd call sites are untouched. Six construction sites changed. Verified against a plain `FIFOBucket` over 4000 randomised operations. |
| **4b-i** | `pick_for_cash` (lowest front-lot gain fraction), `pick_for_harvest` (highest), `ensure_cohort`, `sell_ordered`. Dead code on landing. |
| **4b-ii** | `FolioSet` self-manages: threshold in the constructor, `invest` opens cohorts, unnamed `redeem` draws cheapest-first **and spills onward**. Config keys `folio_cohort_threshold` (0.0) and `folio_max_cohorts` (5). |

**Three conversions remain, and they are three different problems, not three
instances of one.** Each assigns to `.lots`, which the setter refuses once a
second folio exists — that guard is deliberate, and it is what stops the
single-folio behaviour that measured *worse than not harvesting at all*.

| site | shape | needs |
|---|---|---|
| `_sell_hifo`, sell + rebuild | forced rebalancing sell | `sell_ordered(prefer="cash")` behind a `len(bk.order) > 1` guard. Clean. |
| the second rebalance sell | carries `rebal_tax_tally` and stops **mid-lot** when running tax hits `annual_tax_budget` | a budget-aware variant; `sell_ordered` knows nothing about a budget |
| the equity LTCG harvest | a **gain budget**, and it steps up basis in place (`Lot(units, nav, m)`) rather than selling an amount | a new `harvest_to_budget`, drawing `prefer="harvest"` |

Line numbers shift with every commit — find them with
`grep -n "\.lots = " engine.py`.

**Guard each conversion on `len(bk.order) > 1`** so the single-folio path is
untouched and the commit stays inert. Then raising the gate is a one-line
config change whose entire diff is snapshot movement, which is the cleanest
possible thing to review.

**A separate decision, not part of the above.** Those sells currently sort ALL
lots by gain and take the cheapest — HIFO. **That is not achievable in
reality.** Indian practice computes gains FIFO per folio and an AMC cannot be
instructed to sell your highest-cost units; folio choice is the only lever.
So the engine models rebalancing as more tax-efficient than it can be, and
every glided figure ever measured inherits that optimism. Correcting it moves
single-folio behaviour from HIFO to FIFO and moves every snapshot. Own commit,
own review, and do not let it ride along on a refactor.

**The threshold default is not settled.** `0.50` gave three folios on the
retirement model and four on a synthetic drawdown. It is a dial for how many
folios you run, not for the tax outcome — every value measured reached the
same answer. Measure it on the real plan when the gate is raised, rather than
carrying my number across.

## 5. Phase F — investment profile and staggered deployment

Decided in the last session, not started.

- **An investment profile**: how much is invested, deployed into the SWP over
  0-36 months, default immediate.
- **The undeployed remainder** sits in a mix of FD and mutual funds and **pays
  out** 7% pre-tax. FD interest is taxed at slab; MF units are redeemed for
  cash, realising LTCG.
- **Split MFs into debt and equity only**, matching the three sleeves the
  engine already tracks. Give the user a GUI override for exact amounts and
  return rates; default to the derived blend.

The default mix falls out of the arithmetic rather than being another input:
FD at the P33 rung (6.35%) blended with equity at 9% to yield 7% implies
**75.5% FD / 24.5% equity**.

**One input this genuinely needs**: redeeming MF units realises
`X × (1 − cost/value)`, and there is no purchase history for a holding that
predates the plan. Either an explicit "unrealised gain on existing holdings"
figure, an assumed gain fraction, or treat redemptions as wholly gain
(conservative, overstates tax).

**Note**: the LTCG schedule already exists. `EquityTaxChunk` carries rate and
exemption per FY chunk, editable separately for individual and HUF in
`TaxRulesDialog`. Unless a *distinct* schedule is wanted for the pre-deployment
holding, that part is wiring rather than a new feature.

---

## 6. Findings recorded, deliberately not fixed

1. **The golden harness runs without a glide path.**
   `test_engine_golden._run` calls `project_plan(state)` with no `glide_path`,
   while `main.py` passes `glide_path=self.state.glide_path`. Every golden
   snapshot therefore pins the harsher, unglided case. With the glide path the
   lifetime tax figures are 24 / 8 / 29 instead of 57 / 43 / 77 — the glide
   path more than halves the boundary cost by spreading the transition. **This
   is the most misleading of the coverage gaps**, because it makes chunked
   strategies look far more expensive than they are. Small fix, high value.

2. **`_write_risk_free_rate()` decides "unchanged" against a stale singleton**,
   not the file it is about to write. Defensible with one config, and it is
   what made a test order-dependent for months. Its own commit, not a rider.

3. **The seven orphaned optimiser config keys** in `configuration.UNUSED_KEYS`
   are still read by nothing.

4. **`get_funds_data.py`'s CLI defaults still point at `../Personal/`**, which
   does not exist. The GUI supplies absolute paths, so only command-line use is
   affected.

5. **`chunk_defaults.default_chunk_layout()` falls back to
   `max_simulation_years`** when given no horizon. That was the plan length
   while the ceiling was 30; it is now 40 and the fallback is wrong in
   principle. Nothing live reads it — §7's Option selector is still unbuilt —
   but wire `state.horizon` when it is.

---

## 7. Still outstanding from the earlier plan

- **§2.4 GUI half** — `chunk_defaults` computes the approved per-Option table
  and nothing reads it. It needs an Option selector.
- **Phase 3.1 — decompose `Engine.run()`.** ~1500 lines. Still the highest
  structural value remaining, and Phase B's outer-loop design exists partly
  because the engine cannot re-run a single financial year. Blocks 3.2.
- **Phase C** — solver interactions. Cannot be specified until B settles.
- **Phase D** — Monte Carlo horizon. 18 sites; `MC_FIXED_HORIZON = 30` is now
  explicit everywhere and `run_monte_carlo()` refuses any other horizon by
  name, so the work is bounded and the failure mode is loud.
- **Phase E** — sensitivity horizon.
- **Phase 2.3 — delete dead code.** `ruff check --select F841 .`, plus
  `allocate_funds_v0.9.py`. Delete the `F841` ignore from `ruff.toml` when it
  lands.
- **Phase 5** — optimiser property tests and a sigma study.

---

## 8. Housekeeping

- `LICENSE` is absent but referenced by every source-file header.
- `docs/` exists and is empty; `docs/DEFECTS.md`, referenced by commit
  `be54844`, was never committed.
- `archive/` does not exist, though `ruff.toml` excludes it and cites its
  README.
- Both GitHub repositories are now private. That is resolved.

---

## 9. Gotchas that have already cost hours

1. **`.gitignore` is an allowlist whose line 2 is `*`.** New files are
   admitted by extension. `git check-ignore -v` **exits 0 whether or not the
   file is ignored** — read the *pattern*: a leading `!` means admitted. The
   reliable check is whether the file appears as `??` in
   `git status --porcelain -uall`.

2. **The project bundle is lossy at EOF.** A fenced code block needs a newline
   before its closing fence, so the exporter adds one to any file that lacks
   it. Fourteen files are affected: `allocate_funds.py`, `chart_dialog.py`,
   `engine.py`, `fund_dialog.py`, `get_funds_data.py`, `main.py`, `models.py`,
   `monte_carlo.py`, `needs_assessment.py`, `run_debug.py`, `tax_dialog.py`,
   `test/Check_Funds_Universe.py`, `test/__init__.py`, `test_swp_engine.py`.
   **Never hash a file straight out of a bundle.** This cost three round trips
   before the full list was obtained.

3. **The bundle exporter omits `RetirementTaxPlanning.configuration`** — the
   one file without which the application will not start. It filters by
   extension and that file has none it recognises. One extension added to the
   exporter retires the problem permanently.

4. **The staleness test must grep for the NEWEST commit's marker**, not any
   recent one. A bundle exported one commit behind passed a check for the
   commit before it.

5. **Validate on all five golden scenarios, never one.** B1d-ii was declared a
   benign no-op on the strength of `s0_1chunk`, the only scenario with zero
   tax. The other four got worse. Five fixtures exist precisely so this cannot
   happen.

6. **The patcher generator has two non-obvious requirements**, both found on
   real diffs:
   - Hunk uniqueness must be checked against the text **as the patcher will
     see it** — every earlier hunk already applied — not against the pristine
     file. `dialogs.py` has three near-identical chunk editors, and a block
     unique in the original stops being the only match once an earlier
     replacement lands.
   - Edits closer together than two context windows must be **coalesced**.
     Otherwise expanding a later hunk's context pulls in lines an earlier hunk
     rewrote, and widening it makes matters worse rather than better.

7. **A config key in `CONFIG_FINGERPRINT_KEYS` changes every snapshot header**
   when its value changes, so a pure config edit produces ten failing golden
   tests with a clear message. That is the guard working. Regenerate and check
   the diff is header-only.

8. **A skip in the environment where you verify is a hole, not a pass.**
   Install `PySide6` and `pytest-qt` before claiming a green suite. Both are in
   `requirements-dev.txt` and both are now genuinely used — 34 tests drive real
   widgets.

9. **The snapshot review is a real gate.** B1d-ii passed its unit tests, its
   patcher verification, byte-for-byte reproduction and a full suite run. The
   review caught it. Predict what the diff should show *before* looking, then
   check the prediction.

10. **`pytest -n 4` finds order-dependent tests that sequential runs hide.**
    `test_writeback_ignores_funds_that_are_not_liquid` had been passing only
    because a preceding test left a module-level singleton holding the same
    value; xdist put them on different workers and the mask vanished.

11. **Not every `30` is a horizon.** `main.py`'s `addSpacing(30)` and
    `needs_assessment.py`'s `setRowHeight(…, 30)` are pixels;
    `optimization_report.py`'s `rebal_pct > 30` is a percentage. There are
    tests asserting each survives a horizon sweep.

12. **Run `python3 -m pytest`, never bare `pytest`.** A user-level
    `~/.local/bin/pytest` can shadow the venv's on `PATH`. It carries a system
    shebang, so it sees none of the venv's packages — no PySide6, no
    pytest-qt, no xdist. It rejected `-n 4` outright, which is the only reason
    the shadowing was caught: had it accepted the flag it would have run and
    reported green with all 34 widget-driving tests silently skipped. That is
    §9.8 arriving by another route. `which -a pytest` shows the shadowing;
    activating the venv fixes it; `python3 -m pytest` is immune either way.

13. **The review IS the diagnostic, not a formality after it.** Phase 3 was
    chased through five rounds of instrumentation on four successive wrong
    hypotheses — s.87A marginal relief, a secant fix, cross-purse
    double-counting, and "no HUF column moved" (asserted from pytest output
    that had truncated at 25 of 37 lines). What found the actual defect was
    `--regen-golden` followed by `git diff` on the snapshots: one pass, whole
    answer, nothing confounded. Regenerate and read the diff EARLY, not after
    the probes fail.

    The defect it found had been there a long time. `net_p` subtracted
    `this_fy_saved`, so the solver withdrew an extra X to still deliver the
    requirement; the engine then separately debited X from the per-fund buckets
    as the actual transfer. Departing the corpus: `W + X`. Arriving anywhere:
    `W + OI - X` to the user plus `X` to the HUF, so `W + OI`. **X destroyed
    every year**, compounding to 114L over thirty years on `s0_1chunk`.

    `test_conservation.py::test_monthly_corpus_flow_balances` was blind to it by
    construction: it balances the corpus against *itself*, and both outflows
    were recorded as withdrawals, so its residual stayed at 5.7e-14 throughout.
    Its docstring claims it catches "exactly how the tax and HUF-transfer leaks
    manifested." It cannot. A conservation test that never crosses the boundary
    between the corpus and the user's hand is not a conservation test.

14. **A synthetic test that does not resemble the workload can pass while
    proving nothing.** Phase 4b-i's `redeem` picked the cheapest folio and
    delegated; where that folio could not cover the amount, the remainder was
    silently dropped. It shipped green.

    The verification was 3000 randomised invest/redeem steps against a plain
    `FIFOBucket`, identical throughout — and worthless, because constant FIFO
    churn keeps the queue front fresh, so no second folio ever opened and the
    multi-folio path never ran once. A realistic pattern — one large initial
    investment, small regular draws, annual reinvestment — exposed it at once:
    240.5 units remaining against 228.3 for plain FIFO, i.e. redemptions coming
    up short.

    The same failure as asserting from truncated pytest output (§9.13), in a
    different costume: checking the thing that was easy to check rather than the
    thing that needed checking. **Shape the fixture like the plan** — a large
    corpus drawn down slowly — not like a random walk.

---

## 10. What is not verified, and cannot be from an assistant's side

- The GUI is **imported and driven headlessly**, not used. Dialog layout,
  threading and anything requiring a real event loop are unverified.
- **The golden fixtures are frozen copies** of the live project, and they are
  all 100% debt. The Monte Carlo bootstrap snapshot is therefore completely
  insensitive to the equity series — there is a test saying so.
- **Fund data is not refreshed.** `risk_free_rate_mid` and `_low` are
  placeholders seeded from a 32-fund name-only match; a real
  `get_funds_data.py` run matches 33 with the Fund Type column and will move
  them slightly. `risk_free_rate` itself reproduces the configured 0.0535
  exactly.

---

## 11. Live figures, for orientation

```
corpus at FY1                    ~215 L
personal other income             5.401 L taxable + 4.296 L tax-free
  of which pension                1.105 L   (less 0.75 s.16ia)
  of which rent                   4.296 L   (less 30% s.24a)
assessable other income           3.362 L
HUF other income                  none — its 4 L nil band is entirely free

annual requirement (NET, since B1c)   12.274 L rising to 27.616 L
lifetime cash in hand                587.1 L   (was 924.1 before B1c)
corpus at FY30                       937.9 L   (was 0.00 — the plan no
                                                longer exhausts)

FD benchmark ladder    FY1-10   6.35%    P33 + 1%
                       FY11-20  5.48%    P20 + 1%
                       FY21-30  4.57%    P5  + 1%

tax-free gain capacity per year      15.138 L across both entities
```

The plan was delivering a third more cash than it was asked for. Under the net
target it delivers exactly what is asked, and the difference stays invested —
which is why the corpus no longer runs to zero.
