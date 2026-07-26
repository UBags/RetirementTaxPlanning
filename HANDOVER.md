# HANDOVER — RetirementTaxPlanning

_Supersedes the session-2 handover. Everything below reflects `main` at commit
`6137be8`, CI green, 321 tests passing._

---

## 0. What changed since the last handover

Four approved specifications and two refactor phases landed. Three defects were
found and fixed that nobody was looking for. One specification (§2.1) was
implemented and then **superseded within the same session** by a better metric —
read §2.5 before trusting anything §2.1 says.

| item | status |
|---|---|
| §2.1 return metric | implemented, then superseded by §2.5 |
| §2.2 sensitivity + MC bounds | **done** |
| §2.3 Other Income growth | **still open — the only unimplemented spec** |
| §2.4 default chunk structures | done except the GUI wiring |
| §2.5 P33 through-the-cycle metrics | measurement done, consumption not wired |
| Phase 1.2 network/cache isolation | **done** |
| Phase 2.2 config schema validation | **done** |
| ruff | adopted, CI-enforced, tree clean |

Test count went 120 → 321. All of `main` is merged and CI-verified; the earlier
branch chain (`spec/2.1` → `spec/2.2` → `phase/1.2`) sat unmerged for a whole
session and nothing had ever run on Python 3.10 until it landed.

---

## 1. What the system is

Unchanged from the previous handover. A PySide6 desktop application projecting a
30-year systematic withdrawal plan across a personal portfolio and an HUF, with
Indian capital-gains tax modelled per fund lot.

### Key files, current

| file | role |
|---|---|
| `engine.py` | the simulation. `Engine.run()` is still ~1500 lines |
| `models.py` | `AppState`, `FundEntry`, `AllocationChunk`, persistence |
| `monte_carlo.py` | stochastic projection; block bootstrap + log-normal |
| `allocate_funds.py` | MILP fund selection |
| `get_funds_data.py` | fetches NAV history, computes all fund metrics |
| `percentile_metrics.py` | **new** — through-the-cycle P33 metrics |
| `chunk_defaults.py` | **new** — per-Option default chunk structures |
| `configuration.py` | config singleton; now validates at load |
| `archive/` | **new** — superseded code kept with its provenance |

### Test infrastructure

```
tests/test_smoke.py               import + config guards
tests/test_chunk_defaults.py      §2.4 and Phase 2.2
tests/test_percentile_metrics.py  §2.5 maths
tests/test_percentile_wiring.py   §2.5 in get_funds_data
tests/golden/test_engine_golden.py     5 frozen snapshots
tests/golden/test_conservation.py      35 money-conservation invariants
tests/golden/test_return_metric.py     §2.1
tests/golden/test_mc_bounds.py         §2.2
tests/golden/test_mc_isolation.py      Phase 1.2 + MC bootstrap snapshots
```

`pytest -q` → **321 passed**. `ruff check .` → clean.

---

## 2. Specifications

### 2.1 Return metric — DONE, THEN SUPERSEDED

`Engine._fund_annual_return()` was moved from point-in-time `cagr_5` to
`min(Avg_CAGR_3/5/7/10Y)`, matching `AllocationChunk.optimized_yield()` — the
figure the UI shows. Before the change the planner projected one number on
screen and simulated another (7.932% vs 6.813% on Opt2).

Impact was large: personal depletion pushed out 2–3 years, terminal HUF corpus
roughly doubling. The doubling is a **depletion-timing effect, not a return
effect** — ~1.28× is compounding on +0.86pp, the remaining ~1.45× is the HUF no
longer having to bail out an exhausted personal side for the last seven years.

**This is now superseded.** §2.5 replaces `min(Avg_CAGR_*)` with
`min(Pctile_CAGR_*)`. The engine has not yet been moved; see §2.5.

