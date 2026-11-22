# Fidelity Criteria for Financial Time-Series Synthesis

Converted from `Synthetic_Series_Requirements.docx`. LaTeX has been rendered to
readable notation; the ASCII diagram has been replaced with a table; claims
about what is "implemented" have been moved to `../STATUS.md`, because in the
original they described an engine that had not been run against real data.

---

## Why this document exists

Matching volatility, a target CAGR shift, and rolling quantile distributions is
a reasonable baseline. It is not sufficient. Financial series carry non-linear
structure — volatility clustering, long memory, asymmetric response to losses,
slow recovery from drawdowns — that Gaussian and linear models do not reproduce.

Over a forward horizon of 10 to 60 years, small distortions in path dependence
compound. A synthesizer that gets the marginal distribution right and the path
structure wrong will systematically mis-state Systematic Withdrawal Plan
survival, capital sufficiency, and sequence-of-returns risk — and it will do so
while every summary statistic on the report looks correct.

---

## 1. Parameterised CAGR adjustment

Historical returns may overstate forward expected returns. The synthesizer
therefore targets:

    CAGR_target = CAGR_hist − Δ

with defaults Δ = 1.00% for debt and Δ = 1.50% for equity, both configurable,
over a parameterised horizon defaulting to 60 years.

### Required daily drift

Under geometric Brownian motion, the daily log-return drift that delivers
`CAGR_target` over `P` periods per year is:

    μ_daily = ln(1 + CAGR_target) / P  +  σ_ann² / (2P)

**P IS NOT ALWAYS 252.** A series of AMFI NAVs as distributed is *calendar*
daily, roughly 384 observations per year once weekends and holidays are carried
forward. Annualising such a series by 252 understates both CAGR and volatility
by about 30%. `P` must be measured from the data, never assumed.

### What a drift shift does and does not disturb

| Control parameter | Effect of shifting CAGR by Δ |
| --- | --- |
| Daily expected drift `μ_daily` | Shifts by exactly the amount required |
| GARCH-in-mean baseline `μ₀` | Re-centred to `μ_target − λ·E[σ_t]`, preserving the risk-premium slope |
| EVT/GPD tail innovations `z_t` | **Invariant** — `z_t` is standardised, so tail shape is untouched |
| Markov regime drifts | Both states shift uniformly; transition probabilities and dwell times preserved |
| Optimal block length, Hurst exponent | **Invariant** — both depend on the autocovariance of `|z_t|`, which a linear drift shift does not move |

The invariance claims hold **only if `z_t` is genuinely standardised**. If the
residual extraction leaves historical drift inside `z_t`, the tail fit is
contaminated and the invariance is asserted rather than achieved.

---

## 2. Volatility memory and long-range dependence

### 2A. Autocorrelation decay of absolute returns

Daily returns show almost no linear autocorrelation. Absolute and squared
returns show strong, slowly decaying autocorrelation — the signature of
volatility clustering, and it decays as a power law rather than exponentially.

    ACF_|r|(k) = Corr(|r_t|, |r_{t−k}|)  ~  k^(−β)

**Why it matters.** If synthetic volatility clusters dissipate too fast, bear
markets appear as isolated bad days rather than multi-year stress periods. A
withdrawal plan survives the former easily and can be destroyed by the latter.

**Test.** Fit β on the synthetic and historical series out to k = 126 and
require agreement within ±0.05.

### 2B. Hurst exponent

Volatility exhibits long memory, 0.5 < H < 1. Short-memory models under-predict
how long high-volatility regimes last.

**Test.** Rescaled-range analysis on `|r_t|`; require `|H_synth − H_real| ≤ 0.03`.

### 2C. Term structure of variance — *added*

Not in the original document, and it is the criterion that failed hardest when
measured. The variance ratio

    VR(N) = Var(N-period log return) / (N · Var(1-period log return))

is 1.0 for a random walk, below 1 for mean reversion, above 1 for trending.
Measured on `nav_sample.csv`:

| series | VR(3) | VR(6) | VR(12) | VR(18) |
| --- | --- | --- | --- | --- |
| Real equity blend | 1.12 | 1.25 | 1.21 | **0.85** |
| Real debt slot 0 | 0.75 | 0.57 | **0.45** | 0.46 |

Real equity **trends to twelve months and then reverts at eighteen**. Real debt
is an accrual instrument whose annual returns cluster tightly. A block bootstrap
that resamples exchangeable blocks produces a monotone VR curve and cannot
reproduce trend-then-revert at any block length.

This subsumes the rolling-quantile criterion below: decile occupancy at horizon
N is largely a consequence of VR(N).

