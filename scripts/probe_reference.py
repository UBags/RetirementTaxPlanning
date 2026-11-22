#!/usr/bin/env python3
"""Reconcile the engine against the reference model, config a1.

The engine has never been run against the reference model's numbers. Every
figure so far comes from `tests/golden`, whose header says it is a synthetic
behavioural pin — internally consistent, but not evidence that the engine
reproduces the strategy this plan is designed around. This probe is the only
thing that runs the engine on reference-plan parameters, and closing that gap
is the top unverified item in the handover.

WHY a1 AND NOT THE HEADLINE CONFIGS

The interesting configs need the HUF ration and several folios. The shipped
engine has `huf_ration_k = 0.0` and `folio_cohort_threshold = 0.0`, so it has
neither. `a1` — one chunk at 7.25%, one folio, ration off — is exactly the
engine's current shape, and controls come first because they isolate one
mechanism at a time. The sweep then turns each gate on in memory.

TARGETS ARE COMPUTED LIVE, NOT HARDCODED

An earlier version pinned targets taken from `docs/two_purse_model.py`. That
model has since been removed and superseded by `scripts/swp_dp_model.py`, and
the pinned numbers silently became wrong — 16.47/1914.4 against the current
model's 0.56/1614.3 for the same config. A probe that prints a confident but
stale target is worse than one with no target at all.

`reference()` therefore imports the model and runs it, configured DOWN to the
engine's shape for each row. The targets can never go stale again; they move
when the model moves.

WHAT STILL CANNOT BE ALIGNED, AND HOW BIG EACH GAP IS

1. INCOME PATH. `OtherIncome` grows each stream by a COMPOUND percentage. The
   model uses a FLAT increment of 0.10/yr. A single compound rate matching both
   endpoints (15.80 → 18.70, implying 0.583%/yr) tracks it to within 0.061 at
   the worst point.

   This used to be the dominant error. The previous version targeted an older
   brief whose income rose by increments decaying from 0.50 to 0.15, reaching
   25.23 — which compound growth fits to only 1.862. Retargeting to the model's
   flat path cuts the irreducible income error by a factor of THIRTY. Since
   assessable income sets the tax-free headroom, and headroom sets gain
   capacity, that error propagates straight into tax.

2. THE HARVEST. The model realises gain inside the free band and rebuys,
   stepping up basis. The engine does not — Phase 08 is unshipped. This is a
   real strategy difference, not a calibration gap, and it is the largest
   remaining reason the two will disagree.

3. FOLIO OPENING. The engine gates on a GAIN FRACTION; the model opens on a
   vintage CADENCE. Both express "one folio" or "several", but not at the same
   moments. Rows 0.05 and 0.50 map to the same model configuration and so show
   the same target.

Report any discrepancy against these three before treating it as a defect.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "golden"))

import swp_dp_model as ref  # noqa: E402  (sibling in scripts/)
from golden_projects import PROJECTS  # noqa: E402

from swp_planner.config.configuration import config  # noqa: E402
from swp_planner.core.models import AppState  # noqa: E402
from swp_planner.core.net_income_plan import project_plan  # noqa: E402

# ── the live plan, aligned to scripts/swp_dp_model.py ───────────────────────
CORPUS = 350.0
REQUIREMENT = [(1, 5, 35.0), (6, 10, 37.5), (11, 15, 42.5),
               (16, 20, 45.0), (21, 25, 47.5), (26, 30, 50.0)]
INCOME_FY1 = 15.80
ASSESSABLE_FY1 = 5.95
INCOME_STEP = 0.10          # the model's flat annual increment
INCOME_STEPS = 29
HUF_INHERITS, HUF_INHERITS_FY = 190.0, 5
PERSONAL_EXEMPT = [12.0, 14.0, 16.0, 18.0, 20.0, 20.0]
HUF_NIL = [4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
RETURN_A1 = 7.25

_REF_CACHE: dict[tuple[float, float], tuple[float, float]] = {}


def reference_income_path():
    """The model's path: 15.80 rising by a flat 0.10 a year."""
    return [INCOME_FY1 + INCOME_STEP * k for k in range(INCOME_STEPS + 1)]


