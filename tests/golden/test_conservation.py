"""
Conservation invariants for Engine.run().

These exist because the golden snapshots could not catch the bug that motivated
them. Snapshots are within-entity and freeze whatever the engine does; these
assert that money is neither created nor destroyed, which is true regardless of
what the engine does.

The bug they were written for: the April tax event debited only the aggregate
buckets, which are rebuilt from the per-fund buckets moments later. Personal
income tax (Rs 84L over 30y) and the HUF tax-saving transfer (Rs 66L, compounding
to Rs 296L of phantom HUF corpus) were both silently discarded.

Each test below fails against the unfixed engine.
"""
from __future__ import annotations

import copy

import pytest


def _run(state):
    from swp_planner.core.engine import Engine
    return Engine(state).run()


def _personal(y):
    return y.corpus_debt_personal + y.corpus_equity_personal + y.corpus_other_personal


def _huf(y):
    return y.corpus_debt_huf + y.corpus_equity_huf + y.corpus_other_huf


def _with_fd_rate(state, rate: float):
    """Copy of *state* with a flat FD benchmark rate.

    The FD rate drives fd_tax -> tax_saved -> the size of the HUF transfer,
    without touching the actual portfolio in any other way.
    """
    from swp_planner.core.models import FDRateChunk

    s = copy.deepcopy(state)
    s.fd_rate_chunks = [FDRateChunk(year_from=1, year_to=30, fd_rate=rate)]
    s.fd_rate = rate
    return s


def test_the_fd_benchmark_does_not_touch_the_portfolio(scenario):
    """
    fd_tax_benchmark and tax_saved are REPORTED comparisons -- "would an FD
    have done better?" -- and must not move a rupee.

    Before Phase 3 the FD rate drove tax_saved, which drove a transfer out of
    personal into the HUF, so changing the rate changed the portfolio. It no
    longer should. Two runs at 2% and 12% must produce identical corpora.

    This replaces test_huf_transfer_debits_personal_corpus, which asserted
    the opposite and would now merely skip.
    """
    label, state = scenario

    _, y_lo, _, _ = _run(_with_fd_rate(state, 0.02))
    _, y_hi, _, _ = _run(_with_fd_rate(state, 0.12))

    assert sum(r.fd_tax_benchmark for r in y_hi) > \
           sum(r.fd_tax_benchmark for r in y_lo) + 1.0, (
        f"{label}: the FD benchmark did not respond to the FD rate at all, "
        "so this test is not exercising what it claims to."
    )

    bad = [(a.year, _personal(a), _personal(b), _huf(a), _huf(b))
           for a, b in zip(y_lo, y_hi)
           if abs(_personal(a) - _personal(b)) > 1e-6
           or abs(_huf(a) - _huf(b)) > 1e-6]
    assert not bad, (
        f"{label}: the FD benchmark moved the portfolio in {len(bad)} year(s). "
        "It is a reported comparison and must not. "
        f"First: {bad[0]}"
    )

def test_personal_tax_debits_corpus(scenario, monkeypatch):
    """
    Paying no tax must leave more corpus than paying tax. If the two runs agree,
    tax is being reported but never deducted from the portfolio.
    """
    label, state = scenario
    from swp_planner.core import engine

    _, y_base, _, _ = _run(copy.deepcopy(state))
    total_tax = sum(r.tax_personal for r in y_base)
    if total_tax < 1.0:
        pytest.skip(f"{label}: negligible tax over the plan; nothing to test")

    monkeypatch.setattr(engine, "compute_slab_tax", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "compute_ltcg_individual", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "compute_ltcg_other_individual", lambda *a, **k: 0.0)
    _, y_free, _, _ = _run(copy.deepcopy(state))

    idx = max((i for i, r in enumerate(y_base) if _personal(r) > 1.0), default=0)
    p_base, p_free = _personal(y_base[idx]), _personal(y_free[idx])

    assert p_free > p_base + 1e-3, (
        f"{label} FY{y_base[idx].year}: corpus is identical with and without tax "
        f"({p_base:.6f} vs {p_free:.6f}) despite {total_tax:.2f}L of tax reported "
        "over the plan. Tax is not being deducted from the portfolio."
    )


