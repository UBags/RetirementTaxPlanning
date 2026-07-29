"""Capacity-driven transition planning. Pure — no engine imports.

Phase 07. A chunk boundary changes the target allocation, and the engine has
until now moved the whole portfolio in one financial year. On the golden
corpus that single act is not the dominant source of lifetime tax, it is very
nearly the *only* source: on `s1_2chunk` the FY16 boundary and its FY17
aftershock account for 74.78 of a 74.78 lifetime bill, with twenty-eight years
contributing nothing at all.

The fix is to spread each boundary's turnover across a window of years,
spending only the tax-free headroom each year has left once the ordinary
withdrawal has taken its share.

WHAT DECIDES THE WINDOW SHAPE

Windows are centred on the boundary — `N ± (M ± m)` — and hard-capped so they
never cross a neighbouring boundary. That cap is not a tax judgement. Each
chunk's portfolio is the minimum-risk portfolio achieving THAT chunk's target
return, so it only does its job while actually held during that chunk. A
window running past a neighbouring boundary would leave the plan delivering
the wrong chunk's risk profile, which is a mandate breach and not something a
lower tax figure can buy back.

Measurement rejected the two obvious alternatives. Forward-only windows fail
outright on the largest boundaries — `s3_4chunk` FY24 has 49.1 of capacity in
the seven years after it against a 237.2 need. Deadline-driven windows
complete, but produce schedules finishing AFTER the boundary they serve:
`s3_4chunk` FY9 would transition during FY13–16, arriving in chunk 2's
allocation as chunk 3 began.

WHY M IS THE SMALLEST THAT COMPLETES

Cost does not rise with window length (METHODOLOGY Part I §7 G7): a
capacity-driven transition never pays tax, and the window decides whether it
FINISHES, not what it costs. A wider window than necessary buys nothing and
straddles two allocations for longer, so the search takes the smallest M that
completes and stops.

WHY HEADROOM IS PER PURSE

The personal and HUF rebalances are separate operations bounded by separate
tax bands, so their headroom is not fungible. Pooling them understates the
window — `hist_8yr` FY16 needs M=6, and a pooled calculation reports M=5.

WHY THERE IS NO TURNOVER THRESHOLD

`s3_4chunk` FY17 changes no fund at all, only weights, yet still realises 11.4
of gain. A rule keyed on "turnover ≈ 0" would open no window and book the gain
anyway. The rule that falls out of the search instead is "year N covers its
own gain", which is M=0 and needs no magic number.

WHERE ZERO IS UNREACHABLE

`s3_4chunk` FY24 must realise 237.2 with only 127.2 available inside its band.
The residual is REPORTED, never bought by widening the window past the cap. A
scheduler that quietly over-runs its mandate to protect a tax figure is the
B1d-ii failure in a new costume (Part I §8). `spread_residual` then places the
unavoidable gain in the cheapest brackets available rather than dumping it at
the boundary — worth 30.83 → 25.51 on that boundary, almost all of it HUF.

No caller yet. Landed with tests ahead of the wiring that consumes it, the
same sequence `free_gain_capacity` and `cash_within_gain_budget` used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Sequence, Tuple

PURSES: Tuple[str, ...] = ("personal", "huf")

__all__ = [
    "PURSES", "Boundary", "Window", "TransitionPlan",
    "Measurement", "Convergence",
    "normalise", "turnover_fraction", "bands", "build_plan",
    "plans_equal", "spread_residual", "converge",
    "distance_to_target", "rebalance_moves",
]


# ─── allocation arithmetic ────────────────────────────────────────────────────

def normalise(alloc: Mapping[str, float]) -> Dict[str, float]:
    """Active-fund weights summing to 1.

    Takes raw allocations exactly as the engine's rebalance does --
    `f.allocation / sum(allocation over active funds)` -- rather than the
    stored `target_weights`, which is the optimiser's output and is not what
    the rebalance reads. Non-positive allocations are dropped.
    """
    active = {n: float(a) for n, a in alloc.items() if a > 0}
    total = sum(active.values())
    if total <= 0:
        return {}
    return {n: a / total for n, a in active.items()}


def turnover_fraction(old: Mapping[str, float],
                      new: Mapping[str, float]) -> float:
    """Share of corpus that must actually be sold and rebought.

        sum_f max(0, w_old_f - w_new_f)  ==  0.5 * sum_f |w_old_f - w_new_f|

    Not the naive "everything changes". The portfolios either side of a
    boundary are constructed to overlap as much as their return targets allow,
    precisely so this number stays small, and it must be read off the actual
    fund lists.
    """
    w_old, w_new = normalise(old), normalise(new)
    if not w_old or not w_new:
        return 0.0
    return sum(max(0.0, w_old.get(n, 0.0) - w_new.get(n, 0.0))
               for n in set(w_old) | set(w_new))


def bands(boundary_fys: Sequence[int], horizon: int) -> Dict[int, Tuple[int, int]]:
    """Years each boundary's window may occupy: previous boundary to next-1."""
    fys = sorted(boundary_fys)
    out: Dict[int, Tuple[int, int]] = {}
    for i, fy in enumerate(fys):
        lo = fys[i - 1] if i > 0 else 1
        hi = (fys[i + 1] - 1) if i + 1 < len(fys) else horizon
        out[fy] = (lo, hi)
    return out