---

## 3. Asymmetric volatility — the leverage effect

Declines raise future volatility far more than equal-sized rallies do.

    L(k) = Corr(r_t, |r_{t+k}|),  k > 0

In real equity data `L(k) < 0` for k roughly 1 to 20 days. A synthetic series
must preserve that sign. Symmetric models treat −5% and +5% days identically;
real markets do not.

---

## 4. Underwater profile and recovery dynamics

### 4A. Time to recovery

Drawdown depth tells half the story. **Underwater duration** — consecutive days
below the previous all-time high — measures how long capital stays impaired.

    D_t = (P_t − max_{s≤t} P_s) / max_{s≤t} P_s
    TTR_i = t_recovery,i − t_peak,i

**Why it matters.** For a retiree drawing an SWP, a 30% drawdown recovering in
six months is survivable. The same 30% taking seven years is not: units sold at
the bottom never come back.

**Test.** Two-sample Kolmogorov–Smirnov between historical and synthetic TTR
distributions, p > 0.05.

**Caveat on the test's power.** Nineteen years contains few complete
peak-to-recovery episodes. A K-S test on samples that small will fail to reject
almost anything, so p > 0.05 is weak evidence of agreement rather than strong
evidence. Report the sample sizes alongside the p-value or the test flatters
whatever it is given.

### 4B. Consecutive loss runs

Compare the distribution of run lengths of consecutive negative returns.

---

## 5. Generator components

| Component | Purpose | Note |
| --- | --- | --- |
| EVT / GPD tail smoothing | Sample 5σ–6σ events beyond the historical record without inventing drift | Must not be applied i.i.d. per observation — that destroys clustering exactly in the tail |
| Patton–Politis–White optimal block length | Adapt block size from the data's own spectral density at frequency zero | Yields short blocks; short blocks flatten the VR curve toward 1 |
| GJR-GARCH-in-mean | Volatility feeds the drift: post-crash regimes command higher expected recovery | Parameters must be **fitted**, not hardcoded |
| Skewed Student-t innovations | Smooth tail transitions without discretisation gaps | |
| 2-state Markov regime switching | Multi-year bull/crisis dwell times over long horizons | The regime multiplier must not be fed back into the GARCH state — that recursion diverges |
| Shared block draws across funds | Preserve the cross-fund correlation matrix | Absent from a per-asset design; essential for a two-purse model |

---

## 6. Validation matrix

Every row is a test that must be **measured against the real series**, on an
**ensemble** of synthetic paths, and reported with the target's own uncertainty.

| Category | Metric | Threshold |
| --- | --- | --- |
| CAGR shift | `CAGR_synth − CAGR_target` | ≤ 0.01pp |
| Volatility | `σ_synth − σ_hist` | ≤ 0.01pp |
| Term structure | VR(N), N ∈ {3, 6, 12, 18} | within the real value's bootstrap CI |
| Rolling quantiles | decile occupancy of rolling N-month returns in real buckets, drift-adjusted | max deviation ≤ 3pp |
| Volatility clustering | ACF decay exponent β of `|r|` to k = 126 | ±0.05 |
| Long memory | Hurst on `|r|` | ±0.03 |
| Asymmetry | `L(k) = Corr(r_t, |r_{t+k}|)`, k ∈ [1,20] | negative |
| Underwater | TTR distribution, K-S | p > 0.05, with n reported |
| Tail risk | GPD shape ξ | close to ξ_hist |
| Extreme risk | 99% expected shortfall | within 5% relative |
| Regime | HMM dwell times | p₀₀, p₁₁ ± 0.05 |
| Correlation | cross-fund correlation matrix | Frobenius distance within CI |

---

## 7. Two constraints the original document does not state

**The criteria conflict with each other.** Requirement (1) deliberately cuts the
CAGR; requirement (6)'s rolling-quantile row compares synthetic returns against
*real* thresholds. A series with a lower drift must sit systematically low in the
real distribution. Occupancy must therefore be measured on **drift-adjusted**
returns — add the deliberate cut back over the window — or the two requirements
cannot both be satisfied by any series whatsoever.

**Several criteria cannot be met on a single path.** Measured across four seeds
at a fixed block length, the seed-to-seed standard deviation of VR(18) for
equity was **0.54** — larger than the gap between the real value and the value
being called a defect. Any criterion whose sampling noise exceeds its tolerance
must be evaluated **in expectation over an ensemble**, and its tolerance must be
set from the *target's* own confidence interval rather than chosen. Matching a
statistic more tightly than it can be estimated is fitting to noise.
