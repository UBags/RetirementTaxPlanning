# SWP Financial Planner

> **Copyright © 2025 Uddipan Bagchi. All rights reserved.**
> See `LICENSE` in the project root for license information.

A comprehensive retirement-income planning application for Indian investors. It models a 30-year Systematic Withdrawal Plan (SWP) for a **Personal + HUF (Hindu Undivided Family)** dual-entity structure, with tax-optimal fund selection, multi-period portfolio rebalancing via a glide path, Monte Carlo stress testing, and a full PySide6 GUI.

---

## Table of Contents

1. [High-Level Architecture](#high-level-architecture)
2. [Module Overview](#module-overview)
3. [Data Flow Diagram](#data-flow-diagram)
4. [Core Data Models (Class Diagram)](#core-data-models-class-diagram)
5. [Portfolio Optimisation Logic](#portfolio-optimisation-logic)
6. [Simulation Engine Logic](#simulation-engine-logic)
7. [Tax Logic](#tax-logic)
8. [Glide Path & Rebalancing](#glide-path--rebalancing)
9. [Monte Carlo Analysis](#monte-carlo-analysis)
10. [Output File Formats](#output-file-formats)
11. [Input File Format: Fund_Metrics_Output.csv](#input-file-format-fund_metrics_outputcsv)
12. [Project Save File Format (.swp.json)](#project-save-file-format-swpjson)
13. [Configuration File](#configuration-file)
14. [Prerequisites & Installation](#prerequisites--installation)
15. [Steps to Execute the Program](#steps-to-execute-the-program)
16. [CLI: allocate_funds.py](#cli-allocate_fundspy)
17. [CLI: get_funds_data.py](#cli-get_funds_datapy)
18. [GUI Walkthrough](#gui-walkthrough)

---

## High-Level Architecture

```mermaid
graph TD
    subgraph Data Sources
        A[AMFI / mfapi.in<br/>NAV History]
        B[Fund_Metrics_Output.csv<br/>742 funds × 60 metrics]
    end

    subgraph Optimiser Layer
        C[allocate_funds.py<br/>MILP + SLSQP]
        D[glide_path.py<br/>Transition Schedule]
    end

    subgraph Simulation Layer
        E[engine.py<br/>30-yr Monthly SWP]
        F[monte_carlo.py<br/>Block Bootstrap / Log-Normal]
    end

    subgraph Presentation Layer
        G[main.py<br/>PySide6 MainWindow]
        H[chart_dialog.py<br/>Matplotlib Charts]
        I[optimization_report.py<br/>Post-Opt Report]
    end

    subgraph Persistence
        J[*.swp.json<br/>Project Save]
        K[per-user CSV outputs]
    end

    A -->|get_funds_data.py / fetch_amfi_aum.py| B
    B --> C
    C --> D
    D --> E
    E --> G
    E --> F
    F --> H
    G --> J
    G --> K
```

---

## Module Overview

| File | Role |
|---|---|
| `run.py` | Entry point. Prompts for username, creates per-user output directory, launches `MainWindow`. |
| `main.py` | PySide6 `MainWindow`. Hosts 4 scenario tabs, all dialogs, calculation orchestration. |
| `models.py` | All dataclasses: `AppState`, `FundEntry`, `AllocationChunk`, `GlidePath`, `TaxChunk`, `MonthlyRow`, `YearSummary`, etc. Also: `AppState.from_dict()` / `to_dict()` for JSON persistence. |
| `engine.py` | 30-year monthly SWP simulation (per-fund FIFO lot tracking, rebalancing, tax, harvesting). Also `optimize_withdrawal_split()`. |
| `allocate_funds.py` | Portfolio optimiser: loads `Fund_Metrics_Output.csv`, runs MILP (PuLP) + SLSQP (SciPy), multi-pass aim/track/frontier-walk strategies, sticky-portfolio Mode A & B. |
| `glide_path.py` | Builds the year-by-year weight schedule (`GlidePath`) from chunk `target_weights`, with linear interpolation across transition windows. |
| `monte_carlo.py` | Sequence-of-returns stress test: Historical Block Bootstrap (preferred) or Log-Normal. Produces `MCResults` with fan-chart percentile bands and ruin probabilities. |
| `configuration.py` | Singleton config reader for `RetirementTaxPlanning.configuration`. |
| `get_funds_data.py` | Downloads NAV history from mfapi.in / AMFI; computes rolling CAGR, Sharpe, Sortino, Calmar, Max-DD, Alpha, Beta, Treynor; writes `Fund_Metrics_Output.csv`. |
| `fetch_amfi_aum.py` | Fetches AMFI AUM data and adds `Fund Size` column to metrics CSV. |
| `get_amfi_fund_schemes_names.py` | Utility: downloads AMFI master scheme list. |
| `reclassify_legacy_funds.py` | Utility: reclassifies fund types in saved project JSON. |
| `chunk_editor.py` | Reusable PySide6 widget for editing year-range chunk tables. |
| `dialogs.py` | Various input dialogs (funds, requirements, windfalls, income, splits, returns, sensitivity). |
| `fund_dialog.py` | "View Fund Selection & Allocation" dialog (portfolio stats, fund-level CAGR table). |
| `chart_dialog.py` | Non-modal chart windows: Corpus/Cash bar charts, Sensitivity Analysis, Monte Carlo fan charts. |
| `optimization_report.py` | Post-optimisation multi-tab report: Glide Path Summary, Fund Selection, Tax Attribution, Robustness. |
| `tax_dialog.py` | Tax rules editor (slab tables for debt/equity, Personal + HUF). |

---

## Data Flow Diagram

```mermaid
flowchart LR
    subgraph Step1 [1 · Fund Data Acquisition]
        mfapi["mfapi.in API"] --> gfd["get_funds_data.py\nComputes 60 metrics\nper fund"]
        amfi["AMFI AUM API"] --> aum["fetch_amfi_aum.py"]
        gfd --> fmo["Fund_Metrics_Output.csv"]
        aum --> fmo
    end

    subgraph Step2 [2 · Portfolio Optimisation]
        fmo --> lf["load_and_filter()\nadj_ret / adj_std / adj_dd"]
        lf --> milp["MILP Aim Pass\n(PuLP / SLSQP)\nMultiple α-blends\nor Frontier Walk"]
        milp --> cands["Candidate Portfolios\nper Chunk"]
        cands --> score["Cross-chunk\nCombination Scoring"]
        score --> best["select_best_combination()\nChunk.target_weights"]
        best --> gp["build_glide_path()\nGlidePath.schedule\n{year → weights}"]
    end

    subgraph Step3 [3 · Simulation]
        gp --> eng["Engine.run()\n30 × 12 = 360 monthly steps\nFIFO lots · drift control\nLTCG harvest · rebalance"]
        state["AppState\n(requirements, tax,\nwindfalls, income)"] --> eng
        eng --> pmr["List[MonthlyRow]\nPersonal"]
        eng --> hmr["List[MonthlyRow]\nHUF"]
        eng --> ys["List[YearSummary]\n30 rows"]
    end

    subgraph Step4 [4 · Post-Processing]
        ys --> opt_split["optimize_withdrawal_split()\nPersonal ↔ HUF tax\nminimisation"]
        opt_split --> eng
        ys --> mc["monte_carlo.run_monte_carlo()\nMCResults"]
        ys --> sens["run_sensitivity()\nScenario comparison"]
    end

    subgraph Step5 [5 · Output]
        pmr --> csv_pm["*_Personal_Monthly.csv\n*_Personal_Monthly_FundDetail.csv"]
        hmr --> csv_hm["*_HUF_Monthly.csv\n*_HUF_Monthly_FundDetail.csv"]
        ys  --> csv_ya["*_Personal_Annual_Summary.csv\n*_HUF_Annual_Summary.csv"]
        mc  --> charts["Monte Carlo Charts\nFan / Ruin / Paths"]
        sens --> charts2["Sensitivity Chart"]
    end
```

---

## Core Data Models (Class Diagram)

```mermaid
classDiagram
    class AppState {
        +date investment_date
        +List~FundEntry~ funds
        +List~AllocationChunk~ allocation_chunks
        +List~TaxChunk~ individual_debt_chunks
        +List~EquityTaxChunk~ individual_equity_chunks
        +List~TaxChunk~ huf_debt_chunks
        +List~EquityTaxChunk~ huf_equity_chunks
        +List~ReturnChunk~ return_chunks
        +Dict~int,float~ annual_requirements
        +OtherIncome personal_income
        +OtherIncome huf_income
        +List~WindfallEntry~ windfalls
        +List~HUFWithdrawalChunk~ huf_withdrawal_chunks
        +str allocation_mode
        +int rebalance_spread_years
        +GlidePath glide_path
        +to_dict() dict
        +from_dict(d) AppState
        +get_allocation_chunk(year) AllocationChunk
        +get_split_3way(year) tuple
        +evaluate_chunk_scores() list
    }

    class FundEntry {
        +str name
        +str fund_type
        +float allocation
        +float std_dev
        +float sharpe
        +float sortino
        +float calmar
        +float combined_ratio
        +float max_dd
        +float cagr_1 / 3 / 5 / 7 / 10
        +float avg_cagr_3 / 5 / 7 / 10
        +float worst_exp_ret
        +str amfi_fund_type
    }

    class AllocationChunk {
        +int year_from
        +int year_to
        +List~FundEntry~ funds
        +Dict~str,float~ target_weights
        +float min_return
        +float max_std_dev
        +float max_dd
        +float max_per_fund
        +float min_per_fund
        +float min_equity_frac
        +portfolio_std() float
    }

    class GlidePath {
        +Dict~int,dict~ schedule
        +transition_years() List~int~
        +is_flat() bool
    }

    class TaxChunk {
        +int year_from
        +int year_to
        +List~TaxSlab~ slabs
    }

    class TaxSlab {
        +float lower
        +float upper
        +float rate
    }

    class EquityTaxChunk {
        +int year_from
        +int year_to
        +float tax_rate
        +float exempt_limit
    }

    class MonthlyRow {
        +int month_idx
        +int calendar_month
        +int fy_year
        +float corpus_debt_start / end
        +float corpus_equity_start / end
        +float corpus_other_start / end
        +float wd_debt / equity / other
        +float principal_debt / equity / other
        +float gain_debt / equity / other
        +float ind_tax_paid
        +float huf_transfer_in
        +float windfall_personal
        +List~FundWithdrawalDetail~ fund_withdrawals
    }

    class YearSummary {
        +int year
        +float corpus_debt_personal
        +float corpus_equity_personal
        +float corpus_debt_huf
        +float corpus_equity_huf
        +float tax_personal
        +float tax_huf
        +float net_cash_personal
        +float net_cash_huf
        +float net_cash_total
        +float fd_tax_benchmark
        +float tax_saved
        +float rebalance_tax_paid
        +float swp_cash_personal
    }

    class OtherIncome {
        +float salary
        +float taxable_interest
        +float pension
        +float rental
        +float other_taxable
        +float tax_free_interest
        +float other_non_taxable
    }

    class WindfallEntry {
        +int year
        +float amount_personal
        +float amount_huf
    }

    AppState "1" *-- "many" FundEntry
    AppState "1" *-- "many" AllocationChunk
    AppState "1" *-- "1" GlidePath
    AppState "1" *-- "many" TaxChunk
    AppState "1" *-- "many" EquityTaxChunk
    AppState "1" *-- "2" OtherIncome
    AppState "1" *-- "many" WindfallEntry
    AllocationChunk "1" *-- "many" FundEntry
    TaxChunk "1" *-- "many" TaxSlab
```

---

## Portfolio Optimisation Logic

The optimiser lives in `allocate_funds.py`. Two top-level modes are supported:

### Mode A — Singular (Buy-and-Hold)

```mermaid
flowchart TD
    A[AllocationChunks from AppState] --> B[merge_chunks_strict\nStrictest constraints across all chunks]
    B --> C[run_aim_pass_multi\nα-blend MILP: α from 1.0 to 0.775]
    C --> D[Best solution\ntarget_weights]
    D --> E[Apply same weights to every chunk]
    E --> F[build_flat_glide_path\nFlat GlidePath]
```

### Mode B — Chunked Sticky (Default)

```mermaid
flowchart TD
    A[AllocationChunks] --> B[build_expanded_universe\nSeed + up to 50 similar funds]
    B --> C{Candidate generation strategy}
    C -- α-blending --> D[run_aim_pass_multi\nn candidate portfolios per chunk]
    C -- Frontier Walk --> E[run_frontier_walk\nIterative risk-floor increment]
    C -- PuLP Commonality Walk --> F[PuLP MILP\nMaximise cross-chunk overlap]
    D --> G[score_combinations\nEvaluates all cross-chunk combos\nfor turnover efficiency]
    E --> G
    F --> G
    G --> H[select_best_combination\nchunk.target_weights set]
    H --> I[run_track_pass\nSLSQP: minimise turnover from anchor\nsoft constraints: return floor,\nstd/dd ceilings]
    I --> J[build_glide_path\nLinear interpolation\nacross rebalance_spread_years]
```

### Optimiser Constraint Hierarchy

The solver uses a **six-step relaxation ladder** — it only relaxes constraints if infeasible at the previous step:

| Step | Constraint relaxed |
|---|---|
| 0 | All constraints tight |
| 1 | Remove per-type cap |
| 2 | Relax `max_dd` |
| 3 | Relax `max_std_dev` |
| 4 | Lower `min_return` |
| 5 | Raise `max_per_fund` |

`min_per_fund` is enforced via **iterative pruning** at every step and is never relaxed.

### Fund Scoring Metrics

| Derived column | Definition |
|---|---|
| `adj_ret` | `min(Avg_CAGR_3Y, Avg_CAGR_5Y, Avg_CAGR_7Y, Avg_CAGR_10Y)` — worst-case rolling average return |
| `adj_std` | `max(Max_Std_Dev_3Y … 10Y)` — worst-case rolling volatility |
| `adj_dd` | `min(Worst_Max_DD_3Y … 10Y)` — worst-case drawdown (most negative) |
| `adj_quality` | `Combined_Ratio` (prefer 5Y → 10Y → 3Y) = `√(Sortino × Calmar)` |

---

## Simulation Engine Logic

`engine.py → Engine.run()` loops over 360 months (30 FYs × 12 months). Each entity (Personal, HUF) maintains **per-fund FIFO lot buckets**.

```mermaid
flowchart TD
    Start([Month loop: m = 1..360]) --> A[Grow each fund lot by fund-specific CAGR]
    A --> B{SWP month?\nm >= SWP_START}
    B -- Yes --> C[Bounded Smart Withdrawal\nWaterfall: sell overweight funds first\nDrift cap Personal 0.15%, HUF 0.50%]
    B -- No --> D[No withdrawal]
    C --> E[Compute FIFO gains: STCG / LTCG\nper lot held < 12 months]
    D --> E
    E --> F{March = end of FY?}
    F -- Yes --> G[LTCG Harvest\nRealise gains up to equity exempt limit\nStep-up cost basis → avoid future tax]
    F -- No --> H
    G --> H{Chunk boundary year?}
    H -- Yes --> I[_rebalance_portfolio\nSell overweights → buy underweights\nSelf-funded: tax deducted from proceeds]
    H -- No --> J
    I --> J[HUF top-up\nIf Personal corpus exhausted\nHUF transfers deficit to Personal]
    J --> K[Accumulate to p_acc / h_acc\nper FY totals]
    K --> L{m == 360?}
    L -- No --> Start
    L -- Yes --> M[_build_yearly_summaries\n30 YearSummary rows]
```

### Withdrawal Waterfall

At each SWP month, the engine withdraws from funds in this priority order:

1. Funds most **overweight** vs. target (drift correction)
2. Within the same drift tier: funds with the **highest individual expected return** (sell the richest first)
3. Respects category allocation (debt/equity/other ratios)

---

## Tax Logic

The engine computes tax annually in April (India FY end). Tax is calculated separately for **Personal** and **HUF** entities.

```mermaid
flowchart LR
    A[Annual Gains\ndebt / equity / other] --> B{Entity type}
    B -- Individual --> C[compute_slab_tax\nProgressive slabs from TaxChunk\n+ 4% cess]
    B -- Individual --> D[compute_ltcg_individual\nEquity LTCG rate from EquityTaxChunk\nExempt limit deducted first]
    B -- HUF --> E[compute_slab_tax\nHUF slab rates\n+ 4% cess]
    B -- HUF --> F[compute_ltcg_huf\nHUF equity LTCG\nstacked on debt income]
    C --> G[Total Personal Tax]
    D --> G
    E --> H[Total HUF Tax]
    F --> H
    G --> I[Rebalancing tax excluded\nfrom net_cash\nPortfolio self-funds it]
    H --> I
    I --> J[Tax Saved = Personal tax avoided\nby shifting income to HUF]
```

**Tax-Optimal HUF–Personal Split Optimizer** (`optimize_withdrawal_split`): iteratively shifts withdrawal ₹0.25L at a time from Personal to HUF to minimise combined household tax, subject to:
- `min_personal_frac` (≥ 25% personal SWP retained)
- `max_huf_corpus_drain` (≤ 3% HUF corpus per FY)
- `user_cap` (≤ ₹7.5L extra HUF withdrawal per FY)

---

## Glide Path & Rebalancing

```mermaid
flowchart LR
    chunks["AllocationChunks\n[Yr 1-10], [Yr 11-20], [Yr 21-30]"]
    spread["rebalance_spread_years = 4"]
    chunks & spread --> build["build_glide_path()"]
    build --> schedule["GlidePath.schedule\n{1: w1, 2: w1, ...,\n 8: lerp(w1,w2,0.25),\n 9: lerp(w1,w2,0.50),\n10: lerp(w1,w2,0.75),\n11: w2, ...}"]
    schedule --> engine["Engine: each year\n_rebalance_portfolio()\nSells overweights → buys underweights\nCapital gains tax self-funded"]
```

The transition window of `spread_years` is **centred on the chunk boundary**. Because monthly SWP naturally sells overweighted funds first, the glide path rebalancing is mostly accomplished by the normal withdrawal flow — only residual imbalances require explicit sell/buy operations.

---

## Monte Carlo Analysis

```mermaid
flowchart TD
    A[AppState + GlidePath] --> B[run_monte_carlo\nn_sims simulations]
    B --> C{use_bootstrap?}
    C -- Yes --> D[Fetch Nifty 50 NAV\nmfapi.in / AMFI portal\nSaved to mc_nifty50_nav.csv]
    D --> E[Extract Nifty Composite Debt\nFrom _NIFTY_DEBT_DATA table]
    E --> F[Block Bootstrap\nblock_length consecutive years\nPreserves volatility clustering\nand fat tails]
    C -- No / Fallback --> G[Log-Normal\nμ = return_chunks\nσ = Σ w_i·σ_i perfect-correlation]
    F --> H[Centre returns per FY:\nr_centred = r_hist - mean + μ_det_fy\nFloor = μ_det - 3σ]
    G --> H
    H --> I[360 monthly sim steps\nper path]
    I --> J[MCResults\ncorpus_p5/p25/p50/p75/p95\ncash_p5…p95\nruin_by_fy\nmedian / p5 final corpus\nruin_probability]
    J --> K[MonteCarloChart\n5 views:\n1 Corpus Fan\n2 Cash Fan\n3 Ruin Probability\n4 Sequence-of-Returns Paths\n5 Summary Statistics]
```

**Blended portfolio return** for each simulated year:

```
r_portfolio = (w_equity + w_other) × r_equity_centred
            +  w_debt              × r_debt_centred
```

"Other" funds (Gold, Hybrid, International) are treated as equity for volatility — a conservative assumption.

---

## Output File Formats

All outputs are written to `<project_root>/<user_name>/`.

### `*_Personal_Monthly.csv` / `*_HUF_Monthly.csv`
349 rows (months 1–349 of the 30-year plan), 21–23 columns.

| Column | Description |
|---|---|
| `Month#` | Sequential month number (1–349) |
| `Cal Month` | Calendar month label (e.g., `Apr-2025`) |
| `FY Year` | Financial year index (1–30) |
| `Corpus Debt/Eq/Other Start` | Corpus at start of month (₹ lakhs) |
| `WD Debt/Equity/Other/Total` | Gross withdrawal this month |
| `Principal Debt/Equity/Other` | Principal component of withdrawal |
| `Gain Debt/Equity/Other` | Gain component of withdrawal (taxable) |
| `Ind Tax Paid (Apr)` | Tax paid in April (FY-end settlement) |
| `HUF Transfer (Apr)` | Amount transferred from HUF in April |
| `FD Tax Benchmark` | Equivalent tax if invested in FD |
| `Corpus Debt/Eq/Other End` | Corpus at end of month |
| `Windfall Personal` | Windfall received this month |

### `*_Personal_Monthly_FundDetail.csv` / `*_HUF_Monthly_FundDetail.csv`
3822 / 1626 rows (months × funds). Per-fund breakdown.

| Column | Description |
|---|---|
| `Month#`, `Cal Month`, `FY Year` | Time identifiers |
| `Fund Name` | Fund name |
| `Fund Type` | `debt` / `equity` / `other` |
| `Corpus Start` | Fund corpus at start of month |
| `Withdrawal` | Amount withdrawn from this fund |
| `Principal` | Principal component of withdrawal |
| `Gain` | Gain component |
| `Corpus End` | Fund corpus at end of month |

### `*_Personal_Annual_Summary.csv` / `*_HUF_Annual_Summary.csv`
30 rows (one per financial year), 15 columns.

| Column | Description |
|---|---|
| `FY Year` | Financial year index (1–30) |
| `Corpus Debt/Equity/Other (Personal)` | Year-end corpus by category, Personal |
| `Corpus Debt/Equity/Other (HUF)` | Year-end corpus by category, HUF |
| `Tax Personal (L)` | Total personal tax for the year (₹ lakhs) |
| `Tax HUF (L)` | Total HUF tax for the year |
| `Net Cash Personal (L)` | Personal spendable cash (SWP - tax + topup) |
| `SWP Cash Personal (L)` | Raw SWP withdrawal (before tax) |
| `Net Cash HUF (L)` | HUF net cash |
| `Net Cash Total (L)` | Combined household cash |
| `FD Tax Benchmark (L)` | Benchmark: tax if all money were in FD |
| `Tax Saved to HUF (L)` | Tax saved vs. benchmark due to HUF structure |

### `Fund_Metrics_Output.csv`
742 rows (one per fund), 60 columns. Key columns:

| Column | Description |
|---|---|
| `Fund Type` | AMFI regulatory sub-category |
| `Fund Name` | Fund full name |
| `Fund Size` | AUM (₹ crores) |
| `AMFI Code` | Scheme code |
| `Worst_Exp_Ret_%` | Min historical rolling CAGR (conservative return) |
| `Std_Dev_XY` | Annualised std dev over X-year period |
| `Sharpe_XY` | Sharpe ratio |
| `Sortino_XY` | Sortino ratio |
| `Max_DD_XY` | Maximum drawdown |
| `Calmar_XY` | Calmar ratio |
| `Avg_CAGR_XY` | Mean of rolling XY-year CAGRs |
| `Min_CAGR_XY` | Minimum rolling XY-year CAGR |
| `Max_Std_Dev_XY` | Maximum std dev in any rolling XY window |
| `Worst_Max_DD_XY` | Worst drawdown in any rolling XY window |
| `Combined_Ratio_XY` | `√(Sortino × Calmar)` |
| `Alpha_10Y`, `Beta_10Y`, `Treynor_10Y` | Factor metrics vs Nifty 50 |
| `History_Months` | Number of months of NAV history |
| `1Y_CAGR` … `10Y_CAGR` | Point-in-time CAGRs |

---

## Input File Format: Fund_Metrics_Output.csv

This file is the **primary input** to the optimiser. It is generated by `get_funds_data.py` (see [CLI: get_funds_data.py](#cli-get_funds_datapy)).

Place it in the project root directory (or as configured by `allocator_default_input` in the configuration file).

---

## Project Save File Format (.swp.json)

The application saves and loads the entire `AppState` as a JSON file (`*.swp.json`). Top-level keys:

```json
{
  "investment_date": "2025-04-01",
  "funds": [ { "name": "...", "fund_type": "debt", "allocation": 35.0, ... } ],
  "allocation_chunks": [
    {
      "year_from": 1, "year_to": 10,
      "funds": [...],
      "target_weights": { "Fund Name": 0.07, ... },
      "min_return": 0.0685,
      "max_std_dev": 0.0099,
      "max_dd": 0.0075,
      "max_per_fund": 0.08,
      "min_per_fund": 0.02,
      "min_equity_frac": 0.0
    }
  ],
  "individual_debt_chunks": [ { "year_from": 1, "year_to": 30, "slabs": [...] } ],
  "individual_equity_chunks": [ { "year_from": 1, "year_to": 30, "tax_rate": 0.125, "exempt_limit": 1.25 } ],
  "huf_debt_chunks": [...],
  "huf_equity_chunks": [...],
  "return_chunks": [ { "year_from": 1, "year_to": 30, "annual_return": 0.075 } ],
  "annual_requirements": { "1": 12.0, "2": 12.5, ... },
  "personal_income": { "salary": 0, "taxable_interest": 0, ... },
  "huf_income": { ... },
  "windfalls": [ { "year": 5, "amount_personal": 20.0, "amount_huf": 0.0 } ],
  "huf_withdrawal_chunks": [ { "year_from": 1, "year_to": 30, "annual_withdrawal": 6.0 } ],
  "allocation_mode": "chunked_sticky",
  "rebalance_spread_years": 4
}
```

---

## Configuration File

`RetirementTaxPlanning.configuration` — plain `key = value` properties file in the project root.

Key parameters (auto-cast to int/float/bool):

| Key | Example | Description |
|---|---|---|
| `cess_rate` | `0.04` | Health & Education Cess (4%) |
| `equity_ltcg_rate` | `0.125` | Equity LTCG flat rate |
| `other_ltcg_rate` | `0.125` | Other (Debt post-2023) LTCG flat rate |
| `stcg_holding_months` | `12` | Holding period threshold for STCG vs LTCG |
| `exit_load_rate` | `0.01` | Exit load for STCG redemptions |
| `drift_cap_personal` | `0.0015` | Max weight drift before forced rebalance (Personal) |
| `drift_cap_huf` | `0.005` | Max weight drift before forced rebalance (HUF) |
| `max_simulation_years` | `30` | Number of FYs to simulate |
| `allocator_default_input` | `Fund_Metrics_Output.csv` | Default fund metrics file |
| `allocator_default_output` | `allocation_output.csv` | Default allocator output |

---

## Prerequisites & Installation

**Python:** 3.10 or later

```bash
pip install PySide6 matplotlib pandas numpy scipy pulp requests
```

Optional (for block-bootstrap Monte Carlo):
- `requests` (already listed) — used to fetch Nifty 50 NAV from mfapi.in

**Verify dependencies:**
```bash
python -c "import PySide6, matplotlib, pandas, numpy, scipy, pulp; print('OK')"
```

---

## Steps to Execute the Program

### 1. Generate Fund Metrics (one-time / periodic refresh)

```bash
python get_funds_data.py
```

This fetches NAV history from mfapi.in for all AMFI-listed funds, computes all 60 metrics, and writes `Fund_Metrics_Output.csv` to the project root. This can take **20–60 minutes** depending on network speed.

Optionally add AUM data:
```bash
python fetch_amfi_aum.py
```

### 2. Launch the GUI

```bash
python run.py
```

At the welcome dialog, enter your name (e.g., `Uddi`). All outputs will be written to `<project_root>/Uddi/`.

### 3. Typical GUI Workflow

```mermaid
flowchart TD
    S1[Open app: python run.py] --> S2[Enter user name]
    S2 --> S3[File → New or File → Open *.swp.json]
    S3 --> S4[Configure Funds\nDialog: Edit Funds]
    S4 --> S5[Configure Tax Rules\nDialog: Edit Tax Rules]
    S5 --> S6[Set SWP Requirements\nDialog: Annual Requirements]
    S6 --> S7[Optional: Set Income,\nWindfalls, HUF Withdrawals]
    S7 --> S8[Run Portfolio Optimiser\nButton: Optimise Portfolio]
    S8 --> S9[Review Optimisation Report\nGlide Path · Fund Selection · Tax · Robustness]
    S9 --> S10[Run Calculations\nButton: Calculate]
    S10 --> S11[View results in tables:\nPersonal Monthly\nHUF Monthly\nAnnual Summary]
    S11 --> S12[Optional: Monte Carlo\nButton: Monte Carlo]
    S12 --> S13[Optional: Sensitivity Analysis\nButton: Sensitivity]
    S13 --> S14[Export CSVs\nFile → Export]
    S14 --> S15[Save project\nFile → Save]
```

### 4. Command-Line Allocator (single chunk)

```bash
python allocate_funds.py \
  --input Fund_Metrics_Output.csv \
  --total 500 \
  --min-return 6.85 \
  --max-std-dev 0.99 \
  --max-dd 0.75 \
  --min-history 5 \
  --max-per-fund 7.0 \
  --max-per-type 20.0 \
  --min-per-fund 1.0 \
  --max-fund-std 1.5 \
  --max-fund-dd 1.5 \
  --max-per-amc 16.0 \
  --output allocation_output.csv
```

Or interactively (no arguments):
```bash
python allocate_funds.py
```

### 5. Command-Line Allocator (multi-chunk / sticky portfolio)

```bash
python allocate_funds.py \
  --input Fund_Metrics_Output.csv \
  --chunks-file chunks_config.json \
  --output-dir ./my_allocation/ \
  --mode coarse \
  --frontier-walk
```

Where `chunks_config.json` is an array of chunk objects:
```json
[
  {
    "year_from": 1, "year_to": 10,
    "total": 500,
    "min_return": 6.85, "max_std_dev": 0.99, "max_dd": 0.75,
    "min_history": 5, "max_per_fund": 7.0, "max_per_type": 20.0,
    "min_per_fund": 1.0, "max_fund_std": 1.5, "max_fund_dd": 1.5,
    "max_per_amc": 16.0
  },
  {
    "year_from": 11, "year_to": 20,
    "total": 500,
    "min_return": 6.50, "max_std_dev": 1.20, "max_dd": 1.00,
    ...
  }
]
```

All numeric constraint values in the JSON are in **percentage points** (e.g., `6.85` for 6.85%), not fractions.

---

## CLI: allocate_funds.py

```
usage: allocate_funds.py [-h] [--input INPUT] [--total TOTAL]
                         [--chunks-file CHUNKS_FILE] [--output-dir OUTPUT_DIR]
                         [--commonality-bonus COMMONALITY_BONUS]
                         [--mode {coarse,fine}]
                         [--frontier-walk] [--pulp-commonality]
                         [--pulp-max-portfolios N] [--pulp-max-std PCT]
                         [--risk-ref {portfolio,pct75}]
                         [--output OUTPUT]
                         [--min-return PCT] [--max-std-dev PCT] [--max-dd PCT]
                         [--min-history YRS] [--max-per-fund PCT]
                         [--max-per-type PCT] [--min-per-fund PCT]
                         [--max-fund-std PCT] [--max-fund-dd PCT]
                         [--max-per-amc PCT]
```

**Multi-chunk outputs** (in `--output-dir`):
- `chunk_01.csv`, `chunk_02.csv`, … — per-chunk allocation tables
- `summary.csv` — cross-chunk fund commonality summary
- `portfolio_viz.html` — interactive HTML visualisation of all chunks

**Single-chunk output** (`--output`):
- `allocation_output.csv` — fund allocations with portfolio summary row

---

## CLI: get_funds_data.py

```bash
python get_funds_data.py [--output OUTPUT_CSV] [--workers N]
```

Downloads NAV history from `https://api.mfapi.in/mf/{scheme_code}` for all active AMFI schemes, computes rolling metrics, and writes `Fund_Metrics_Output.csv`.

Key metrics computed per fund per time window (3Y, 5Y, 7Y, 10Y):

- **CAGR**: Point-in-time and rolling average/minimum
- **Std Dev**: Annualised from monthly returns (rolling max)
- **Sharpe**: `(CAGR - rf) / std_dev` where `rf` = 6% (repo rate proxy)
- **Sortino**: `(CAGR - rf) / downside_std`
- **Max Drawdown**: Peak-to-trough from NAV series (rolling worst)
- **Calmar**: `CAGR / |Max_DD|`
- **Combined Ratio**: `√(Sortino × Calmar)` — quality composite
- **Alpha, Beta, Treynor**: vs. Nifty 50 Index benchmark

---

## GUI Walkthrough

The main window (`main.py → MainWindow`) has **4 scenario tabs** — each scenario is an independent `AppState`. Shared fields (tax rules, requirements, income) are synchronised across scenarios.

| Dialog / Section | Access | Purpose |
|---|---|---|
| **Edit Funds** | Toolbar button | Add/remove funds, set allocations, import from CSV |
| **Tax Rules** | Toolbar button | Edit slab tables for debt/equity, Personal + HUF |
| **Annual Requirements** | Toolbar button | Set SWP withdrawal targets per FY |
| **Other Income** | Toolbar button | Salary, pension, rental, interest income |
| **Windfalls** | Toolbar button | One-off large inflows in specific years |
| **HUF Withdrawals** | Toolbar button | HUF scheduled withdrawal chunks |
| **Return Assumptions** | Toolbar button | Per-period annual return overrides |
| **Optimise Portfolio** | Toolbar button | Launch MILP+SLSQP optimiser; opens Optimisation Report |
| **Calculate** | Toolbar button | Run engine; populates tables |
| **Monte Carlo** | Toolbar button | Sequence-of-returns stress test; opens fan charts |
| **Sensitivity** | Toolbar button | Multi-scenario return comparison; opens comparison chart |
| **Optimisation Report** | Auto-opens post-optimise | Glide path, fund selection, tax attribution, robustness ranking |
| **Personal Monthly Table** | Main tab | 349-row monthly detail, Personal corpus |
| **HUF Monthly Table** | Main tab | 349-row monthly detail, HUF corpus |
| **Annual Summary Table** | Main tab | 30-row FY summary, both entities |

---

*© 2025 Uddipan Bagchi. All rights reserved.*