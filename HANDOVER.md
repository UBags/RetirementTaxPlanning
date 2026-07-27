# HANDOVER — RetirementTaxPlanning

Replaces the previous handover, which was written when Phase A1 was next and
describes a codebase that no longer exists.

**Baseline: `main` at `12646dd`, 650 tests passing, `ruff check .` clean.**

Phase A is complete. Phase B is one step from complete. The single most
valuable piece of work now outstanding was found by analysis at the end of the
last session and is written up in §4 — it is not in any earlier plan.

---

## 0. Do these first

1. `ruff check .` then `python3 -m pytest -q -n 4`. Expect **650 passing**, no
   warnings summary. About 65 seconds under `-n 4`, 88 sequential:
   `project_plan()` runs the engine four to ten times per projection, and five
   golden scenarios each pay that. Run `python3 -m pytest`, never bare
   `pytest` — see §9.12.
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

Eighteen commits. Tests 382 → 650. Grouped by what they were for.

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

## 4. THE LARGEST OPPORTUNITY — tax-free harvesting across chunk boundaries

Found by analysis after B1d-ii was reverted. It is worth more than anything
else outstanding and is not in any earlier plan.

### Where the tax actually comes from

`s0_1chunk` has **zero lifetime tax**. Every multi-chunk scenario has a large
one. The cause is not the withdrawals — it is the **chunk boundaries**.

`s1_2chunk` moves from a 15-fund portfolio to a 13-fund one at FY16, with 9
funds in common. Six funds holding 40% of the corpus are sold outright in a
single year:

```
FY16 tax  54.21      of a 57.40 lifetime total
```

`s3_4chunk` has boundaries at FY9, FY17 and FY24; FY24 alone carries 62.56 of
its 77.03. The tax is not spread — it is concentrated in single years.

The `rebalance_tax_paid` column **understates this**. It is a pro-rata
attribution of the year's tax by gain share, showing 60-70%. The *marginal*
cause is closer to 100%: without the boundary there is no taxable year at all,
which is exactly what `s0_1chunk` demonstrates.

### The capacity that is going unused

Every financial year, both entities can realise gain with **zero** tax:

```
personal debt gain    8.638     s.87A — no tax until total income reaches 12L
personal equity       1.250     LTCG exemption
HUF debt gain         4.000     nil band, no other income against it
HUF equity            1.250
                     ------
per year             15.138 L
```

Over FY1-15 that is **272.7 L** of capacity, of which the current plan uses
**43.3 L**. The spare is **229.4 L**.

The gain embedded in the funds dropped at FY16 is **152.7 L**.

**The spare capacity covers the entire boundary, with 77 L left over.** The
54.21 L spike is not reducible — it is *eliminable*.

### What the mechanism would be

Not withdrawing more to spend. **Selling and immediately rebuying**: sell from
funds the next chunk drops, buy into funds it keeps, in annual slices sized to
the remaining tax-free headroom. The portfolio transition executed early and
free, instead of in one taxable lump.

Four constraints shape it:

- **Exit load** — `exit_load_fraction` applies within `stcg_holding_months`
  (12). Harvest only lots older than that and it costs nothing.
- **STCG** — the same cut-off. One rule handles both.
- **Capacity does not carry forward.** Unused headroom in FY1 is gone. That is
  what makes this a schedule rather than a one-off.
- **Basis steps up** as you harvest, shrinking future gains. That is the
  benefit, not a cost.

The boundary year itself can also slide by a year or two, which is a further
degree of freedom. With 229 L against 153 L it is not needed here; it would
matter on a tighter plan.

### Why this reframes B1d-ii

B1d-ii tried to minimise tax on a **fixed** set of realised gains by choosing
which purse they came from. The dominant lever is not allocation but
**timing** — *when* gain is realised, so that it lands in years with unused
free capacity. Timing dominates allocation by an order of magnitude here, which
is why optimising the split gained nothing and cost accuracy.

Do this before revisiting B1d-ii.

---

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
