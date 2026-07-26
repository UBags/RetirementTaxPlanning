# HANDOVER — RetirementTaxPlanning

_Pending work only. Anything already done has been removed; `git log` has that._

**Baseline: `main` at `4dea430`, 382 tests passing, `ruff check .` clean.**

---

## 0. Do these first

1. **`4dea430` is committed but NOT pushed.**
   `git -C ~/PycharmProjects/RetirementTaxPlanning push`, then check the CI run.
2. **Confirm state before anything else:**
   ```bash
   git -C ~/PycharmProjects/RetirementTaxPlanning status -sb -uall
   ruff check . && pytest -q -n 4      # expect 382
   ```
3. `pytest -n 4` works (pytest-xdist installed) and halves the runtime. Use it.

### How to work with this codebase

Changes arrive as **patchers**: standalone scripts run from the project root that
apply anchored text edits, support `--dry-run` / `--check` / `--revert`, write a
`.bak`, validate every anchor before writing anything, and refuse to touch a file
that has drifted. Build them by deriving hunks mechanically from a verified
working tree and proving they reproduce it byte-for-byte before embedding.

**One step at a time.** Give the command whose result you need, stop, read the
output, then decide the next. Stacking five stages and guessing at stages three
to five is how several hours were lost.

---

## 1. Phase A — flexible calendar and a variable horizon

**Decision already taken:** the plan starts in **any month of the current FY,
defaulting to the current month**, and ends on **31 March** of a chosen year,
defaulting to 30 financial years out. A menu item exposes both.

### A1 — make the horizon a real variable  ← NEXT

`config.max_simulation_years` exists, is in `REQUIRED_KEYS`, and is read in
**two** places (`engine.py:2417`, `chunk_defaults.py`). Everywhere else 30 is
hardcoded — `range(1, 31)`, `min(fy, 30)`, `np.full(30, …)`, 30-element arrays,
30-row snapshots. Roughly **69 sites across nine modules**:

| file | sites | in A1? |
|---|---:|---|
| `monte_carlo.py` | 18 | no — Phase D |
| `engine.py` | 14 | **yes** |
| `dialogs.py` | 13 | no — A3 |
| `chart_dialog.py` | 8 | no — A3 |
| `main.py` | 7 | no — A3 |
| `glide_path.py` | 6 | **yes** |
| `models.py` | ~5 | **yes** |
| `allocate_funds.py` | 2 | **yes** |
| `optimization_report.py` | 2 | **yes** |

A1 is ~27 sites. **No behaviour change at horizon 30 — the golden snapshots must
stay green throughout.** That is what makes a change this wide safe to attempt.

#### The foundation (built and verified in a sandbox, NOT in the repo)

Add to `AppState`:

```python
    #: Last financial year of the plan. The plan ends 31 March of
    #: (investment FY + horizon_years). Stored rather than derived from an end
    #: date so FY indices — which every chunk, tax band and requirement is
    #: keyed by — stay integers.
    horizon_years: int = 30

    @property
    def horizon(self) -> int:
        """THE single authority. Clamped to config.max_simulation_years, which
        is the ceiling the rest of the system (notably the 30-element Monte
        Carlo arrays) is built for. Raising that ceiling is Phase D."""
        from configuration import config
        ceiling = int(config.max_simulation_years)
        return max(1, min(int(self.horizon_years or ceiling), ceiling))

    def fy_range(self):
        """1..horizon inclusive. Replaces range(1, 31)."""
        return range(1, self.horizon + 1)
```

Verified: default 30; `15 → 15`; `99 → 30`; unset → 30; legacy files → 30.

#### Known blocker, found while verifying the above

`horizon_years` does **not** round-trip. `AppState.from_dict()` filters through a
`known_keys` whitelist at **`models.py:615`** and silently drops anything not
listed. Add `"horizon_years"` to it, and to the constructor:

```python
    investment_date=inv_date,
    horizon_years=int(d.get("horizon_years", 30) or 30),
```

**This is the third whitelist to silently drop data** — `.gitignore` (three
files), the CSV `cols` list in `get_funds_data.main()`, now `known_keys`.
**Write a test that round-trips every `AppState` dataclass field and asserts
nothing is lost.** It would have caught this in seconds and will catch the next.

#### The sites that need thought, not substitution

`glide_path.py` has seven, and two encode meaning rather than a bound:

```python
right_end = min(right_end, 29)      # year 30 is hold-to-end
...
if y == 30:
    schedule[y] = _owning_weights(30)
```