Two related defects were fixed with it: `get_funds_data.py` described
`Worst_Exp_Ret_%` as `min(1Y,3Y,5Y,10Y CAGR)` when the code uses `Avg_CAGR_*`
and excludes 1Y; and the mode-toggle tooltip called Conservative mode a
stress test.

**Finding: "Conservative mode" is now arithmetically identical to Historical**
for every fund carrying no STT charge — in this dataset, all of them.
`Min_CAGR_3Y/5Y/7Y/10Y` is computed and exported by `get_funds_data.py` and
never imported. About six lines in `import_fund_metrics()` would give the toggle
something real to mean.

### 2.2 Sensitivity and Monte Carlo bounds — DONE

Band is now asymmetric: `floor = mu − 2.5σ` (was 3.0), `cap = mu + 3.0σ` (did
not exist). Both applied **after** per-FY centring, so both are measured from
`mu_det` rather than the bootstrap sample mean. Sensitivity defaults are
−1.00% / +1.50%.

The cap is the half with teeth. `_lognormal_returns()` was unbounded above, so a
low-σ debt portfolio could draw an implausible year that compounded through the
rest of the path and lifted the median, not just the P95.

**Finding: ~33–40% of bootstrap draws land exactly on a band edge.**
`_per_chunk_sigmas()` derives σ from the funds' own `std_dev` (~1.0–1.5%) while
the bootstrap draws from raw index history (debt index σ ≈ 3%). The band is
narrower than the distribution it clamps. The module docstring claims the
bootstrap "preserves historical volatility / tail shape"; at these rates it
substantially does not. §2.2 widened the effect by adding the cap; the floor was
already clipping ~20%.

**Finding: every fixture scenario is 100% debt**, so `_block_bootstrap_returns()`
weights the Nifty series by zero. Adding 50pp to every Nifty year moves the
projection by exactly nothing. Yet `run_monte_carlo()` gates on
`eq_hist is not None and dbt_hist is not None`, so an offline run silently falls
back to log-normal because a series that would be multiplied away could not be
downloaded. Filed as a test, deliberately not fixed — changing that gate changes
which generator real runs use.

### 2.3 "Other Income" per-stream growth rates — STILL OPEN

**The only unimplemented specification.** Unchanged from the previous handover.
Self-contained: models, dialog, engine, tests. It will move projected numbers, so
it wants §2.1's treatment — behaviour commit, then a separate reviewed snapshot
regeneration.

Good candidate to fold in while there: the pandas `FutureWarning` at
`get_funds_data.py:760`, `pd.concat([fallback, overnight_rf])` with empty
entries, in the debt-benchmark path the MC bootstrap now depends on.

### 2.4 Default chunk structures per Option — DONE except GUI wiring

`chunk_defaults.py` implements the approved table, anchored on
`risk_free_rate + spread`:

```
Option 1  FY1-30                              rf + 1.25
Option 2  FY1-15, FY16-30                     rf + 1.60 / 1.00
Option 3  FY1-10, FY11-20, FY21-30            rf + 1.90 / 1.50 / 1.00
Option 4  FY1-7, FY8-15, FY16-23, FY24-30     rf + 2.10 / 1.60 / 1.25 / 1.00
```

Boundaries scale off `config.max_simulation_years` rather than hardcoding 30, so
a shorter plan does not keep FY24-30. At 30 the scaling is exact.

**The spec described a field that does not exist.** There is no
`AllocationChunk.min_return`: `main.py:4744` sets it as a bare instance
attribute and `dialogs.py:1378` reads it with `getattr(chunk, 'min_return',
0.0685)` — a *fraction* where the GUI dicts hold *percent*. That inconsistency
is recorded, not fixed. The real home of the floor is the chunk-definition dicts,
which hardcode `"min_return": 6.85` in four places in `main.py`.

**NOT DONE: the GUI wiring.** Those four sites still hardcode 6.85. Until they
are wired, `chunk_defaults.py` computes a table nothing reads.