def implied_compound_rate():
    """The single compound rate the engine CAN express, matching endpoints."""
    path = reference_income_path()
    return ((path[-1] / path[0]) ** (1.0 / INCOME_STEPS) - 1.0) * 100.0


def reference(k: float, folio: float):
    """Run the reference model configured down to the engine's shape.

    Aligns every parameter that can be aligned — rate, ration, folio count —
    and switches OFF the equity sleeve, which the engine does not have at all.
    Cached: distinct engine gate pairs collapse onto three model configs.
    """
    key = (k, folio)
    if key in _REF_CACHE:
        return _REF_CACHE[key]

    saved = {n: getattr(ref, n) for n in
             ("INTEREST_RATE", "EQUITY_ENABLED", "RATION_K")}
    try:
        ref.INTEREST_RATE = RETURN_A1 / 100.0
        ref.EQUITY_ENABLED = False
        ref.RATION_K = k
        if folio <= 0.0:
            r = ref.simulate("1chunk", 30, 1, vintage_every=0, no_vintage=True)
        else:
            r = ref.simulate("1chunk", 30, 8, vintage_every=5)
    finally:
        for name, value in saved.items():
            setattr(ref, name, value)

    _REF_CACHE[key] = (r["tax"], r["corpus"])
    return _REF_CACHE[key]


def build_a1() -> dict:
    sc = copy.deepcopy(PROJECTS["main"]["scenarios"][0])

    # `allocation` is an absolute lakh amount per fund, NOT a percentage --
    # `allocation_params.total_money` is the optimiser's input and does not
    # seed the corpus. Setting only the latter left the plan running on 214.5
    # against a 35-50 requirement, exhausting the personal purse by FY17.
    live = [f for f in sc["funds"] if f.get("allocation", 0) > 0]
    scale = CORPUS / sum(f["allocation"] for f in live)
    for f in sc["funds"]:
        f["allocation"] = f.get("allocation", 0.0) * scale
    for ch in sc["allocation_chunks"]:
        for f in ch.get("funds", []):
            f["allocation"] = f.get("allocation", 0.0) * scale
    sc["allocation_params"]["total_money"] = CORPUS

    req = {}
    for lo, hi, v in REQUIREMENT:
        for fy in range(lo, hi + 1):
            req[str(fy)] = v
    sc["annual_requirements"] = req

    g = implied_compound_rate()
    # other_taxable / tax_free_interest carry no statutory deduction, so
    # assessable is exactly what is entered -- pension and rental would be
    # reduced by s.16ia and s.24a and miss the 5.95.
    sc["personal_income"] = {
        "salary": 0.0, "taxable_interest": 0.0,
        "tax_free_interest": INCOME_FY1 - ASSESSABLE_FY1,
        "pension": 0.0, "rental": 0.0,
        "other_taxable": ASSESSABLE_FY1, "other_non_taxable": 0.0,
        "tax_free_interest_growth": g, "other_taxable_growth": g,
    }
    sc["huf_income"] = {k: 0.0 for k in
                        ("salary", "taxable_interest", "tax_free_interest",
                         "pension", "rental", "other_taxable",
                         "other_non_taxable")}

    sc["windfalls"] = [{"year": HUF_INHERITS_FY, "amount": HUF_INHERITS,
                        "target": "huf"}]

    for chunk, lim in zip(sc["individual_debt_chunks"], PERSONAL_EXEMPT):
        chunk["exempt_limit"] = lim
    for chunk, lim in zip(sc["huf_debt_chunks"], HUF_NIL):
        chunk["exempt_limit"] = lim

    for f in sc["funds"]:
        for key in ("avg_cagr_3", "avg_cagr_5", "avg_cagr_7", "avg_cagr_10",
                    "worst_exp_ret"):
            f[key] = RETURN_A1
    for rc in sc["return_chunks"]:
        rc["annual_return"] = RETURN_A1 / 100.0

    sc["huf_withdrawal_chunks"] = []
    return sc