"The last year is hold-to-end" is the intent; `29`/`30` are how it was spelled
when the horizon was fixed. These become `horizon - 1` and `horizon`, but read
the surrounding logic first.

#### Sites in `engine.py`

Lines 1178, 1446, 1485, 1568 (`safe_fy = min(fy, 30)`), 1583, 1760, 1940, 1953,
2071, 2135, 2205, 2317 (`p_rows = [r for r in p_rows if r.fy_year <= 30]`).
2417 already reads config.

#### Monte Carlo must refuse, not silently truncate

Until Phase D, `run_monte_carlo()` should raise or warn clearly when
`state.horizon != 30` rather than produce 30-element output for a different plan.

### A2 — the calendar itself

**Separate commit from A1.** A1 is wide and must move nothing; A2 is narrow and
moves everything. Mixing them makes neither diff reviewable.

1. **Default `investment_date` to the current month.** Currently
   `date(date.today().year, 3, 1)` — March hardcoded at `models.py:449` and
   again in the `from_dict` fallback at `models.py:630`.

2. **The horizon spills today and the spill is silently dropped.** The engine
   runs 360 months regardless of start month:

   | start | FY1 | FY31 | monthly rows kept |
   |---|---:|---:|---:|
   | 2026-03-01 | 1 month | 11 months | 349 |
   | 2026-07-01 | 9 months | 3 months | 357 |
   | 2026-04-01 | 12 | 0 | 360 |

   `p_rows = [r for r in p_rows if r.fy_year <= 30]` throws the remainder away.
   **The live March plan is projecting 349 months, not 360.** Under the decision
   taken, the horizon should be computed in months from the start date to 31
   March of the final FY, not fixed at 360.

   Note `_fy_year_for_month()` is already correct for arbitrary start months —
   a July start gives FY1 = 9 months, FY2 onward = 12. Nothing to fix there.

3. **FY1 other income is not pro-rated.** `OtherIncome.taxable(fy)` returns a
   full year regardless of how many months FY1 spans. On the live project
   (`investment_date = 2026-03-01`, other income ₹15.3L) that is **~11 months of
   phantom income**, and FY1 is now the growth base year, so it compounds
   through all thirty. Requirements already pro-rate correctly
   (`req_monthly = req_annual / 12`, applied per month).

4. **Add `Engine.months_in_fy(fy)`** as the single definition and use it
   everywhere an annual figure becomes monthly. Do not inline the arithmetic —
   that is the mistake §2.1, §2.3 and §2.5 each had to undo.

5. Tax slabs stay annual. Indian tax is assessed on the financial year whatever
   fraction of it you were invested.

**Snapshots move here, deliberately, in their own commit.**

### A3 — the menu item

Dialog for start month (constrained to the current FY) and end year (default
+30, bounded). Then the ~28 GUI sites in `main.py`, `dialogs.py`,
`chart_dialog.py`.

**Untestable without PySide6.** Either accept manual smoke-testing, or land
`pytest-qt` first — it is already in `requirements-dev.txt` line 4, commented
"Phase 4 GUI worker tests", and entirely unused.

---

## 2. Phase B — solve for net income

**The request:** the user specifies the *total income they need* and the system
determines how many units to liquidate. Today `annual_requirements` means
"withdraw this much"; it must come to mean "end up with this much".

**Confirmed current semantics:** `get_requirement(year)` returns
`annual_requirements[y]` untouched, and that is the withdrawal target the SWP
drives at (`engine.py:1765`). Other income is added *afterwards*:

```python
net_p = (ann_wd_p + ann_topup_p
         + taxable_other + non_taxable_other
         - this_fy_saved - swp_only_tax)
```

So the corpus funds the full requirement and other income is surplus on top.
Other income *is* stacked under SWP debt gains for slab purposes
(`total_slab_income = taxable_other + a["gain_d"]`) and is in the FD benchmark.

**Why this is a fixed point.** Withdrawal → gain → tax → net cash, and the target
is on net cash. No closed form: the gain fraction depends on which lots HIFO
sells, across three asset classes, stacked above other income in the slabs, with
tax paid in April of the following year.

**Proposed approach — an outer loop, not surgery on the inner one:**

```
req[k+1] = req[k] + (target − net_cash[k])
```

Contracts at roughly the marginal tax rate, so it converges geometrically — a
handful of iterations, ~0.2s per run. It does not touch `Engine.run()`, which
matters: that method is ~1500 lines and four of the five engine defects found so
far live in it.

