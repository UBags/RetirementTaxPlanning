# data_synthesizer — status

Built against `nav_sample.csv`: 7,305 calendar-daily observations, 241 month
ends, FY2007-08 to FY2025-26.

## What is here

| module | state |
| --- | --- |
| `metrics.py` | complete — every fidelity statistic, plus two uncertainty estimators |
| `docs/FIDELITY_CRITERIA.md` | complete — converted from the requirements document |
| `targets.py` | not written |
| `engine.py` | not written |
| `verify.py` | not written |

## Measured baseline

| | equity blend | debt slot 0 |
| --- | --- | --- |
| observations per year | 365.2 | 365.2 |
| CAGR | 11.88% | 8.24% |
| volatility | 18.15% | 2.73% |
| skew / excess kurtosis | −0.47 / +21.9 | −4.44 / +259.0 |
| max drawdown | −62.3% | −12.5% |
| VR(3/6/12/18) | 1.12 / 1.25 / 1.21 / 0.85 | 0.75 / 0.57 / 0.45 / — |
| ACF decay β on \|r\| | 0.148 | 0.251 |
| Hurst on \|r\| | 0.684 | 0.696 |
| leverage L(1) / L(20) | −0.078 / +0.006 | +0.023 / +0.046 |
| completed underwater spells | 164, median 4 days | 395, median 3 days |

The leverage effect has the right sign in equity and is absent in debt, which
is what theory predicts and a useful sign the metric layer is reading correctly.

## A RETRACTION, recorded because the reasoning is worth not repeating

Across three earlier sessions the case was made that the existing
`nav_feed.extend_series` fails to preserve the term structure of variance, and
that this matters because the redemption gate is a mean-reversion rule. The
evidence was a table comparing real variance ratios against synthetic ones:
equity 0.85 against 0.92 at eighteen months, debt 0.45 against 1.14 at twelve.

**That case does not survive measurement.** Nineteen years contains too few
independent long-horizon windows to distinguish those values. Across contiguous
nine-year sub-periods the real series itself spans:

| statistic | full-sample | real range across 9-year windows | synthetic |
| --- | --- | --- | --- |
| equity VR(18) | 0.85 | 0.61 – 0.99 | 0.92 |
| equity VR(12) | 1.21 | 0.70 – 1.44 | 1.12 |
| debt VR(12) | 0.45 | 0.22 – 2.38 | 1.14 |
| debt VR(6) | 0.57 | 0.45 – 1.78 | 1.26 |

Every synthetic value is inside. The generator was accused of a defect that the
data cannot demonstrate.

**A second error, caught by the first.** The initial uncertainty band came from
a moving-block bootstrap, which returned intervals that did not contain their
own point estimates for debt — 0.45 against a lower bound of 0.47, 0.57 against
0.65. The cause: a moving-block bootstrap DESTROYS the long-range dependence it
is being asked to put a band around. Its interval is centred on the random-walk
null, which is the same null the generator produces, so the comparison was
rigged toward acquittal. Any statistic measuring dependence must be
bootstrapped by CONTIGUOUS subsampling, never by reshuffling.

Consequence: the rolling-quantile occupancy deviations measured earlier (total
absolute deviation 50 to 70 across deciles) were computed on ONE seed with no
uncertainty attached and must be re-tested the same way before they are
believed. They are currently unproven, not established.

## Defects in the uploaded engine

These do not depend on any confidence interval — the engine returns `nan` on
this project's data.

1. **Divergent GARCH recursion.** The regime volatility multiplier is fed back
   into the conditional-variance state, so crisis days multiply variance by
   roughly 1.85² × (β + αz²) ≈ 3.1 and ratchet permanently. Overflows to `nan`
   within a 35-year path on real NAVs. It survived upstream testing only
   because the test data was low-volatility Gaussian noise.
2. **252 trading days assumed.** These NAV series are calendar daily, 365.2
   observations per year. The engine reported the equity blend at 8.05% CAGR
   and 15.07% volatility where the values are 11.88% and 18.15%. Worse than the
   crash: without the `nan` it returns a plausible number that is 30% wrong.
3. **No cross-fund correlation.** Each series is synthesized independently.
   `nav_feed.extend_series` draws block starts ONCE across all series so debt
   and equity still co-move — essential for a two-purse model, and lost here.
4. **Volatility is never targeted**, only CAGR. The upstream harness tolerates a
   1.5-point miss; `extend_series` solves sigma to 1e-9.
