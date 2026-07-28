# archive/

Code that has been superseded and removed from the live tree, kept so the
reasoning is not lost with it.

## Why this exists rather than `git log`

The history has the code. What it does not have, findable, is *why* a
particular approach was replaced — that lives in a commit message six months
back that nobody will search for, because you have to already know the method
existed to go looking. Each file here carries its own provenance header: what
replaced it, and what was wrong with it.

The concrete case this convention was created for: `suggest_risk_free_rate()`
computed the risk-free rate one way, `percentile_metrics` then computed it
another, and for a while both existed. Two functions claiming to produce the
same number by different methods is exactly the thing that gets picked up by
mistake later. Deleting the loser hides the fact that the question was ever
open; archiving it records the answer.

## Rules

- **Nothing here is imported.** No `__init__.py`, and the modules are not
  wired into anything. They are read, not run, and several would not run.
- **Every file states what replaced it** and why, in a docstring at the top.
- **Excluded from `ruff`** (see `ruff.toml`): holding dead code to live
  standards is the opposite of the point.
- **Still valid Python**, so `python -m compileall $(git ls-files '*.py')`
  passes. Anything that will not parse should be a `.txt`.
- **Tracked in git.** Archiving is not deleting.

## Contents

| file | replaced by | why |
|---|---|---|
| `chunk_defaults_suggest_risk_free_rate.py` | `percentile_metrics.risk_free_from_liquid_funds()` | wrong metric (`min(Avg_CAGR_*)` rather than P33 of the cross-fund monthly average), wrong classifier (fund name rather than AMFI category), and swept Overnight and Money Market funds in with Liquid |
| `rebalance_portfolio.py` | nothing yet -- HANDOVER 4.3's capacity-driven transition scheduler will occupy the socket | dead since `bfbf226` removed its caller, and the only two places in the engine that used HIFO, which is not achievable: gains are FIFO per folio (CBDT Circular 704) and an AMC cannot be told to sell your highest-cost units |