**Four things that must be decided, not discovered:**

1. **`.swp.json` needs a version flag.** Every saved plan carries the old
   semantics. Opening `Uddipan_project.swp.json` after the change would
   reinterpret ₹25L as a net target and withdraw ~₹30L to hit it.
2. **Withdrawals become return-dependent.** They are not today — measured during
   §2.1: `swp_cash_personal` was bit-identical before and after a metric change
   that moved every other number. All five golden snapshots move, and the
   conservation invariants need re-derivation, because "money withdrawn" stops
   being an input.
3. **Corpus exhaustion needs a defined answer.** When the portfolio cannot supply
   the target the loop diverges. The HUF top-up currently handles shortfall; that
   path would move inside the solve, and "spend down faster to hit the target" is
   a plausible-but-wrong behaviour it could produce.
4. **The tax-optimal split optimiser already perturbs withdrawals** to minimise
   tax. Two solvers on the same variable needs a decided precedence.

**Phase B is much safer after Phase 3.1** — the outer loop is verifiable
precisely because the engine is a pure function of state today.

---

## 3. Phase C — solver interactions

Precedence between the net-income solve and the tax-optimal split optimiser; HUF
top-up inside the solve; exhaustion semantics. Cannot be specified until B's
shape is settled.

## 4. Phase D — Monte Carlo horizon

18 sites. `mu_det`, `sigmas`, `floors`, `caps` are all `np.full(30, …)`;
`corpus_p50` and friends are 30-element; `mc_history.json` and the five bootstrap
snapshots assume it.

## 5. Phase E — sensitivity analysis horizon

`run_sensitivity()` and `SensitivityDialog` share the same 30-year assumption.

---

## 6. §2.4 — the GUI half, still outstanding

`chunk_defaults.py` computes the approved per-Option table and **nothing reads
it**. Four sites in `main.py` still hardcode `"min_return": 6.85` — lines 3624,
3667, 4582, 4742.

**The structural problem:** `ChunkTableWidget` takes
`make_default_chunk: callable(year_from, year_to) -> dict`. That is all the
context a default gets. But the §2.4 spread depends on *how many chunks there are
and which one this is* — a chunk starting at FY1 takes `rf + 1.25` in Option 1 and
`rf + 2.10` in Option 4. Same range, different spread. `(yf, yt)` cannot
distinguish them, and `_add_chunk()` appends one row at a time, so the widget
never knows the user is "building Option 3".

**It needs an Option selector** — combo or four buttons — replacing the whole
table via `default_chunks_for_option(n)`, which exists and is tested.
`_rebuild_chunk_table()` is the hook; `set_data()` already takes a full list.
~30 lines, none testable without PySide6.

With `risk_free_rate` now 0.0535, the defaults are 6.75% / 7.15% / 7.45% / 7.65%
depending on Option and chunk.

Also unresolved: there is no `AllocationChunk.min_return` field. `main.py:4742`
sets it as a bare instance attribute; `dialogs.py:1378` reads it with
`getattr(chunk, 'min_return', 0.0685)` — a **fraction** where the GUI dicts hold
**percent**.

---

## 7. Phase 3.1 — decompose `Engine.run()`

**The highest structural value remaining.** ~1500 lines.

Four of the five engine defects found so far share one shape: **two mutable
representations of the same money, updated by different rules**. Making the
aggregates a computed property over `p_fund_buckets` retires the shape rather
than the instances.

Everything needed to do it safely now exists: five golden snapshots, 35
conservation invariants, five MC bootstrap snapshots. **If a snapshot moves, the
refactor is wrong.**

Supporting evidence: the expected-return metric was written out **seven times**;
collapsing it to one definition took an afternoon and moved nothing. The
other-income flattening was written out **five times**, and collapsing it exposed
a latent bug — `_optimal_split()` had hoisted `taxable_other` out of its per-FY
loop under the comment `(constant)`, correct only while the streams were flat.
`Engine.run()` is the larger instance of the same problem.

**Phase 3.2** (STCG on the SWP path) is blocked on 3.1.

---

## 8. Remaining refactor phases

- **Phase 2.1 — horizon constant.** Subsumed by A1. `engine.py` partly done;
  `glide_path.py` untouched.
- **Phase 2.3 — delete dead code.** The list is written down:
  `ruff check --select F841 .` enumerates 20 dead locals across 11 files, plus
  `allocate_funds_v0.9.py`. **Delete the `F841` ignore from `ruff.toml` when this
  lands** — it is commented in the file, but it is the kind of thing that
  silently becomes permanent.
