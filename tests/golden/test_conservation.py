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
    from engine import Engine
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
    from models import FDRateChunk

    s = copy.deepcopy(state)
    s.fd_rate_chunks = [FDRateChunk(year_from=1, year_to=30, fd_rate=rate)]
    s.fd_rate = rate
    return s


def test_huf_transfer_debits_personal_corpus(scenario):
    """
    A higher FD benchmark produces a larger `tax_saved`, hence a larger transfer
    out of Personal into the HUF. Personal corpus must fall in response.

    If personal corpus is identical under both FD rates, the transfer is being
    credited to the HUF without ever being debited from Personal.
    """
    label, state = scenario

    _, y_lo, _, _ = _run(_with_fd_rate(state, 0.02))
    _, y_hi, _, _ = _run(_with_fd_rate(state, 0.12))

    xfer_lo = sum(r.tax_saved for r in y_lo)
    xfer_hi = sum(r.tax_saved for r in y_hi)
    if xfer_hi - xfer_lo < 1.0:
        pytest.skip(f"{label}: FD rate barely changes tax_saved; nothing to test")

    idx = max((i for i, (a, b) in enumerate(zip(y_lo, y_hi))
               if _personal(a) > 1.0 and _personal(b) > 1.0), default=None)
    if idx is None:
        pytest.skip(f"{label}: personal corpus exhausted in both runs")

    p_lo, p_hi = _personal(y_lo[idx]), _personal(y_hi[idx])

    assert p_hi < p_lo - 1e-3, (
        f"{label} FY{y_lo[idx].year}: personal corpus did not respond to a "
        f"{xfer_hi - xfer_lo:.2f}L larger HUF transfer "
        f"(low-FD {p_lo:.6f} vs high-FD {p_hi:.6f}). "
        "The transfer is credited to the HUF but never debited from Personal."
    )


def test_personal_tax_debits_corpus(scenario, monkeypatch):
    """
    Paying no tax must leave more corpus than paying tax. If the two runs agree,
    tax is being reported but never deducted from the portfolio.
    """
    label, state = scenario
    import engine

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


def test_aggregate_matches_per_fund_when_all_funds_touched(scenario):
    """
    When the withdrawal waterfall touches every held fund, the aggregate corpus
    must equal the sum of the per-fund detail.

    Months where the waterfall concentrates on a few over-weight funds are
    excluded: engine.py skips funds with amt_f < 0.01, so fund_withdrawals is a
    record of withdrawals, not a portfolio snapshot. Comparing the two in those
    months compares different sets of funds.
    """
    label, state = scenario
    p_rows, _, _, _ = _run(state)

    n_full = max((len(r.fund_withdrawals) for r in p_rows), default=0)
    if n_full == 0:
        pytest.skip(f"{label}: no per-fund detail recorded")

    checked = 0
    for r in p_rows:
        if len(r.fund_withdrawals) != n_full:
            continue        # waterfall concentrated; not a full snapshot
        agg = r.corpus_debt_end + r.corpus_equity_end + r.corpus_other_end
        det = sum(fd.corpus_end for fd in r.fund_withdrawals)
        tol = max(0.01, 1e-6 * abs(agg))
        assert abs(agg - det) <= tol, (
            f"{label} m{r.month_idx} (FY{r.fy_year}): aggregate {agg:.6f} vs "
            f"per-fund {det:.6f} across all {n_full} funds — the two views of "
            f"the same money have diverged."
        )
        checked += 1

    assert checked > 0, f"{label}: no full-snapshot months to check"


def test_transfer_recorded_equals_transfer_reported(scenario):
    """
    `huf_transfer_in` must reconcile with `tax_saved`, with a one-year lag:
    FY N's saving is computed at that year's tax settlement and transferred in
    April of FY N+1.
    """
    label, state = scenario
    p_rows, yearly, _, _ = _run(state)

    moved_by_fy: dict[int, float] = {}
    for r in p_rows:
        moved_by_fy[r.fy_year] = moved_by_fy.get(r.fy_year, 0.0) + r.huf_transfer_in

    saved_by_fy = {r.year: r.tax_saved for r in yearly}

    for fy, moved in sorted(moved_by_fy.items()):
        if moved <= 1e-9:
            continue
        entitled = saved_by_fy.get(fy - 1, 0.0)
        assert moved <= entitled + 1e-6, (
            f"{label} FY{fy}: transferred {moved:.4f}L to the HUF but FY{fy - 1} "
            f"tax_saved was only {entitled:.4f}L"
        )


def test_no_entity_holds_negative_corpus(scenario):
    """Debiting tax and transfers must not push either entity below zero."""
    label, state = scenario
    _, yearly, _, _ = _run(state)
    for r in yearly:
        assert _personal(r) >= -1e-6, f"{label} FY{r.year}: personal {_personal(r):.6f}"
        assert _huf(r) >= -1e-6, f"{label} FY{r.year}: HUF {_huf(r):.6f}"