# ─── plan objects ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Boundary:
    """A chunk boundary and the gain each purse must realise to cross it."""
    fy: int
    turnover: float
    gain: Mapping[str, float]

    def need(self, purse: str) -> float:
        return float(self.gain.get(purse, 0.0))


@dataclass(frozen=True)
class Window:
    fy: int
    m: int
    lo: int
    hi: int
    band: Tuple[int, int]
    instalments: Mapping[str, Mapping[int, float]]
    residual: Mapping[str, float]
    clamped: bool

    @property
    def complete(self) -> bool:
        return all(v <= 1e-6 for v in self.residual.values())


@dataclass(frozen=True)
class TransitionPlan:
    windows: Tuple[Window, ...] = field(default_factory=tuple)

    def budget(self, purse: str, fy: int) -> float:
        """Gain this purse may realise on transitions in `fy`, all windows."""
        return sum(w.instalments.get(purse, {}).get(fy, 0.0)
                   for w in self.windows)

    def residual_total(self, purse: str | None = None) -> float:
        purses = PURSES if purse is None else (purse,)
        return sum(w.residual.get(p, 0.0) for w in self.windows for p in purses)

    @property
    def complete(self) -> bool:
        return all(w.complete for w in self.windows)


# ─── the search ───────────────────────────────────────────────────────────────

def build_plan(boundaries: Sequence[Boundary],
               spare: Mapping[str, Mapping[int, float]],
               horizon: int,
               margin: float = 1.0) -> TransitionPlan:
    """Centred, band-capped windows; shared headroom spent exactly once.

    `spare[purse][fy]` is the gain that purse may still realise tax-free in
    `fy` AFTER the ordinary withdrawal has taken its share. Gross headroom is
    the wrong input: the withdrawal is charged first (Part I §5.3), and sizing
    against gross would over-commit every window.

    Boundaries are served nearest-deadline-first so the tightest is fed before
    a later one can eat its years, and every rupee of capacity is decremented
    as it is spent -- never offered to two windows. Double-spending produces a
    scheduler that reports success while over-committing.

    `margin` requires capacity >= need * margin before a window is declared
    complete. At 1.0 a window completing exactly is accepted; `hist_8yr`
    completes with 0.8% to spare, a point estimate standing between zero tax
    and a bill, so a caller wanting resilience should pass something above 1.
    """
    if not boundaries:
        return TransitionPlan()

    remaining_cap = {p: {int(fy): float(v) for fy, v in spare.get(p, {}).items()}
                     for p in PURSES}
    band = bands([b.fy for b in boundaries], horizon)

    windows = []
    for b in sorted(boundaries, key=lambda x: band[x.fy][1]):
        lo_cap, hi_cap = band[b.fy]
        lo = hi = b.fy
        m = 0
        for m in range(0, horizon + 1):
            lo, hi = max(lo_cap, b.fy - m), min(hi_cap, b.fy + m)
            enough = all(
                sum(remaining_cap[p].get(fy, 0.0) for fy in range(lo, hi + 1))
                >= b.need(p) * margin
                for p in PURSES)
            if enough or (lo == lo_cap and hi == hi_cap):
                break

        instalments: Dict[str, Dict[int, float]] = {}
        residual: Dict[str, float] = {}
        for p in PURSES:
            outstanding = b.need(p)
            per_year: Dict[int, float] = {}
            for fy in range(lo, hi + 1):        # chronological instalments
                if outstanding <= 1e-9:
                    break
                avail = remaining_cap[p].get(fy, 0.0)
                if avail <= 1e-9:
                    continue
                take = min(avail, outstanding)
                remaining_cap[p][fy] = avail - take
                per_year[fy] = take
                outstanding -= take
            instalments[p] = per_year
            residual[p] = max(0.0, outstanding)

        windows.append(Window(
            fy=b.fy, m=m, lo=lo, hi=hi, band=(lo_cap, hi_cap),
            instalments=instalments, residual=residual,
            clamped=(lo == lo_cap and hi == hi_cap),
        ))

    return TransitionPlan(tuple(sorted(windows, key=lambda w: w.fy)))


