# SESSION_LOG — Phases 1 to 4b-ii

Companion to `HANDOVER.md` and `HANDOVER_REVISION.md`. Those say what is true
and what the evidence is. This says **how the session actually went**, including
the parts that went wrong, because the failures were more instructive than the
successes and several of them are repeatable traps.

Written from the commits, the patchers and the measurements — not from recall
of the conversation. Where a number appears it came from a run.

---

## 1. What landed

| commit | what | snapshots |
|---|---|---|
| `87051b1` | pytest-xdist pinned, run in CI | none |
| `ac41ad7` | per-year requirement-delivery guard, 5 scenarios, 25 items | none |
| `bfbf226` | glide-path transition scheduler retired; 341 lines removed | **none** |
| `3a3813c` | secant step in `solve_schedule`, gated so it cannot fire yet | **none** |
| — | Phase 3: the final cash identity + a double debit | all ten |
| — | `PLAN_TOL = 1e-2` | none |
| — | conservation identity, `YearSummary` fields | none |
| — | Phase 4a `FolioSet` | **none** |
| — | Phase 4b-i folio-selection API | **none** |
| — | Phase 4b-ii the config gate, switched off | **none** |

Tests 652 → 722.

Eight of ten commits moved no snapshot at all. That is not luck; it is the
result of deliberately separating representation changes from behaviour
changes, and it is the single most useful habit to carry forward.

---

## 2. The two real defects found

### 2.1 The HUF transfer was debited twice

The largest finding of the session, and it predated all of it.

`net_p` subtracted `this_fy_saved`, so the solver withdrew an extra X to still
deliver the requirement. The engine then separately debited X from the per-fund
buckets as the actual transfer. Per year, with `tax_saved = X`:

```
departing the personal corpus   W + X
arriving with the user          W + OI - X
arriving in the HUF             X
arriving from outside           OI
```

Arriving: `W + OI`. Departing: `W + X + OI`. **X destroyed every year.**

`s0_1chunk` at FY30: personal `937.891 → 1162.282` (+224.39), HUF
`656.255 → 546.309` (−109.95), total `1594.146 → 1708.591` (+114.44). The HUF
loses the ~23L of transfers it no longer receives, compounded. Personal gains
twice that, because it stops sending the transfer *and* stops paying the
duplicate. The +114 is the destroyed stream recovered.

**Why no test caught it.** `test_monthly_corpus_flow_balances` asserts
`corpus_end == corpus_start − withdrawal`. Both outflows were booked as
withdrawals, so its residual sat at 5.7e-14 throughout. It balances the corpus
against *itself* and never crosses to the user's hand, which is where the money
was going. Its docstring claims it catches "exactly how the tax and
HUF-transfer leaks manifested". It cannot.

### 2.2 `redeem` dropped the remainder across folios

Phase 4b-i's `redeem` picked the cheapest folio and delegated. Where that folio
could not cover the amount, the rest was silently dropped. It shipped green and
was caught a round later. See §4.3 for why the verification missed it.

---

## 3. Five wrong hypotheses, in order

Phase 3 took five rounds of instrumentation. Every hypothesis was reasoned from
reading the code, and four of five were wrong.

1. **s.87A marginal relief drives the slope negative.** Quoted from §B1b,
   plausible, wrong. The slope was +2.0, not negative.
2. **A secant step will fix the non-convergence.** Correct in principle;
   didn't help, because the real problem was elsewhere.
3. **Cross-purse double-counting in the HUF top-up.** The decomposition showed
   `topup_from_huf = 0.0000` in every probed year.
4. **"No HUF column moved."** Asserted from pytest output that had truncated
   at 25 of 37 lines. FY3 `1.8648 → 0.0000` was in the twelve I couldn't see.
5. **Exit loads are the missing destination.** Measured `0.000000` in every
   scenario.

One probe was itself invalid: it compared a raw `Engine.run()` against a
snapshot produced through `project_plan`, so every "moved" line in it was
solver-versus-no-solver rather than before-versus-after.

**What actually found the defect:** `pytest --regen-golden` followed by
`git diff` on the ten snapshot CSVs. One pass, complete data, nothing
confounded — and the conservation arithmetic fell straight out of the FY30
totals.

The lesson is not "measure before hypothesising", though that would have
helped. It is that **the snapshot review is the primary diagnostic, not a
formality once the work is done.** Regenerate early and read the diff; probes
are for questions the diff has already narrowed. It is now `HANDOVER` §9.13.

---

## 4. Traps worth naming

### 4.1 Truncated output is not evidence

Twice, conclusions were drawn from output that had visibly truncated —
pytest's `...` after 25 diffs, and a `-q` summary that showed test names
without assertion text. Both produced confident wrong statements. If the tool
elides, get the whole artefact before concluding.

### 4.2 The solver had never been tested at precision

Every measured `d(net)/d(gross)` was exactly 1.000 before Phase 3, because the
tax term cancelled out of cash in hand. The first correction therefore landed
exactly on the answer — error `0.00e+00` in one pass. **`TOL = 1e-4` was never
exercised.** It was not a precision the engine had been shown to meet; it was
one the problem never asked for. Once tax stopped cancelling, the engine turned
out to have a response floor around 0.01 on a 12–25L target.

### 4.3 A synthetic fixture that doesn't resemble the workload proves nothing

4b-i was verified with 3000 randomised invest/redeem steps against a plain
`FIFOBucket`. Identical throughout — and worthless. Constant FIFO churn keeps
the queue front fresh, so no second folio ever opened and the multi-folio path
never ran once.