- **Phase 2.4 — doc drift.**
- **Phase 4 — GUI threading.** Would benefit from `pytest-qt` landing first.
- **Phase 5 — optimiser property tests and a sigma study.**

---

## 9. Findings recorded, deliberately not fixed

1. **`navs.get(fn, 1.0)` appears 16 times in `engine.py`.** A missing NAV
   silently prices a fund at 1.0 in money paths. Making all sixteen raise is far
   larger than it sounds, and some may be legitimate (a fund with no NAV yet in a
   transition month). Needs its own scoped pass.

2. **Seven config keys are read by nothing** — `optimizer_default_user_cap`,
   `optimizer_min_personal_fraction`, `optimizer_max_huf_corpus_drain`,
   `optimizer_grid_step`, `optimizer_default_iterations`,
   `rebalance_no_trade_band`, `default_cagr_fallback`. Six configure the
   Tax-Optimal Split Optimizer, which runs but does not take its parameters from
   there — **tuning `optimizer_default_user_cap` changes nothing and says
   nothing.** Recorded in `configuration.UNUSED_KEYS`.

3. **~33–40% of Monte Carlo bootstrap draws land exactly on a band edge.**
   `_per_chunk_sigmas()` takes σ from the funds' own `std_dev` (~1.0–1.5%) while
   the bootstrap draws from raw index history (debt index σ ≈ 3%). The band is
   narrower than the distribution it clamps. The module docstring claims the
   bootstrap "preserves historical volatility / tail shape"; at these rates it
   substantially does not.

4. **Every fixture scenario is 100% debt**, so `_block_bootstrap_returns()`
   weights the Nifty series by zero — adding 50pp to every Nifty year moves the
   projection by exactly nothing. Yet `run_monte_carlo()` gates on
   `eq_hist is not None and dbt_hist is not None`, so an offline run silently
   falls back to log-normal because a series that would be multiplied away could
   not be downloaded.

5. **"Conservative mode" is arithmetically identical to Historical** for every
   fund carrying no STT charge — in this dataset, all of them.
   `Min_CAGR_3Y/5Y/7Y/10Y` is computed and exported by `get_funds_data.py` and
   never imported. ~6 lines in `import_fund_metrics()` would give the toggle
   something real to mean.

6. **144 of 801 funds (18%) have no percentile at all** after the §2.5
   reading-count guard — funds with 36–58 months of history, enough for one 3Y
   window but not 24 of them. They fall back permanently to the §2.1 tier. Zero
   of the 33 funds in the live plan are affected, which is why the threshold was
   left at 24.

7. **`pandas` FutureWarning at `get_funds_data.py:760`** —
   `pd.concat([fallback, overnight_rf])` with empty entries, in the
   debt-benchmark path the MC bootstrap depends on. Small, self-contained.

8. **`get_funds_data.py`'s CLI defaults have rotted** — `INPUT_FILE` and friends
   point at `../Personal/`, which does not exist. The GUI supplies absolute
   paths, so only command-line use is affected. Working invocation:
   ```bash
   python get_funds_data.py \
     --input  Uddipan/Schemes_and_Funds/mutual_funds_min750cr.csv \
     --output /tmp/out/Fund_Metrics_Output.csv
   ```

9. **Two `Fund_Metrics_Output.csv` schemas exist.** The repo-root one is older and
   lacks `Min_CAGR_*`; the current one is `Uddipan/Fund_Metrics_Output.csv`.
   `config.allocator_default_input` points at the root one.

---

## 10. Housekeeping

- `LICENSE` is absent but referenced by all source-file headers.
- `UBags/swp-retirement-plan` is still public with the source in it.
- `docs/` exists but is empty; `docs/DEFECTS.md` referenced by commit `be54844`
  was never committed.

---

## 11. Gotchas that have already cost hours

1. **`.gitignore` is an allowlist.** Line 2 is `*`; everything tracked exists
   because of a later `!` negation, and markdown is admitted *per location*. It
   has silently swallowed three files: `tests/golden/fixtures/mc_history.json`
   (leaving committed snapshots with nothing to reproduce them from),
   `archive/README.md`, and `HANDOVER.md`. **Always
   `git status --porcelain -uall`** before committing a new file type or
   directory — plain `git status` collapses untracked directories and shows
   `?? archive/` whether or not everything inside it is visible.