def test_monthly_corpus_flow_balances(scenario):
    """
    Within any month: corpus_end == corpus_start - withdrawal.

    Growth is applied before corpus_start is read, so no growth term appears.
    A non-zero residual means money entered or left the aggregate without being
    recorded as a withdrawal -- exactly how the tax and HUF-transfer leaks
    manifested. Worst residual across all four scenarios: 5.7e-14.
    """
    label, state = scenario
    p_rows, _, h_rows, _ = _run(state)

    for entity, rows in (("personal", p_rows), ("huf", h_rows)):
        for r in rows:
            start = r.corpus_debt_start + r.corpus_equity_start + r.corpus_other_start
            end = r.corpus_debt_end + r.corpus_equity_end + r.corpus_other_end
            wd = r.wd_debt + r.wd_equity + r.wd_other
            resid = (start - wd) - end
            assert abs(resid) < 1e-6, (
                f"{label}/{entity} m{r.month_idx}: corpus_start {start:.6f} - "
                f"withdrawal {wd:.6f} != corpus_end {end:.6f} "
                f"(residual {resid:.9f})"
            )


def _assert_aggregate_matches_per_fund(label, rows, entity):
    """Shared body: aggregate corpus must equal the sum of per-fund detail.

    Months where the waterfall concentrates on a few over-weight funds are
    excluded: engine.py skips funds with amt_f < 0.01, so fund_withdrawals is a
    record of withdrawals, not a portfolio snapshot. Comparing the two in those
    months compares different sets of funds.
    """
    n_full = max((len(r.fund_withdrawals) for r in rows), default=0)
    if n_full == 0:
        pytest.skip(f"{label}/{entity}: no per-fund detail recorded")

    checked = 0
    for r in rows:
        if len(r.fund_withdrawals) != n_full:
            continue        # waterfall concentrated; not a full snapshot
        agg = r.corpus_debt_end + r.corpus_equity_end + r.corpus_other_end
        det = sum(fd.corpus_end for fd in r.fund_withdrawals)
        tol = max(0.01, 1e-6 * abs(agg))
        assert abs(agg - det) <= tol, (
            f"{label}/{entity} m{r.month_idx} (FY{r.fy_year}): aggregate "
            f"{agg:.6f} vs per-fund {det:.6f} across all {n_full} funds — the "
            f"two views of the same money have diverged."
        )
        checked += 1

    assert checked > 0, f"{label}/{entity}: no full-snapshot months to check"


def test_aggregate_matches_per_fund_when_all_funds_touched(scenario):
    """When the waterfall touches every held fund, personal aggregate ==
    sum of per-fund detail. Regression guard for issue #3."""
    label, state = scenario
    p_rows, _, _, _ = _run(state)
    _assert_aggregate_matches_per_fund(label, p_rows, "personal")


def test_aggregate_matches_per_fund_huf(scenario):
    """Issue #4 — the same invariant for the HUF entity.

    Expected to fail: the HUF aggregate buckets grow at a blended per-category
    rate while the per-fund buckets grow at each fund's own CAGR, so the two
    views drift apart between annual syncs. Identical in shape to issue #3,
    which was fixed for the personal side only.

    Registered in conftest.KNOWN_BUGS as xfail(strict=True) — fixing the engine
    makes this pass unexpectedly and breaks the build until the entry is
    removed, which is the intended signal.
    """
    label, state = scenario
    _, _, h_rows, _ = _run(state)
    _assert_aggregate_matches_per_fund(label, h_rows, "huf")


def test_no_cash_moves_from_personal_to_the_huf(scenario):
    """
    Phase 3 removed the transfer: the tax saving stays in the personal
    corpus. `huf_transfer_in` must therefore be zero in every month.

    This replaces test_transfer_recorded_equals_transfer_reported, which
    reconciled the transfer against tax_saved. With nothing transferred that
    test passed vacuously -- it skipped every row on `moved <= 1e-9`. A test
    that passes because it examines nothing is a hole, not a guard.
    """
    label, state = scenario
    p_rows, _yearly, _h, _hy = _run(state)

    moved = [(r.fy_year, r.huf_transfer_in) for r in p_rows
             if abs(r.huf_transfer_in) > 1e-9]
    assert not moved, (
        f"{label}: {len(moved)} month(s) moved cash from personal into the "
        f"HUF. Phase 3 removed the transfer; the saving stays where it is. "
        f"First few: {moved[:5]}"
    )


def test_no_entity_holds_negative_corpus(scenario):
    """Debiting tax and transfers must not push either entity below zero."""
    label, state = scenario
    _, yearly, _, _ = _run(state)
    for r in yearly:
        assert _personal(r) >= -1e-6, f"{label} FY{r.year}: personal {_personal(r):.6f}"
        assert _huf(r) >= -1e-6, f"{label} FY{r.year}: HUF {_huf(r):.6f}"