### 2.5 Through-the-cycle P33 metrics — NEW, partially done

Supersedes the metric choice in §2.1. Two quantities, both at a configurable
percentile, decided at **P33** — roughly a two-in-three chance the realised
figure is higher, so plans come in ahead of projection rather than behind.

**`risk_free_rate`** — the through-the-cycle liquid-fund yield. For every liquid
fund take the rolling 3Y CAGR series; align by window **end date**; average
across funds within each calendar month; take P33 of that monthly series.

*Average first, percentile second.* The orders are not interchangeable:
percentile-then-average measures fund dispersion, average-then-percentile
measures the rate cycle, and the rate cycle is what liquid funds track. A test
proves the two differ on a constructed case.

**Per-fund expected return** — P33 of each of the 3Y/5Y/7Y/10Y rolling series,
then the lowest of the four.

Liquid funds match `"liquid"` as a substring of the AMFI Fund Type — not the
full `"Debt Scheme - Liquid Fund"`, because AMFI has reorganised category
prefixes before. Money Market, Overnight, Low Duration, Ultra Short and Short
Duration are excluded explicitly.

**Measured on real data** (recomputed from `Uddipan/rolling_data.json`, 30 liquid
funds):

```
fund                                   Min     P33     Avg    n
UTI Liquid Fund                       3.93    5.16    6.09   91
Axis Liquid Fund                      3.95    5.86    6.35  125
Aditya Birla Sun Life Liquid Fund     3.98    5.89    6.38  125
PGIM India INSTA CASH FUND            6.85    6.99    6.97    4   <- see below

cross-fund monthly average, 125 usable months:
  min 3.57%   P33 5.50%   mean 6.10%   max 8.65%
```

So `risk_free_rate` measures to **~0.0550**, against the `0.0665` placeholder
currently in the config file. 1.15pp lower, which loosens every chunk
`min_return` by the same amount once the write-back lands.

**Do not expect `Pctile ≤ Avg`.** On a left-skewed rolling series — a long tail
of bad windows dragging the mean down, which is what debt funds produce — P33
sits above the mean. The invariant that holds is `Min ≤ Pctile`.

**Resolution matters and is guarded.** Ten years of history gives the 3Y series
~85 readings and the 10Y series ~13; P33 of thirteen is the fourth value. Since
the per-fund metric takes a `min()`, the least reliable input would otherwise win
whenever it happened to be low. `fund_expected_return()` requires 24 readings on
3Y, 18 on 5Y, 12 on 7Y and 10Y. PGIM India INSTA CASH FUND above is the live
example — four windows, and a "P33" above its own mean.

**Config tunables** (three keys, separate on purpose — they are equal today and
are not the same decision; lowering the first loosens MILP floors, lowering the
second lowers the projection):

```
risk_free_rate_percentile = 33
fund_return_percentile    = 33
risk_free_min_funds       = 10
```

#### What is done

- `percentile_metrics.py` — all the maths, pure, no I/O, fully tested.
- `get_funds_data.py` — `_rolling_stats()` keeps window end dates and exports
  `Pctile_CAGR_3Y/5Y/7Y/10Y` plus a `Return_Pctile` column recording which
  percentile produced them.

#### What is NOT done

1. **Nothing writes `risk_free_rate`.** It is still the `0.0665` placeholder.
   The measurement must run at the end of `get_funds_data.py` and write back to
   the config file, informing the user. Decision already taken: **write on
   import, and inform; the user does not get to edit it.**
2. **`models.import_fund_metrics()` does not read `Fund Type`.**
   `FundEntry.amfi_fund_type` is always `None` even though the column is column
   1 of `Fund_Metrics_Output.csv`. Prerequisite for classifying liquid funds
   in-app.
3. **`models.import_fund_metrics()` does not read `Pctile_CAGR_*`.**
4. **`engine._fund_annual_return()` still uses `min(Avg_CAGR_*)`**, i.e. §2.1.
   Moving it to `min(Pctile_CAGR_*)` is the change that supersedes §2.1, lowers
   every projection, and moves all five golden snapshots again.