def plans_equal(a: TransitionPlan, b: TransitionPlan, tol: float = 1e-6) -> bool:
    """Convergence test for the fixed-point driver.

    Compares the instalment schedules themselves, not a summary of them. A
    comparison that can only return True would stop the fixed point after one
    pass while reporting success, so this is pinned from both sides by test.
    """
    if len(a.windows) != len(b.windows):
        return False
    for wa, wb in zip(a.windows, b.windows):
        if (wa.fy, wa.lo, wa.hi) != (wb.fy, wb.lo, wb.hi):
            return False
        for p in PURSES:
            ia, ib = wa.instalments.get(p, {}), wb.instalments.get(p, {})
            if set(ia) != set(ib):
                return False
            if any(abs(ia[fy] - ib[fy]) > tol for fy in ia):
                return False
            if abs(wa.residual.get(p, 0.0) - wb.residual.get(p, 0.0)) > tol:
                return False
    return True


def spread_residual(years: Sequence[int], amount: float,
                    tax_of: Callable[[int, float], float],
                    step: float = 0.05) -> Dict[int, float]:
    """Place unavoidable gain in the cheapest brackets available.

    Part I §8: once tax cannot be avoided it is still a choice WHERE to pay
    it. Slab tax is convex in income, so a residual spread across years fills
    low brackets first while the same amount dumped in one year drives
    straight to the top rate. Water-fills by repeatedly adding `step` to
    whichever year currently shows the lowest marginal rate.

    `tax_of(fy, extra) -> tax` is supplied by the caller. Rates depend on the
    entity and on the user's slabs, which are the engine's business; the
    callback keeps this module free of tax policy, exactly as
    `FolioSet.sell_to_tax_budget` does.
    """
    years = list(years)
    if not years or amount <= 1e-9:
        return {}
    extra = {fy: 0.0 for fy in years}
    outstanding = amount
    while outstanding > 1e-9:
        s = min(step, outstanding)
        best = min(years, key=lambda fy: (tax_of(fy, extra[fy] + s)
                                          - tax_of(fy, extra[fy])))
        extra[best] += s
        outstanding -= s
    return {fy: v for fy, v in extra.items() if v > 1e-9}


# ─── the fixed point ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Measurement:
    """What one projection reveals about a run.

    `spare[purse][fy]` is gain still realisable tax-free after the ordinary
    withdrawal has taken its share; `boundaries` carries the gain each purse
    must realise to cross each boundary.
    """
    boundaries: Tuple[Boundary, ...]
    spare: Mapping[str, Mapping[int, float]]
    horizon: int


@dataclass(frozen=True)
class Convergence:
    plan: TransitionPlan
    iterations: int
    converged: bool
    oscillated: bool
    residuals: Tuple[float, ...]
    chosen: int