A realistic pattern — one large initial investment, small regular draws, annual
reinvestment — found the defect immediately: 240.5 units remaining against
228.3 for plain FIFO. **Shape the fixture like the plan**, not like a random
walk. Now `HANDOVER` §9.14.

### 4.4 A deleted test leaves a name behind

Twice, removing a test function left its name in the registry at the foot of
`test_swp_engine.py`. That is a `NameError` at import, not a syntax error, so
`py_compile` passes cleanly. Caught both times only by an explicit residue
grep after applying.

### 4.5 `.bak` is not a recovery mechanism

A patcher's `--revert` half-completed, restoring one file and refusing another,
leaving the tree in a state where the engine held the old behaviour and the
tests asserted the new one. Every phase starts from a clean commit, so
`git checkout --` is strictly more reliable and needs no hash to match.
`--revert` was dropped from later patchers.

### 4.6 Regeneration is only ever the answer when a *snapshot* test fails

Ten red assertions in a new test file prompted "do we need to regen?". No
golden test had failed; regenerating would have changed nothing. The engine was
right and the test was wrong — twice over, in fact: first it mixed a cash-basis
outflow with an accrual figure (tax is debited in April of the *following*
year), then it double-counted tax that was already inside `wd_*`.

---

## 5. What the exporter fix bought

`CopyFullProjectCode2MD.py` had a bug: `content[:-1] if content.endswith("\n")
else content` emitted **identical text** in both branches, so a missing final
newline was unrepresentable and every reader restored one. That is the +1 byte
on `engine.py`.

After the fix — per-file `bytes`, `sha256`, `eof_nl`, `crlf`, and an adaptive
fence — the bundle reconstructs **98 of 98 files byte-exact**. Phase 2 cost
three round-trips for hashes and anchors; every patcher since has been derived
straight from the bundle with an embedded post-hash, so it proves its own
output rather than trusting line arithmetic.

(The metadata immediately earned itself: it caught a CRLF-mangling bug in the
*reader*, not the writer.)

---

## 6. Method that worked

- **Separate representation from behaviour.** Every inert commit had a
  falsifiable claim attached — "no snapshot moves" — and if one had, that was
  a stop, not a judgement call.
- **Pin by SHA-256 before and after.** The patcher rebuilds in memory, checks
  against the embedded post-hash, and only then writes. "It applied" and "it
  produced exactly the intended bytes" become the same statement. This caught
  a real bug once, where a derive script's string-replace had no corresponding
  line hunk in the patcher.
- **Gate behaviour behind config.** `folio_cohort_threshold = 0.0` lets the
  whole folio mechanism land and be reviewed while provably changing nothing.
  Raising it later is a one-line diff whose entire content is snapshot
  movement.
- **Predict before running.** Stating what should move, and by roughly how
  much, turns a snapshot review from reading numbers into checking a claim.
  Several predictions were wrong, which is the point — a wrong prediction is
  information, an absent one is not.

---

## 7. Where it stands

Phase 4 is three-quarters done and **inert**. `folio_cohort_threshold = 0.0`,
one folio per fund, nothing changed yet.

Remaining, in `HANDOVER` §4.10 with full detail:

1. Three conversions of the lot-mutating sites, each guarded on
   `len(bk.order) > 1` so each commit stays inert. They are three different
   problems — one clean, one carrying a tax-budget tally that stops mid-lot,
   one working to a gain budget and stepping up basis in place.
2. Raise the gate. One-line config change; the entire diff is snapshot
   movement.
3. **Separately** — HIFO is a fiction. The rebalance sells sort all lots by
   gain and take the cheapest, which no AMC will do for you. Folio choice is
   the only lever. Every glided figure ever measured inherits that optimism,
   including the 57.40 → 24.42 in `HANDOVER_REVISION` §4.2.

**Do not combine raising the gate with the HIFO correction.** Two sets of
moving numbers in one diff is precisely the mechanism by which the double debit
stayed hidden.

---

## 8. Addendum — what happened after 4b-ii

Added when the session log was committed. §7 above was written at 4b-ii and is
left as it stood; this records where its predictions held and where they did
not.

**All three conversions landed** — `7f271a5`, `2cb57ce`, `1ba8718` — each
guarded and each inert.

**Only one of the three ever blocked the gate.** `_rebalance_portfolio`, which
held the other two, was called nowhere in the engine: its caller went with the
341 lines retired in `bfbf226`. §7's inventory came from grepping for lot
assignments, which finds assignments but not callers. See `HANDOVER` §9.15.

**§7 item 3 resolved itself.** HIFO lived only in `_rebalance_portfolio` and
`_estimate_transition_tax`, both dead. Archiving them to
`archive/rebalance_portfolio.py` (`73103bf`) removed HIFO entirely and moved no
snapshot — the opposite of the prediction that it would move every one. Dead
code cannot move a snapshot. 722 → 720.

**§4.3's lesson had a second half that was missed.** Every Phase 4 verification
was correctly shaped like the plan, and every one lived in a throwaway script.
Nothing in the suite exercised a multi-folio path until `5b72d42` added
`tests/test_folio_set.py`. A fixture shaped like the plan proves nothing once
the session ends unless it lives in the suite. 720 → 736.

**§3 hypothesis 5 was right about the fact and wrong about the cause.** Exit
loads did measure `0.000000` in every scenario — because the engine charges no
exit load anywhere on the live path. `exit_load_fraction` had exactly one
reader, inside the archived rebalancer, and is now in `UNUSED_KEYS`.
Structurally zero, not coincidentally so.

Only raising the gate remains.