---

## 3. Defects found and fixed this session

None of these were on any list. All were found by tooling or by reading code
while doing something else.

### 3.1 `import get_funds_data` hijacked stdout and root logging

`logging.basicConfig` ran at **module level**, building
`io.TextIOWrapper(sys.stdout.buffer, ...)` in its handlers list. `TextIOWrapper`
takes ownership of the buffer and closes it on garbage collection. Under pytest
`sys.stdout` is a per-test capture object whose `.buffer` is a temp file pytest
still needs, so importing the module closed pytest's capture and every
subsequent test errored in setup *and* teardown with
`ValueError: I/O operation on closed file`. **81 errors from one import.**

The trigger is *building the argument list*, not configuring logging:
`basicConfig()` is a no-op once the root logger has handlers, but the wrapper is
constructed regardless.

Two further import-time effects removed: root logging reconfigured
process-wide for every importer, and `fund_analyser.log` created (`mode="w"`) in
whatever the cwd happened to be.

`monte_carlo._fetch_debt_index_annual_returns()` performs that import for the
embedded debt-index table, so the blast radius was the whole Monte Carlo path.

Fixed by moving the block verbatim into `configure_logging()`, called from
`__main__`. Pinned by `test_no_module_configures_logging_at_import`, which greps
every module for column-0 `logging.basicConfig`.

### 3.2 `_run_monte_carlo`'s error dialog raised `UnboundLocalError`

`main.py` imports `traceback` at module scope, but `_run_monte_carlo` contained a
second, redundant `import traceback` in a later `except` block. Python binds a
name assigned anywhere in a function as local for the whole body, so the handler
~100 lines earlier never reached the module-level import:

```python
QMessageBox.critical(self, "Monte Carlo Error", traceback.format_exc())
# -> UnboundLocalError: cannot access local variable 'traceback'
```

**Any exception from `run_monte_carlo()` produced an UnboundLocalError instead of
the error dialog, discarding the original exception.** Found by ruff F823. All
four function-local `import traceback` statements removed.

### 3.3 Rolling-window series desync in `_rolling_stats()`

```python
if np.isfinite(cagr): raw_cagrs.append(cagr)
if np.isfinite(sd):   raw_stds.append(sd)
...
clean_idx = [i for i, s in enumerate(raw_stds) if s <= std_ceil]
cagrs   = [raw_cagrs[i] for i in clean_idx if i < len(raw_cagrs)]
```

Conditional appends, so the lists desync whenever a window has a finite std and a
non-finite CAGR — a zero or missing opening NAV does it. From that point
`raw_cagrs[i]` is a different window from `raw_stds[i]` and the outlier filter
discards CAGRs from windows it never meant to touch. `if i < len(raw_cagrs)`
suppressed the IndexError and hid the fault.

Fixed while adding window end dates, which required positional alignment anyway.
**This can move `Avg_CAGR_*`/`Min_CAGR_*` for affected funds**, and those feed
the engine — projections may shift after the next `get_funds_data.py` run.

---

## 4. Other findings, not fixed

1. **Seven config keys are read by nothing.**
   `optimizer_default_user_cap`, `optimizer_min_personal_fraction`,
   `optimizer_max_huf_corpus_drain`, `optimizer_grid_step`,
   `optimizer_default_iterations`, `rebalance_no_trade_band`,
   `default_cagr_fallback`. Six configure the Tax-Optimal Split Optimizer, which
   runs but does not take its parameters from there — tuning
   `optimizer_default_user_cap` changed nothing and said nothing. Recorded in
   `configuration.UNUSED_KEYS`; `test_unused_keys_really_are_unused` fails the day
   one acquires a reader.

   Note this reverses the obvious reading: `test_smoke.py` named 16 keys against a
   23-key file, which looks like drift. It was not — the 16 are exactly the 16
   that anything reads.

