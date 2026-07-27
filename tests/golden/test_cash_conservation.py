# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""
Cash leaving the corpus must arrive somewhere.

The gap this fills
------------------
`test_conservation.py::test_monthly_corpus_flow_balances` asserts

    corpus_end == corpus_start - withdrawal

which balances the corpus against *itself*. It held to 5.7e-14 for years while
a rupee a year was being destroyed, because both outflows were recorded as
withdrawals and the identity closed perfectly. It never crosses the boundary
between the corpus and the user's hand -- which is exactly where the money was
going missing. Its docstring claims it catches "exactly how the tax and
HUF-transfer leaks manifested". It cannot.

The defect it could not see, fixed in Phase 3: `net_p` subtracted
`this_fy_saved`, so the solver withdrew an extra X to still deliver the
requirement, and the engine separately debited X as the HUF transfer. Departing
the personal corpus: `W + X`. Arriving: `W + OI - X` with the user plus `X` in
the HUF, so `W + OI`. X unaccounted every year, compounding to 114L over thirty
on s0_1chunk.

WHAT IS NOT HERE, AND WHY
-------------------------
The invariant that would have caught it directly is

    everything redeemed  ==  cash reaching the user
                           + tax paid
                           + transfers out

and it **cannot be written against MonthlyRow today**. Two terms are missing:

  * `topup_from_huf` -- cash the HUF sends to a depleted personal corpus. It is
    accumulated into `p_acc` and reaches YearSummary only folded into
    `swp_cash_personal`, so it cannot be separated from the withdrawal.
  * other income -- never touches the corpus, but is inside
    `net_cash_personal`, so it must be removed before the two sides compare.
    It is recoverable only by rearranging the very identity under test, which
    makes the check tautological.

Adding both to MonthlyRow is a small change and it is the prerequisite for the
real test. Until then the checks below cover what can be verified without
reaching into the engine's own accumulators to audit the engine's own
accumulators.

See HANDOVER §9.13 and HANDOVER_REVISION §7.3.
"""
from __future__ import annotations

import pytest

TOL = 1e-6          # Rs lakh. These are exact identities, not solver output.


def _run(state):
    from engine import Engine
    return Engine(state).run()


def _by_fy(rows):
    out: dict[int, list] = {}
    for r in rows:
        out.setdefault(r.fy_year, []).append(r)
    return out


def test_tax_debited_from_the_corpus_matches_the_tax_reported(scenario):
    """The April debit must equal the previous year's reported liability.

    This crosses the boundary the existing conservation test does not: monthly
    corpus flow on one side, yearly reporting on the other. A mismatch means
    the corpus was debited for an amount the summary never accounted for, or
    the reverse.

    Tax for FY n is settled in April of FY n+1, so the debit lags by one year.
    """
    label, state = scenario
    p_rows, p_yearly, _h, _hy = _run(state)

    paid_in = {fy: sum(getattr(r, "ind_tax_paid", 0.0) for r in rows)
               for fy, rows in _by_fy(p_rows).items()}
    owed_for = {y.year: y.tax_personal for y in p_yearly}

    bad = []
    for fy in sorted(paid_in):
        settled = paid_in[fy]
        liability = owed_for.get(fy - 1, 0.0)
        # A debit may be clamped by an exhausted corpus, never inflated.
        if settled > liability + TOL:
            bad.append((fy, settled, liability))

    assert not bad, (
        f"{label}: the corpus was debited more tax than was ever reported "
        f"owing, in {len(bad)} year(s).\n" + "\n".join(
            f"    FY{fy}: debited {s:.6f} but FY{fy - 1} liability was {o:.6f}"
            for fy, s, o in bad[:8])
    )


@pytest.mark.parametrize("entity", ["personal", "huf"])
def test_no_month_redeems_more_than_the_corpus_held(scenario, entity):
    """A withdrawal larger than the corpus means a shortfall path was skipped.

    Cheap, and it fails loudly rather than producing a negative corpus that
    compounds silently into every later year of the snapshot.
    """
    label, state = scenario
    p_rows, _py, h_rows, _hy = _run(state)
    rows = p_rows if entity == "personal" else h_rows

    bad = []
    for r in rows:
        start = (r.corpus_debt_start + r.corpus_equity_start
                 + r.corpus_other_start)
        wd = r.wd_debt + r.wd_equity + r.wd_other
        if wd > start + TOL:
            bad.append((r.month_idx, start, wd))

    assert not bad, (
        f"{label}/{entity}: {len(bad)} month(s) redeemed more than the corpus "
        f"held.\n" + "\n".join(f"    m{m}: corpus {s:.6f}, withdrew {w:.6f}"
                               for m, s, w in bad[:8])
    )


@pytest.mark.parametrize("entity", ["personal", "huf"])
def test_no_corpus_column_goes_negative(scenario, entity):
    """No individual asset class may be driven below zero.

    test_conservation.py checks the total. A single class can go negative while
    the total stays positive, and that is how a pro-rata redemption bug hides.
    """
    label, state = scenario
    p_rows, _py, h_rows, _hy = _run(state)
    rows = p_rows if entity == "personal" else h_rows

    bad = [(r.month_idx, r.corpus_debt_end, r.corpus_equity_end,
            r.corpus_other_end)
           for r in rows
           if min(r.corpus_debt_end, r.corpus_equity_end,
                  r.corpus_other_end) < -TOL]

    assert not bad, (
        f"{label}/{entity}: {len(bad)} month(s) hold a negative corpus "
        f"column.\n" + "\n".join(
            f"    m{m}: debt {d:.6f} equity {e:.6f} other {o:.6f}"
            for m, d, e, o in bad[:8])
    )