2. **Silent-drop whitelists are a recurring pattern**, not a one-off. Three so
   far: `.gitignore`; the CSV `cols` list in `get_funds_data.main()`
   (`reindex(columns=cols)` discarded five computed columns with no warning); and
   `known_keys` in `AppState.from_dict()`. Assume any new field needs registering
   somewhere, and test the round-trip.

3. **`git add --renormalize` is not retroactive.** Five files were stored with
   CRLF while `.gitattributes` already declared `eol=lf`. Content committed
   before a rule exists stays as-is until explicitly renormalised, so those files
   re-dirtied on every `status` and blocked a merge. When ruff finally rewrote
   them, git reported 2064 changed lines for one file against maybe fifteen real
   ones. Normalise in its own commit or the diff is unreviewable and `git blame`
   is destroyed.

4. **Never stash tool output across a change to the files that tool touches.**
   Stashing a ruff run, renormalising line endings, then `git stash pop`
   conflicts by construction. Re-running the tool is always cheaper than merging
   its output and cannot produce a wrong merge. Same for golden snapshots: if you
   are resolving a conflict in `tests/golden/snapshots/`, the answer is
   `--regen-golden` on the merged code, never a hand-picked hunk.

5. **`ruff check` exits 1 whenever it finds anything.** So
   `ruff check --statistics . && ruff check --fix .` halts before the fix runs.
   Only the final bare `ruff check .` is a gate; diagnostic and fix passes need
   `|| true` or their own line.

6. **`compileall .` walks `.venv`** and dies on PySide6's Jinja templates. Use
   `python -m compileall -q $(git ls-files '*.py')`.

7. **Pin every git command with `git -C <path>`.** A `cd ~/project` block pasted
   after one that left the shell in `/tmp` merged into a clone with no local
   branch of that name. Symptoms: `git push` says "Everything up-to-date" and
   lint reports hundreds of errors because `ruff.toml` only exists on the
   unmerged branch.

8. **A project bundle can be generated today from a stale source.** The export
   tooling ran against `main` while all the work sat on an unmerged branch, so the
   "latest codebase" was byte-identical to the session-start copy for four rounds.
   **Two-second staleness test: grep the bundle for the most recent thing you
   added.**

9. **`grep -c` counts lines, not occurrences** — and an f-string like
   `out[f"Pctile_CAGR_{label}"]` means the literal never appears in source at all.

10. **A skip in the environment where you verify is a hole, not a pass.** A test
    that skipped for a missing dependency hid a defect that produced 81 errors on
    the user's machine.

---

## 12. What is not verified, and cannot be from the assistant's side

- **The GUI.** `test_smoke.py` imports every dialog module and exercises nothing.
  ~50 imports were removed from six Qt modules by ruff with no test driving what
  they do. A manual pass through calculations, charts, the fund dialog,
  sensitivity and Monte Carlo would close it. `pytest-qt` is installed and unused.
- **`get_funds_data.py` against live AMFI** — no network, no NAV cache in the
  assistant's environment. Percentile arithmetic was validated by recomputing from
  `Uddipan/rolling_data.json` and confirmed against a real 801-fund run.
- **The block-bootstrap path with a live fetch.** `mc_history.json` is a frozen
  capture.

---

## 13. Live figures, for orientation

- `risk_free_rate` = **0.0535**, measured (P33 of the cross-fund monthly average,
  127 months, 33 liquid funds). Rewritten automatically on every
  `get_funds_data.py` run; a second run logs "unchanged".
- Corpus ≈ ₹1500L over 30 years. Requirement ₹25L rising to ₹40–45L. Other income
  ₹15.3L (taxable ₹6.8L: interest 3.5, pension 1.3, rental 2.0; tax-free ₹8.5L).
  HUF other income: none.
- Adding ₹5L of taxable other income raises FY1 personal net cash by only
  **₹0.52L** — it raises the FD benchmark, which raises `tax_saved`, which is
  transferred to the HUF and deducted from personal net cash.
- All five golden scenarios are 100% debt.
- **§2.5 is committed but not in effect.** The engine reads `Pctile_CAGR_*` and
  no saved plan has them, so it still projects on the §2.1 tier. Switching on
  requires re-running `get_funds_data.py` to `Uddipan/Fund_Metrics_Output.csv`
  and then **Import Fund Metrics** in the GUI. Every projection drops when you do.
  Worth doing when there is time to examine the resulting plan properly.