2. **`navs.get(fn, 1.0)` appears 16 times in `engine.py`**, not the two the
   previous handover named. A missing NAV silently prices a fund at 1.0 in money
   paths. Making all sixteen raise is a much larger behaviour change than "it
   should raise" implies, and some may be legitimate (a fund with no NAV yet in a
   transition month). Needs its own scoped pass.

3. **`Fund_Metrics_Output.csv` at the repo root is an older schema** with no
   `Min_CAGR_*` columns. The current one is `Uddipan/Fund_Metrics_Output.csv`.
   `config.allocator_default_input` points at the root one.

4. **`get_funds_data.py`'s CLI defaults have rotted.** `INPUT_FILE` and friends
   point at `../Personal/`, which does not exist. The GUI supplies absolute
   paths, so only command-line use is affected.

5. **20 unused locals (`F841`)** across 11 files, ignored in `ruff.toml` and
   queued for Phase 2.3. **Delete that ignore when 2.3 lands** — it is commented
   in the file, but it is the kind of thing that silently becomes permanent.

---

## 5. Repository policy

Unchanged, plus:

**`archive/`** — superseded code kept with its provenance, rather than deleted.
The history has the code; what it lacks *findably* is why an approach was
replaced. Rules in `archive/README.md`: nothing imported, no `__init__.py`, every
file states what replaced it and why, excluded from ruff, still valid Python so
`compileall` passes, tracked in git. Distinct from `older_versions/`, which is
deliberately untracked.

**ruff** — `ruff.toml` selects `F` (pyflakes) and `I` (isort) only. `UP` and `B`
would add 350+ findings (`List` → `list` across every file), which is a
formatting decision, not a lint fix. `E501` stays off: 856 hits, mostly aligned
numeric tables and box-drawing rules where wrapping makes them worse. CI runs
`ruff check .` before `compileall`.

---

## 6. Gotchas that cost time

Items 1–15 from the previous handover still stand. Added:

16. **`.gitignore` is an allowlist.** Line 2 is `*`; everything tracked exists
    because of a later `!` negation. Markdown is admitted *per location*
    (`!ReadMe.md`, `!docs/*.md`, `!archive/*.md`) — there is no blanket `!*.md`.
    This has silently swallowed a file **twice**: `tests/golden/fixtures/
    mc_history.json` (committed snapshots with nothing to reproduce them from)
    and `archive/README.md`. **Always `git status --porcelain -uall`** before
    committing a new file type or directory — plain `git status` collapses
    untracked directories and shows `?? archive/` whether or not everything
    inside it is visible.

17. **Five files were stored with CRLF** while `.gitattributes` already declared
    `eol=lf`. Attributes do not apply retroactively: content committed before a
    rule exists stays as-is until `git add --renormalize` rewrites it. So the
    rules governed new writes while five files sat in the ODB violating them,
    invisibly, until ruff rewrote them and git reported whole-file diffs — 2064
    changed lines for `get_amfi_fund_schemes_names.py` against maybe fifteen real
    ones. Normalise in its own commit or the real diff is unreviewable and
    `git blame` is destroyed.

18. **Never stash tool output across a change to the files that tool touches.**
    Stashing a ruff run, renormalising line endings, then `git stash pop`
    conflicts by construction — the renormalise rewrites exactly the files the
    stash holds. Re-running the tool is always cheaper than merging its output
    and cannot produce a wrong merge. Same applies to golden snapshots: if you
    are ever resolving a conflict in `tests/golden/snapshots/`, the answer is
    `--regen-golden` on the merged code, never a hand-picked hunk.

19. **`ruff check` exits 1 whenever it finds anything.** So
    `ruff check --statistics . && ruff check --fix .` halts before the fix ever
    runs. Only the final bare `ruff check .` is a gate; the diagnostic and fix
    passes need `|| true` or their own line.