def converge(project: Callable[[TransitionPlan | None], Measurement],
             max_iterations: int = 5,
             margin: float = 1.2) -> Convergence:
    """Iterate a transition plan to a fixed point.

    Every window is sized against the spare capacity a run leaves it, and
    spreading a transition CHANGES that capacity: the boundary stops dumping
    its whole turnover into one year, so the withdrawal's own gain in the
    surrounding years shifts and the spare profile moves under the plan that
    was sized against it. Measuring once and building on it takes the numbers
    from a run the fix then invalidates. So: measure, plan, re-measure under
    that plan, and repeat until the plan stops moving.

    `project(plan)` runs a projection under `plan` and returns what it
    measured; `project(None)` is the unscheduled baseline. Passing it in keeps
    this function free of engine imports and -- more to the point -- lets the
    oscillation and capping paths be tested with stubs that actually respond
    to a plan, which the engine cannot do until the rebalance sites consume
    one.

    `project` is called at most `max_iterations` times, so the cost is bounded
    and visible. That ceiling matters: `project_plan` already runs the engine
    three or four times to solve the net-cash schedule.

    WHY THE MARGIN DEFAULTS ABOVE 1.0

    At margin 1.0 the window search is knife-edge -- with 9.0 of capacity a
    year, a need of exactly 45.0 takes M=2 and one nano-rupee more takes M=3
    (`test_window_choice_is_knife_edge_at_margin_one`). Since the need is
    re-measured every pass, a boundary near an exact multiple of capacity can
    alternate between two widths forever, exhausting the cap while looking
    like a mere failure to settle. A margin above 1.0 moves the decision off
    that edge, and separately buys back the case where a window completes with
    under a percent to spare.

    ON NOT CONVERGING

    Non-convergence is reported, never disguised. The plan returned is the one
    with the least unfunded gain among those seen, earliest on a tie, which is
    deterministic and is the best of a bad set rather than an arbitrary last
    value. `oscillated` distinguishes a cycle from slow drift, because the two
    want different responses: a cycle wants a wider margin, drift wants a
    higher cap.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    m = project(None)
    plan = build_plan(m.boundaries, m.spare, m.horizon, margin)
    seen = [plan]
    oscillated = False

    for i in range(2, max_iterations + 1):
        m = project(plan)
        nxt = build_plan(m.boundaries, m.spare, m.horizon, margin)
        if plans_equal(nxt, plan):
            return Convergence(
                plan=nxt, iterations=i, converged=True, oscillated=False,
                residuals=tuple(p.residual_total() for p in seen + [nxt]),
                chosen=len(seen))
        if any(plans_equal(nxt, older) for older in seen[:-1]):
            oscillated = True
        seen.append(nxt)
        plan = nxt

    chosen, best = min(enumerate(seen),
                       key=lambda kv: (kv[1].residual_total(), kv[0]))
    return Convergence(
        plan=best, iterations=max_iterations, converged=False,
        oscillated=oscillated,
        residuals=tuple(p.residual_total() for p in seen), chosen=chosen)


# ─── translating a slice into fund movements ──────────────────────────────────

def distance_to_target(values: Mapping[str, float],
                       new_weights: Mapping[str, float]) -> float:
    """Value that must still move to reach `new_weights`.

    The sum of positive excesses: for each fund, how far it sits ABOVE its
    share of the new allocation. Retiring funds have a target of zero, so
    their whole balance counts. Sells and buys are equal by construction, so
    this single number is the size of the remaining transition.

    Recomputed each year rather than fixed at the window's first year. Part I
    §7 G6 sets the target once, in rupees, at the start year; recomputing is
    self-correcting instead -- it absorbs growth and any drift the ordinary
    withdrawals introduce, and it cannot leave a transition reporting itself
    complete while the portfolio is still off target. The difference is small
    while instalments outrun growth and matters when they do not.
    """
    total = sum(v for v in values.values() if v > 0)
    if total <= 0:
        return 0.0
    w_new = normalise(new_weights)
    return sum(max(0.0, values.get(n, 0.0) - total * w_new.get(n, 0.0))
               for n in set(values) | set(w_new))


def rebalance_moves(values: Mapping[str, float],
                    new_weights: Mapping[str, float],
                    amount: float) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Sells and buys moving `amount` of value toward the new allocation.

    Returns `(sells, buys)`, each fund name to a positive rupee amount, with
    `sum(sells) == sum(buys) == min(amount, distance_to_target(...))`. Nothing
    is created or destroyed: the slice leaves over-weight funds and arrives in
    under-weight ones, pro rata to how far each is from its target.

    Selling only from funds ABOVE target is what makes a partial transition
    coherent. A naive slice that sold pro rata across the whole portfolio
    would churn funds already at their target -- realising gain, paying exit
    loads and moving nothing -- and the transition would never converge.

    `amount` is the rupee value the year's gain budget affords, which the
    caller obtains from `TransitionPlan.budget` via `cash_within_gain_budget`.
    Passing an amount at or beyond the remaining distance completes the
    transition exactly; passing more never overshoots.
    """
    total = sum(v for v in values.values() if v > 0)
    if total <= 0 or amount <= 1e-12:
        return {}, {}

    w_new = normalise(new_weights)
    names = set(values) | set(w_new)
    excess, deficit = {}, {}
    for n in names:
        gap = values.get(n, 0.0) - total * w_new.get(n, 0.0)
        if gap > 1e-12:
            excess[n] = gap
        elif gap < -1e-12:
            deficit[n] = -gap

    move = min(amount, sum(excess.values()), sum(deficit.values()))
    if move <= 1e-12:
        return {}, {}

    e_tot, d_tot = sum(excess.values()), sum(deficit.values())
    sells = {n: move * (v / e_tot) for n, v in excess.items()}
    buys = {n: move * (v / d_tot) for n, v in deficit.items()}
    return sells, buys
