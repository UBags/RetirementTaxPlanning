#!/usr/bin/env python3
"""Unused tax-free capacity per FY, and the window each boundary needs.

METHODOLOGY §15.2 step 2. `probe7.py` reports headroom GROSS -- the whole
year's tax-free allowance. That is not what a transition may spend. Part I
§5.3 fixes the priority: cash withdrawal first, transition second, harvest
last. So the transition's budget is headroom NET of the gain the ordinary
withdrawal has already realised.

Sizing a window against gross headroom would over-commit it. That is the
B1d-ii shape -- a scheduler reporting success while breaching the constraint
it exists to satisfy -- and the methodology flags it twice.

    spare(t) = free_gain_capacity(t) - withdrawal_gain(t)

`withdrawal_gain` is the accumulator's realised gain less the share booked by
the boundary rebalance, both read from `Engine.gain_ledger`.

The per-boundary feasibility test then asks: what is the smallest M for which
the spare capacity over FY[N-M .. N+M] covers the gain that boundary must
realise? Cost does not rise with window length (Part I §7 G7), so the smallest
M that completes is the right choice, not the M that minimises cost.

TWO CAVEATS, both conservative:

1. The boundary gain is measured from TODAY's hard cut, which sells extra
   units to fund the tax on its own gain (`engine.py` ~1527-1604). At zero tax
   that loop never fires, so the real figure is lower. This is an upper bound.
2. Spare capacity is measured on today's run. Spreading the transition changes
   the corpus path and hence the withdrawal's own gain, so the profile shifts
   under the new policy. This is a first-order screen, not a prediction.

Usage:  python3 scripts/probe_capacity.py            # all five fixtures
        python3 scripts/probe_capacity.py main 1     # one scenario, per-FY
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "golden"))

from golden_projects import PROJECTS  # noqa: E402

from swp_planner.core.engine import (  # noqa: E402
    Engine,
    compute_slab_tax,
    free_gain_capacity,
)
from swp_planner.core.models import AppState  # noqa: E402
from swp_planner.core.net_income_plan import project_plan  # noqa: E402

SCENARIOS = [
    ("main", 0, "s0_1chunk"),
    ("main", 1, "s1_2chunk"),
    ("main", 2, "s2_3chunk"),
    ("main", 3, "s3_4chunk"),
    ("hist_8yr", 2, "hist_8yr"),
]


def analyse(proj: str, idx: int):
    """Return (state, per-FY rows, boundary rows) for one scenario."""
    state = AppState.from_dict(PROJECTS[proj]["scenarios"][idx])
    _pm, _py, _hm, _hy, solution = project_plan(state)
    eng = Engine(state, gross_schedule=solution.gross)
    eng.run()
    led = eng.gain_ledger

    rows = []
    for fy in state.fy_range():
        months = eng.months_in_fy(fy)
        gross_p = free_gain_capacity(
            state.get_debt_tax_chunk(fy, "individual"), "individual",
            state.personal_income.assessable(fy, months, "individual"))
        gross_h = free_gain_capacity(
            state.get_debt_tax_chunk(fy, "huf"), "huf",
            state.huf_income.assessable(fy, months, "huf"))

        ap = led["personal"].get(fy, {})
        ah = led["huf"].get(fy, {})
        rp = led["rebalance_personal"].get(fy, {})
        rh = led["rebalance_huf"].get(fy, {})

        # Slab-taxed gain only: debt is what free_gain_capacity measures.
        wd_p = max(0.0, ap.get("gain_d", 0.0) - rp.get("d", 0.0))
        wd_h = max(0.0, ah.get("gain_d", 0.0) - rh.get("d", 0.0))

        rows.append(dict(
            fy=fy, gross_p=gross_p, wd_p=wd_p,
            spare_p=max(0.0, gross_p - wd_p),
            gross_h=gross_h, wd_h=wd_h,
            spare_h=max(0.0, gross_h - wd_h),
            assess_p=state.personal_income.assessable(fy, months, "individual"),
            assess_h=state.huf_income.assessable(fy, months, "huf"),
            chunk_p=state.get_debt_tax_chunk(fy, "individual"),
            chunk_h=state.get_debt_tax_chunk(fy, "huf"),
            # Per purse. The personal and HUF rebalances are separate
            # operations (engine.py:1435 and 2058) and each is bounded by its
            # OWN entity's bands -- headroom is not fungible between them.
            bdy_p=sum(rp.get(k, 0.0) for k in "deo"),
            bdy_h=sum(rh.get(k, 0.0) for k in "deo"),
            rebal_gain=(sum(rp.get(k, 0.0) for k in "deo")
                        + sum(rh.get(k, 0.0) for k in "deo")),
        ))

    boundaries = [r for r in rows if r["rebal_gain"] > 1e-6]
    return state, rows, boundaries


def smallest_window(rows, n: int, need: float, horizon: int):
    """Smallest M for which spare over FY[n-M .. n+M] covers `need`."""
    spare = {r["fy"]: r["spare_p"] + r["spare_h"] for r in rows}
    for m in range(0, horizon + 1):
        lo, hi = max(1, n - m), min(horizon, n + m)
        cap = sum(spare.get(fy, 0.0) for fy in range(lo, hi + 1))
        if cap >= need:
            return m, lo, hi, cap
        if lo == 1 and hi == horizon:
            return None, lo, hi, cap
    return None, 1, horizon, 0.0


def forward_window(rows, n: int, need: float, horizon: int):
    """Smallest K for which spare over FY[n .. n+K] covers `need`.

    Part I §7 G6: where the transition moves toward a LOWER-returning
    allocation -- which every fixture does, returns stepping down at each
    chunk -- start as late as capacity allows, because every year spent
    part-transitioned into the poorer allocation is a real cost the tax figure
    never shows. A forward-only window is the strongest form of that rule.
    """
    spare = {r["fy"]: r["spare_p"] + r["spare_h"] for r in rows}
    for k in range(0, horizon - n + 1):
        cap = sum(spare.get(fy, 0.0) for fy in range(n, min(horizon, n + k) + 1))
        if cap >= need:
            return k, cap
    return None, sum(spare.get(fy, 0.0) for fy in range(n, horizon + 1))


def deadlines(boundaries, horizon: int) -> dict:
    """A boundary must complete before the NEXT boundary opens.

    You cannot still be transitioning into chunk 2 once chunk 3 has begun --
    the portfolio would be straddling three allocations at once. The last
    boundary's deadline is the horizon.
    """
    fys = [b["fy"] for b in boundaries]
    out = {}
    for i, fy in enumerate(fys):
        out[fy] = (fys[i + 1] - 1) if i + 1 < len(fys) else horizon
    return out


def schedule_all(rows, boundaries, horizon: int, respect_prior: bool = False):
    """Allocate spare capacity across ALL boundaries at once.

    METHODOLOGY §15.2: where windows overlap, shared headroom is allocated
    nearest-deadline-first and spent ONCE, never offered to each window. The
    per-boundary table sizes each window in isolation and therefore proves
    nothing about whether they coexist -- `s3_4chunk` needs 291.6 against
    339.7 of total spare, so 86% of every spare rupee has to land in the
    right year.

    Allocation runs BACKWARDS from each deadline. Part I §7 G6: start as late
    as capacity allows. Note this reasons differently in the engine than in
    the standalone model: `_fund_annual_return` takes each fund's own CAGR, so
    returns follow the funds actually HELD, not the chunk calendar. A late
    window therefore keeps the older funds -- and their higher returns --
    working for longer, which Part I §12 said the model could not see. It can.

    `respect_prior` forbids a window opening before the previous boundary, so
    the portfolio is never transitioning toward chunk k+1 while chunk k-1 is
    still live. Operationally sane; it is also what makes `s3_4chunk` FY24
    infeasible, which is the finding.

    Returns (results, used) where `used` is per-FY consumption, which the
    caller asserts never exceeds that year's spare -- the double-spend guard.
    """
    spare = {r["fy"]: r["spare_p"] + r["spare_h"] for r in rows}
    used = {fy: 0.0 for fy in spare}
    dl = deadlines(boundaries, horizon)
    fys = [b["fy"] for b in boundaries]

    results = []
    for b in sorted(boundaries, key=lambda x: dl[x["fy"]]):
        n, need = b["fy"], b["rebal_gain"]
        floor = 1
        if respect_prior:
            prior = [f for f in fys if f < n]
            floor = prior[-1] if prior else 1
        remaining, touched = need, []
        for fy in range(dl[n], floor - 1, -1):
            if remaining <= 1e-9:
                break
            avail = spare.get(fy, 0.0) - used[fy]
            if avail <= 1e-9:
                continue
            take = min(avail, remaining)
            used[fy] += take
            remaining -= take
            touched.append(fy)
        results.append(dict(
            fy=n, need=need, deadline=dl[n], floor=floor,
            start=min(touched) if touched else None,
            end=max(touched) if touched else None,
            assigned=need - remaining,
            complete=remaining <= 1e-6,
        ))
    return results, used


def bands(boundaries, horizon: int) -> dict:
    """The years a boundary's window may occupy: previous boundary to next-1.

    A chunk's portfolio is the minimum-risk portfolio achieving THAT chunk's
    target return, so it only does its job while it is actually held during
    that chunk. A window crossing a neighbouring boundary would leave the
    portfolio delivering the wrong chunk's risk profile, which is a mandate
    breach rather than a tax cost. The band is therefore a hard cap.
    """
    fys = [b["fy"] for b in boundaries]
    out = {}
    for i, fy in enumerate(fys):
        lo = fys[i - 1] if i > 0 else 1
        hi = (fys[i + 1] - 1) if i + 1 < len(fys) else horizon
        out[fy] = (lo, hi)
    return out


def centred_schedule(rows, boundaries, horizon: int, margin: float = 1.0):
    """Windows centred on N per `N +/- (M +/- m)`, capped at the band.

    Search M upward from 0 and take the smallest that completes -- cost does
    not rise with window length (Part I §7 G7), so a wider window buys nothing
    but risk of straddling. Stop growing once both ends hit the band.

    Spending runs CHRONOLOGICALLY across the window, each year taking what it
    can afford. Part I §7 G6: instalments track capacity, not a schedule.

    `margin` requires capacity >= need * margin before declaring completion.
    At 1.0 a window completing exactly is accepted; `hist_8yr` completes with
    0.8% to spare, which is a point estimate standing between zero and a tax
    bill, so 1.2 is the defensible setting once the shape is agreed.

    Where a boundary cannot complete inside its band the residual is REPORTED,
    not bought by widening. A scheduler that silently over-runs its mandate to
    protect a tax figure is the B1d-ii failure in a new costume (Part I §8).
    """
    spare = {r["fy"]: r["spare_p"] + r["spare_h"] for r in rows}
    used = {fy: 0.0 for fy in spare}
    band = bands(boundaries, horizon)

    results = []
    for b in sorted(boundaries, key=lambda x: band[x["fy"]][1]):
        n, need = b["fy"], b["rebal_gain"]
        lo_cap, hi_cap = band[n]
        chosen = (0, n, n, 0.0)
        for m_ in range(0, horizon + 1):
            lo, hi = max(lo_cap, n - m_), min(hi_cap, n + m_)
            cap = sum(spare[fy] - used[fy] for fy in range(lo, hi + 1))
            chosen = (m_, lo, hi, cap)
            if cap >= need * margin or (lo == lo_cap and hi == hi_cap):
                break
        m_, lo, hi, cap = chosen

        remaining = need
        for fy in range(lo, hi + 1):          # chronological instalments
            avail = spare[fy] - used[fy]
            if avail <= 1e-9:
                continue
            take = min(avail, remaining)
            used[fy] += take
            remaining -= take
            if remaining <= 1e-9:
                break

        results.append(dict(
            fy=n, need=need, M=m_, lo=lo, hi=hi, band=(lo_cap, hi_cap),
            assigned=need - remaining, residual=max(0.0, remaining),
            complete=remaining <= 1e-6,
        ))
    return results, used


def centred_by_purse(rows, boundaries, horizon: int, margin: float = 1.0):
    """Centred windows, but each purse spends only its OWN headroom.

    `centred_schedule` pools personal and HUF spare. That is wrong in
    principle: the two rebalances are separate operations bounded by separate
    tax bands, and pooling hides a purse-specific shortfall behind the other
    purse's slack. It happens not to change `s3_4chunk` FY24 -- both purses
    saturate, so the residual is 110.0 either way -- but it can change M
    elsewhere, and a screen that is right by luck is not a screen.

    One window per boundary, shared by both purses: a chunk transition is a
    single event and the plan moves both portfolios together. M is the
    smallest for which BOTH purses complete, clamped at the band.
    """
    spare = {"p": {r["fy"]: r["spare_p"] for r in rows},
             "h": {r["fy"]: r["spare_h"] for r in rows}}
    used = {k: {fy: 0.0 for fy in spare[k]} for k in spare}
    band = bands(boundaries, horizon)

    results = []
    for b in sorted(boundaries, key=lambda x: band[x["fy"]][1]):
        n = b["fy"]
        need = {"p": b["bdy_p"], "h": b["bdy_h"]}
        lo_cap, hi_cap = band[n]
        chosen = (0, n, n, {"p": 0.0, "h": 0.0})
        for m_ in range(0, horizon + 1):
            lo, hi = max(lo_cap, n - m_), min(hi_cap, n + m_)
            cap = {k: sum(spare[k][fy] - used[k][fy] for fy in range(lo, hi + 1))
                   for k in spare}
            chosen = (m_, lo, hi, cap)
            if (all(cap[k] >= need[k] * margin for k in spare)
                    or (lo == lo_cap and hi == hi_cap)):
                break
        m_, lo, hi, _cap = chosen

        residual, spent = {}, {}
        for k in spare:
            r_ = need[k]
            per_year = {}
            for fy in range(lo, hi + 1):        # chronological instalments
                avail = spare[k][fy] - used[k][fy]
                if avail <= 1e-9:
                    continue
                take = min(avail, r_)
                used[k][fy] += take
                per_year[fy] = take
                r_ -= take
                if r_ <= 1e-9:
                    break
            residual[k] = max(0.0, r_)
            spent[k] = per_year

        results.append(dict(
            fy=n, M=m_, lo=lo, hi=hi, band=(lo_cap, hi_cap),
            need=need, residual=residual, spent=spent,
            complete=all(v <= 1e-6 for v in residual.values()),
        ))
    return results, used


def _bases(rows, used, key: str, entity_key: str):
    """Slab income each year already carries, before any residual is added."""
    wd = "wd_p" if key == "p" else "wd_h"
    assess = "assess_p" if key == "p" else "assess_h"
    return {r["fy"]: r[assess] + r[wd] + used[key].get(r["fy"], 0.0)
            for r in rows}


def price_residual(rows, used, key: str, years, amount: float,
                   step: float = 0.05):
    """Tax on unfunded gain: dumped at one year versus spread across the band.

    Part I §8. Once tax is unavoidable it is still a choice WHERE to pay it.
    Slab tax is convex in income, so spreading a residual across years fills
    the cheapest brackets first; dumping it in one year drives straight to the
    top rate. This water-fills by repeatedly adding a small increment to
    whichever year currently shows the lowest marginal rate.

    Returns (tax_if_dumped_at_worst_year, tax_if_spread, per-year spread).
    """
    entity = "individual" if key == "p" else "huf"
    chunk_key = "chunk_p" if key == "p" else "chunk_h"
    chunks = {r["fy"]: r[chunk_key] for r in rows}
    bases = _bases(rows, used, key, entity)

    def tax(fy, extra):
        c = chunks[fy]
        return (compute_slab_tax(bases[fy] + extra, c, entity)
                - compute_slab_tax(bases[fy], c, entity))

    years = [fy for fy in years if fy in bases]
    if not years or amount <= 1e-9:
        return 0.0, 0.0, {}

    dumped = max(tax(fy, amount) for fy in years)

    extra = {fy: 0.0 for fy in years}
    remaining = amount
    while remaining > 1e-9:
        s = min(step, remaining)
        best = min(years,
                   key=lambda fy: tax(fy, extra[fy] + s) - tax(fy, extra[fy]))
        extra[best] += s
        remaining -= s
    spread = sum(tax(fy, e) for fy, e in extra.items())
    return dumped, spread, {fy: e for fy, e in extra.items() if e > 1e-6}


def main() -> None:
    targets = SCENARIOS
    detail = False
    if len(sys.argv) >= 3:
        proj, idx = sys.argv[1], int(sys.argv[2])
        targets = [(proj, idx, f"{proj}[{idx}]")]
        detail = True

    for proj, idx, label in targets:
        state, rows, boundaries = analyse(proj, idx)
        horizon = max(r["fy"] for r in rows)

        if detail:
            print(f"=== {label} — per-FY capacity ===")
            print(f"{'FY':>3} {'gross_p':>8} {'wd_p':>7} {'spare_p':>8} "
                  f"{'gross_h':>8} {'wd_h':>7} {'spare_h':>8} {'bdy_gain':>9}")
            print("-" * 64)
            for r in rows:
                print(f"{r['fy']:>3} {r['gross_p']:>8.2f} {r['wd_p']:>7.2f} "
                      f"{r['spare_p']:>8.2f} {r['gross_h']:>8.2f} "
                      f"{r['wd_h']:>7.2f} {r['spare_h']:>8.2f} "
                      f"{r['rebal_gain']:>9.2f}")
            print("-" * 64)

        tot_spare = sum(r["spare_p"] + r["spare_h"] for r in rows)
        tot_gross = sum(r["gross_p"] + r["gross_h"] for r in rows)
        print(f"\n{label}: lifetime gross headroom {tot_gross:.1f}, "
              f"spare after withdrawals {tot_spare:.1f} "
              f"({tot_spare / tot_gross:.0%} unused)")

        if not boundaries:
            print("  no boundary realises gain — nothing to schedule")
            continue
        for b in boundaries:
            need = b["rebal_gain"]
            m, lo, hi, cap = smallest_window(rows, b["fy"], need, horizon)
            verdict = (f"M={m}  window FY{lo}-{hi}  capacity {cap:.1f}"
                       if m is not None
                       else f"UNREACHABLE  best capacity {cap:.1f}")
            print(f"  boundary FY{b['fy']:<3} gain {need:>7.1f}  ->  {verdict}")
            k, fcap = forward_window(rows, b["fy"], need, horizon)
            fwd = (f"K={k}  window FY{b['fy']}-{min(horizon, b['fy'] + k)}"
                   f"  capacity {fcap:.1f}"
                   if k is not None
                   else f"UNREACHABLE forward-only  capacity {fcap:.1f}")
            print(f"{'':>17}forward-only  ->  {fwd}")

        # Do the windows coexist? The table above sizes each in isolation.
        spare = {r["fy"]: r["spare_p"] + r["spare_h"] for r in rows}
        for respect in (False, True):
            sched, used = schedule_all(rows, boundaries, horizon, respect)
            over = [fy for fy, u in used.items() if u > spare[fy] + 1e-6]
            title = ("windows may open no earlier than the previous boundary"
                     if respect else "windows may open anywhere")
            print(f"  -- simultaneous allocation, {title} --")
            for s in sched:
                state_s = "completes" if s["complete"] else "STARVES"
                span = (f"FY{s['start']}-{s['end']}"
                        if s["start"] is not None else "no years")
                print(f"  boundary FY{s['fy']:<3} deadline FY{s['deadline']:<3}"
                      f" {span:>10}  assigned {s['assigned']:>7.1f}"
                      f" of {s['need']:>7.1f}  {state_s}")
            tot_used = sum(used.values())
            print(f"  allocated {tot_used:.1f} of {sum(s['need'] for s in sched):.1f}"
                  f" needed, spare {sum(spare.values()):.1f}"
                  f" ({tot_used / sum(spare.values()):.0%} utilisation);"
                  + (" GUARD BREACHED " + str(over) if over
                     else " no year over-spent"))

        # The policy the plan actually mandates: centred on N, capped so a
        # window never crosses a neighbouring boundary.
        sched, used = centred_schedule(rows, boundaries, horizon)
        over = [fy for fy, u in used.items() if u > spare[fy] + 1e-6]
        print("  -- centred windows N +/- M, capped at the adjacent boundary --")
        resid_total = 0.0
        for s in sched:
            state_s = "completes" if s["complete"] else "RESIDUAL"
            print(f"  boundary FY{s['fy']:<3} band FY{s['band'][0]}-{s['band'][1]}"
                  f"  M={s['M']:<2} window FY{s['lo']}-{s['hi']}"
                  f"  assigned {s['assigned']:>7.1f} of {s['need']:>7.1f}"
                  f"  residual {s['residual']:>6.1f}  {state_s}")
            resid_total += s["residual"]
        print(f"  unfunded gain {resid_total:.1f}"
              + (" GUARD BREACHED " + str(over) if over
                 else "; no year over-spent"))

        # Per purse — headroom is not fungible between the two entities.
        sched, used = centred_by_purse(rows, boundaries, horizon)
        print("  -- per purse, centred and band-capped --")
        for s in sched:
            for k, name in (("p", "personal"), ("h", "HUF     ")):
                r_ = s["residual"][k]
                mark = "zero" if r_ <= 1e-6 else f"RESIDUAL {r_:.1f}"
                print(f"  FY{s['fy']:<3} {name} M={s['M']:<2}"
                      f" FY{s['lo']}-{s['hi']}"
                      f"  need {s['need'][k]:>7.1f}"
                      f"  funded {s['need'][k] - r_:>7.1f}   {mark}")
            if not s["complete"]:
                years = range(s["lo"], s["hi"] + 1)
                for k, name in (("p", "personal"), ("h", "HUF")):
                    r_ = s["residual"][k]
                    if r_ <= 1e-6:
                        continue
                    dumped, spread, _plan = price_residual(
                        rows, used, k, years, r_)
                    print(f"       {name} residual {r_:>6.1f}"
                          f" -> tax {dumped:>6.2f} if dumped in one year,"
                          f" {spread:>6.2f} if spread over FY{s['lo']}-{s['hi']}")


if __name__ == "__main__":
    main()