def totals(state):
    """Lifetime tax across both purses, and the FY30 corpus."""
    _pm, py, _hm, _hy, _sol = project_plan(state)
    tax = sum(y.tax_personal + getattr(y, "tax_huf", 0.0) for y in py)
    last = py[-1]
    end = (last.corpus_debt_personal + last.corpus_equity_personal
           + last.corpus_other_personal + last.corpus_debt_huf
           + last.corpus_equity_huf + last.corpus_other_huf)
    return tax, end


def with_gates(k: float, folio: float) -> None:
    """Override the gates IN MEMORY, never in the shipped configuration.

    The ration measured worse on five of five fixtures, and the rule was that
    if it is still worse it does not ship. Validating the engine against the
    reference needs the mechanism ON; shipping it needs a sweep nobody has run.
    `config` is a singleton over a dict, so a probe can have the first without
    committing the second.
    """
    config._ensure_loaded()
    config._data["huf_ration_k"] = k
    config._data["folio_cohort_threshold"] = folio


SWEEP = [
    (0.0, 0.00, "a1  ration off, one folio  <- the shipped engine"),
    (1.0, 0.00, ""),
    (1.2, 0.00, "a2  ration on,  one folio"),
    (1.5, 0.00, ""),
    (1.2, 0.05, ""),
    (1.2, 0.50, "b   ration on,  folios on"),
]


def main() -> None:
    skip_ref = "--no-reference" in sys.argv

    print(f"income: the engine expresses a compound {implied_compound_rate():.4f}%/yr; "
          f"the model adds a flat {INCOME_STEP:.2f}/yr. Worst mid-path gap ~0.06.")
    print(f"{'FY':>3} {'model':>10} {'engine':>10} {'gap':>8}")
    path = reference_income_path()
    with_gates(0.0, 0.0)
    state = AppState.from_dict(build_a1())
    for fy in (1, 5, 10, 15, 20, 25, 30):
        got = (state.personal_income.taxable(fy)
               + state.personal_income.non_taxable(fy))
        print(f"{fy:>3} {path[fy - 1]:>10.2f} {got:>10.2f} "
              f"{got - path[fy - 1]:>8.2f}")

    print()
    if skip_ref:
        print(f"{'k':>6} {'folio':>7} {'tax':>9} {'corpus':>10}   config")
    else:
        print(f"{'k':>6} {'folio':>7} {'tax':>9} {'corpus':>10}"
              f" | {'ref tax':>9} {'ref corpus':>11}   config")

    for k, folio, label in SWEEP:
        with_gates(k, folio)
        tax, end = totals(AppState.from_dict(build_a1()))
        row = f"{k:>6.2f} {folio:>7.2f} {tax:>9.2f} {end:>10.1f}"
        if not skip_ref:
            rtax, rend = reference(k, folio)
            row += f" | {rtax:>9.2f} {rend:>11.1f}"
        print(f"{row}   {label}")

    if not skip_ref:
        print("\nThe reference harvests and the engine does not (Phase 08 is "
              "unshipped). Subtract that, and the ~0.06 income gap, before "
              "reading any difference as an engine defect.")

    if "--monthly" in sys.argv:
        with_gates(1.2, 0.0)
        pm, _py, hm, _hy, _s = project_plan(AppState.from_dict(build_a1()))
        print()
        print(f"monthly rows available — personal {len(pm)}, HUF {len(hm)}")
        fields = [f for f in vars(pm[0])] if pm else []
        print("MonthlyRow fields:", ", ".join(fields[:14]))


if __name__ == "__main__":
    main()