5. **No seed parameter** — global `numpy.random`, so no run is reproducible.
6. **GARCH parameters hardcoded**, not fitted: ω = 0.05σ², α = 0.05, β = 0.85.
7. **EVT smoothing is applied i.i.d. per observation**, re-randomising every
   draw beyond the 2% thresholds and destroying clustering exactly in the tail
   it exists to model.
8. **The verification harness is circular.** Its "historical" series are GBM
   with two multiplicative crash windows: no volatility clustering, no long
   memory, VR ≈ 1 by construction. It validates the reproduction of stylised
   facts against data possessing none of them, which is why it reports 20/20.
9. **The K-S underwater test fails on real data** — p = 0.012 equity, 0.005
   debt — against the document's own p > 0.05 threshold.

## What the criteria document gets right

Volatility-clustering ACF decay, the Hurst exponent, the leverage effect,
underwater DURATION rather than depth alone, EVT tails and regime dwell times
are the right targets, and are a genuine expansion of what was being measured
before. They are adopted in `docs/FIDELITY_CRITERIA.md`.

## Open question that now governs the design

Half the criteria cannot be enforced on nineteen years. Before an engine is
written, each threshold in the validation matrix needs classifying as
ENFORCEABLE or UNMEASURABLE at this sample size, by running the contiguous
subsampling above on every statistic. Building a generator to hit targets that
cannot be estimated is how a synthesizer acquires four significant figures of
false precision.

---

# Round 2 — the engine, and what the harness convicted it of

`engine.py` and `verify.verify_ensemble` are in. 78 tests.

## The design rule

The engine carries ONLY what the harness can enforce. No GARCH, no EVT tails,
no regime switching. A block bootstrap of screened real returns inherits
volatility clustering, the leverage effect and fat tails by resampling the real
thing rather than modelling it, and every component the uploaded engine added on
top was untestable in principle and produced `nan` in practice. Components get
added when a test forces them, and the commit that adds one names the test.

## What the ensemble verifier found, on real data, 10 paths x 35 years

Exact criteria pass to 1e-9: CAGR lands on history minus delta, volatility
lands on history, for all five funds tested.

| criterion | real | ensemble band | verdict |
| --- | --- | --- | --- |
| equity ACF beta | 0.148 | [0.63, 1.18] | **FAIL** |
| equity Hurst | 0.684 | [0.73, 0.78] | **FAIL** |
| equity leverage(1) | −0.078 | [−0.09, −0.06] | pass |
| equity VR(3..18) | — | — | UNMEASURABLE |
| corporate ACF beta | 0.251 | [0.75, 1.35] | **FAIL** |
| corporate Hurst | 0.696 | [0.76, 0.82] | **FAIL** |
| money market Hurst | 0.629 | [0.79, 0.82] | **FAIL** |

**THE DEFECT IS VOLATILITY CLUSTERING PERSISTENCE.** Real absolute returns decay
at beta 0.148; the synthetic paths decay three to eight times faster. Clusters
break at every block boundary. For a withdrawal plan this is the difference
between a bear market that is a run of bad days and one that is a bad decade.

## Which criteria can convict, and it is not the ones argued for

VARIANCE RATIO IS UNMEASURABLE AT EVERY HORIZON ON EVERY FUND. Three sessions
were spent arguing the term structure of variance was the missing
characteristic. The ensemble bands span a random walk everywhere, so the
criterion cannot distinguish any generator from any other. ACF decay and the
Hurst exponent -- both from the uploaded requirements document, neither of them
in the original list -- are the ones with bands tight enough to fail a
generator. The uploaded criteria were better than the ones they were criticised
against.

## A third correction to the record

"Debt is mean-reverting, VR(12) = 0.45" describes slot 0, a corporate bond
fund. Money market slot 34 reads VR(12) = 4.90 -- strongly TRENDING, because an
accrual instrument's twelve-month return is the slowly-moving yield level while
its daily returns are noise. Debt was never one thing, and the redemption
gate's category groups exist for the same reason.

## Next

Fix the clustering decay. The candidates, in order of how much new machinery
they require: longer blocks; variable block length drawn per block; blocks
sampled from a fitted volatility state so a crisis block follows a crisis
block; a GARCH overlay. Each must be justified by moving ACF beta and Hurst
inside their bands WITHOUT moving leverage(1) outside its own, which currently
passes.
