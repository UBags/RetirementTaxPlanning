# data/

Untracked. Inputs, caches and logs live here; only this README is in git.
Every default path resolves through `src/swp_planner/paths.py`.

| Folder | Contents | Produced by |
|---|---|---|
| `funds/` | `Fund Details.csv`, `Fund_Metrics_Output.csv`, `Fund_Metrics_Errors.csv`, `amfi_map.csv`, `rolling_data.json`, `Schemes_and_Funds/` | `scripts/make_fund_details.py`, `swp_planner.funddata.*` CLIs |
| `nav/` | `nav_corpus.json`, `.nav_corpus_cache.json`, `nav_sample.csv` | `swp_planner.funddata.build_nav_corpus`, `extract_nav_sample` |
| `cache/` | `mc_nifty50_nav.csv`, `mc_debt_index.csv` (7-day Monte Carlo history caches) | `swp_planner.simulation.monte_carlo` |
| `inputs/` | Hand-maintained files no module writes (e.g. `30_Yrs_Forecast_Data.xlsx`, `redemptions.csv`) | you |
| `logs/` | `fund_analyser.log`, `swp_debug.log`, `refresh.sh` step logs | CLIs, `scripts/refresh.sh` |
| `etmoney/`, `nsdl_active_*.xlsx`, `manifest.jsonl` | ingest pipeline captures | `scripts/etmoney_*`, `scripts/nsdl_*` |

The GUI writes per-user results to `outputs/<user>/`, not here.