20. **`compileall .` walks `.venv`** and dies on PySide6's Jinja templates
    (`__init__.tmpl.py`). Use `-x '(^|/)\.venv/'` or
    `python -m compileall -q $(git ls-files '*.py')`.

21. **A merge can be run in the wrong directory and silently do nothing.** A
    `cd ~/project` block pasted after one that left the shell in `/tmp` merged
    into a clone that had no local branch of that name. Symptoms: `git push`
    says "Everything up-to-date" and a lint run reports hundreds of errors
    because `ruff.toml` only exists on the unmerged branch. Pin every git command
    with `git -C <path>`.

22. **A project bundle can be generated today from a stale source.** The export
    tooling ran against `main` while all the work sat on an unmerged branch, so
    the "latest codebase" was byte-identical to the session-start copy for four
    rounds. Two-second staleness test: grep the bundle for the most recent thing
    you added.

---

## 7. Recommended order of work

**Next up: finish §2.5.** It is half-landed — the measurement exists and nothing
consumes it — and that is the worst state to leave anything in.

1. **§2.5 write-back.** `get_funds_data.py` computes `risk_free_rate` from the
   liquid-fund series and writes it to the config file, informing the user.
   Needs a config-file rewriter that preserves comments and ordering.
2. **§2.5 consumption.** `import_fund_metrics()` reads `Fund Type` and
   `Pctile_CAGR_*`; `engine._fund_annual_return()` moves to
   `min(Pctile_CAGR_*)`. **Moves every projection and all five golden
   snapshots** — behaviour commit, then a separate reviewed regeneration.
3. **§2.4 GUI wiring.** Four sites in `main.py` still hardcode `min_return: 6.85`.
4. **§2.3 Other Income growth** — the last unimplemented spec.
5. **Phase 3.1, decompose `Engine.run()`.** The highest structural value
   remaining, and everything needed to do it safely now exists: golden snapshots,
   35 conservation invariants, and MC bootstrap snapshots. Four of the five engine
   defects found so far share one shape — two mutable representations of the same
   money updated by different rules — and making the aggregates a computed
   property over `p_fund_buckets` retires the shape rather than the instances.
   **If a snapshot moves, the refactor is wrong.**
6. **Phase 3.2** (STCG on the SWP path), blocked on 3.1.
7. **Phase 2.3** dead code — the list is now written down:
   `ruff check --select F841 .` enumerates the 20 dead locals, plus
   `allocate_funds_v0.9.py`. Delete the `F841` ignore from `ruff.toml`.
8. **Phase 2.1** horizon constant — `engine.py` done, `glide_path.py` outstanding.
9. **Phase 4** GUI threading, **Phase 5** optimiser property tests.

### Housekeeping

- `LICENSE` is absent but referenced by all source-file headers.
- `UBags/swp-retirement-plan` is still public with the source in it.
- `docs/` exists but is empty; `docs/DEFECTS.md` referenced by commit `be54844`
  was never committed.

---

## 8. Verification status

`main` at `6137be8`. CI green on Python 3.10 and 3.12.

```
ruff check .    All checks passed!
pytest -q       321 passed
```

Verified from a clean clone, not just the working tree.

**What is NOT verified:**

- **The GUI.** `test_smoke.py` imports every dialog module but exercises nothing.
  ~50 imports were removed from six Qt modules by ruff and no test drives what
  they do. A manual pass through calculations, charts, the fund dialog,
  sensitivity and Monte Carlo would close it.
- **`get_funds_data.py` against live AMFI.** The percentile arithmetic was
  validated by recomputing from `Uddipan/rolling_data.json`, but the wired code
  has never fetched real NAV. First real run will be the first execution of that
  path.
- **The block-bootstrap path with real history.** `mc_history.json` is frozen and
  snapshotted, but it is a capture, not a live fetch.
