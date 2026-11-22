#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
"""
capture_mc_history.py — freeze the Monte Carlo historical inputs (Phase 1.2).

WHY
---
`monte_carlo.run_monte_carlo(use_bootstrap=True)` is the default path and the
one the owner actually uses, and it has never been under test. It cannot be,
while `_fetch_nifty50_annual_returns()` reaches api.mfapi.in: the returned
series grows by a year every April, differs between this machine and CI, and
degrades silently to log-normal when the network is down (every fetcher ends
in `except Exception: return None`).

This script captures both historical series ONCE into a committed fixture, so
the bootstrap path becomes reproducible and snapshottable. It is the MC
equivalent of `pytest tests/golden --regen-golden`: run deliberately, review
the diff, commit.

    python scripts/capture_mc_history.py

writes  tests/golden/fixtures/mc_history.json

WHAT IS CAPTURED
----------------
    equity : Nifty 50 April-March annual returns, from the mfapi/AMFI fetch
             (or the local mc_nifty50_nav.csv cache if it is fresh).
    debt   : Nifty Composite Debt Index April-March annual returns, from the
             _NIFTY_DEBT_DATA table embedded in get_funds_data.py. This one
             needs no network and is already deterministic; it is captured
             anyway so the fixture is self-contained and one file pins the
             whole bootstrap input.

Provenance — capture date, series lengths, first and last values — is written
into the file, because a frozen fixture with no record of where it came from
is worse than no fixture: it looks authoritative and cannot be audited.

WHEN TO RE-RUN
--------------
Only deliberately: after a new financial year closes and you want the extra
year of history in the simulation, or if the NAV split/consolidation fixes in
`_apply_nav_fixes()` change. Re-running rewrites the fixture, which will move
the MC snapshot. Both diffs belong in one reviewed commit, and the commit
message should say which financial year was added.

NETWORK
-------
This script DOES use the network, deliberately — it is the one place that
should. It is not a test and pytest's Phase 1.2 network block does not apply
to it. If the equity fetch fails it says so and writes nothing, rather than
producing a fixture with a silently truncated series.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "golden" / "fixtures" / "mc_history.json"


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    import numpy as np

    from swp_planner.simulation import monte_carlo as mc

    print("Fetching Nifty 50 annual returns (network) ...")
    eq = mc._fetch_nifty50_annual_returns()
    if eq is None or len(eq) < mc._MIN_EQUITY_FY:
        print("FAILED: equity history unavailable or too short. Nothing written.\n"
              "        Check connectivity to api.mfapi.in / portal.amfiindia.com.\n"
              "        A partial fixture is worse than none — refusing to write.",
              file=sys.stderr)
        return 1

    print("Building debt index annual returns (no network) ...")
    dbt = mc._fetch_debt_index_annual_returns()
    if dbt is None or len(dbt) == 0:
        print("FAILED: debt history unavailable. get_funds_data.py must be "
              "importable from the project root. Nothing written.", file=sys.stderr)
        return 1

    eq = np.asarray(eq, dtype=float)
    dbt = np.asarray(dbt, dtype=float)

    payload = {
        "_provenance": {
            "captured_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "captured_by": "scripts/capture_mc_history.py",
            "equity_source": "monte_carlo._fetch_nifty50_annual_returns() "
                             "(mc_nifty50_nav.csv cache, else mfapi.in, else AMFI portal)",
            "debt_source": "monte_carlo._fetch_debt_index_annual_returns() "
                           "(_NIFTY_DEBT_DATA embedded in get_funds_data.py)",
            "equity_n_fy": int(len(eq)),
            "debt_n_fy": int(len(dbt)),
            "equity_mean": round(float(eq.mean()), 6),
            "debt_mean": round(float(dbt.mean()), 6),
            "note": "April-March financial-year returns, oldest first. "
                    "Frozen deliberately: re-running this script moves the MC "
                    "snapshot, so both diffs belong in one reviewed commit.",
        },
        "equity_annual_returns": [round(float(x), 8) for x in eq],
        "debt_annual_returns": [round(float(x), 8) for x in dbt],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    existed = OUT.exists()
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\n{'rewrote' if existed else 'wrote'} {OUT.relative_to(ROOT)}")
    print(f"  equity: {len(eq):>3} FYs   mean {eq.mean()*100:6.2f}%   "
          f"range {eq.min()*100:7.2f}% .. {eq.max()*100:7.2f}%")
    print(f"  debt:   {len(dbt):>3} FYs   mean {dbt.mean()*100:6.2f}%   "
          f"range {dbt.min()*100:7.2f}% .. {dbt.max()*100:7.2f}%")
    if existed:
        print("\nThis rewrote an existing fixture. Review `git diff` before "
              "committing, then regenerate the MC snapshot in the same commit:")
        print("  pytest tests/golden/test_mc_isolation.py --regen-golden -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
