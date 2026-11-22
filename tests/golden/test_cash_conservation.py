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

THE IDENTITY
------------
Everything redeemed from the personal corpus in a financial year goes to
exactly three places -- the user, the tax authority, or the HUF:

    sum(monthly wd)  ==  cash reaching the user
                       + sum(monthly ind_tax_paid)
                       + sum(monthly huf_transfer_in)

and the cash reaching the user is what net_cash_personal reports, less the two
components that never came out of this corpus:

    cash reaching the user  ==  net_cash_personal
                              - other_income_personal
                              - topup_from_huf

Those last two fields were added to YearSummary for this test. Until then the
identity could not be written at all: the top-up reached the summary only
folded inside swp_cash_personal, and other income was recoverable only by
rearranging the very identity under test, which would have made the check
tautological.

The left-hand side is rebuilt from the MONTHLY rows and the right-hand side
from the YEARLY summary, deliberately, so a reporting error in one cannot hide
inside the check meant to find it. That is exactly how the double debit
survived for so long: test_monthly_corpus_flow_balances compared the corpus
with itself and never crossed to the other side.

See HANDOVER §9.13 and HANDOVER_REVISION §7.3.
"""
from __future__ import annotations

import pytest

TOL = 1e-6          # Rs lakh. These are exact identities, not solver output.


def _run(state):
    from swp_planner.core.engine import Engine
    return Engine(state).run()


def _by_fy(rows):
    out: dict[int, list] = {}
    for r in rows:
        out.setdefault(r.fy_year, []).append(r)
    return out


def test_every_redemption_is_reported_as_a_withdrawal(scenario):
    """CASH BASIS. Monthly redemptions must sum to the yearly figure.

        sum(monthly wd)  ==  swp_cash_personal - topup_from_huf

    MEASURED, not assumed: these agree to six decimals on all five scenarios.
    An earlier version of this test added the April tax debit on the right and
    failed by exactly -tax in every scenario, which is how we learned that the
    TAX REDEMPTION IS ALREADY INSIDE wd_*. It is not a separate outflow on top
    of the withdrawal; it is part of it.

    That answers a question left open when these tests were first written --
    whether tax was double-debited the way the HUF transfer was. It is not. The
    redemption sits inside wd, net_cash_personal subtracts the liability once,
    and conservation holds. The transfer was the only defect of that shape.

    Transfers are likewise inside wd, though that cannot be verified from the
    current fixtures because Phase 3 drove huf_transfer_in to zero everywhere.
    If a transfer ever returns and is NOT inside wd, this test fails -- which is
    the right outcome, since it would mean a new outflow path exists.

    This check is deliberately weak on its own: both sides come from the same
    accumulators, so it verifies monthly/yearly consistency and little else.
    The load-bearing guard is test_reported_cash_equals_its_own_components
    below.
    """
    label, state = scenario
    p_rows, p_yearly, _h, _hy = _run(state)
    monthly = _by_fy(p_rows)

    bad = []
    for y in p_yearly:
        rows = monthly.get(y.year, [])
        if not rows:
            continue
        withdrawn = sum(r.wd_debt + r.wd_equity + r.wd_other for r in rows)
        reported = y.swp_cash_personal - y.topup_from_huf
        if abs(withdrawn - reported) > TOL:
            bad.append((y.year, withdrawn, reported, withdrawn - reported))

    assert not bad, (
        f"{label}: monthly redemptions do not sum to the yearly figure in"
        f" {len(bad)} year(s). Either a redemption path is not being recorded"
        f" on the monthly rows, or swp_cash_personal has acquired a term that"
        f" is not a withdrawal.\n" + "\n".join(
            f"    FY{fy}: monthly {w:.6f} vs yearly {r:.6f} ({d:+.6f})"
            for fy, w, r, d in bad[:8])
        + ("\n    ..." if len(bad) > 8 else ""))


def test_reported_cash_equals_its_own_components(scenario):
    """ACCRUAL BASIS. This is the one that catches a double debit.

        net_cash_personal  ==  swp_cash_personal
                             + other_income_personal
                             - tax_personal

    The Phase 3 defect violated exactly this. net_p carried an extra
    `- this_fy_saved`, so the reported cash was short by the HUF transfer while
    the solver had already withdrawn enough to cover it — the corpus lost twice
    what anyone received, and 114L compounded away over thirty years.

    Reconstructing the total from its parts is the only check that sees it.
    test_conservation.py::test_monthly_corpus_flow_balances cannot: it compares
    the corpus with itself, and both outflows were booked as withdrawals, so its
    residual sat at 5.7e-14 the whole time.
    """
    label, state = scenario
    _pm, p_yearly, _hm, _hy = _run(state)

    bad = []
    for y in p_yearly:
        expect = (y.swp_cash_personal + y.other_income_personal
                  - y.tax_personal)
        if abs(y.net_cash_personal - expect) > TOL:
            bad.append((y.year, y.net_cash_personal, expect,
                        y.net_cash_personal - expect))

    assert not bad, (
        f"{label}: reported cash does not equal its parts in {len(bad)}"
        f" year(s). A term is being added or subtracted that is not one of"
        f" withdrawal, other income or tax.\n" + "\n".join(
            f"    FY{fy}: reported {n:.6f} vs parts {e:.6f} ({d:+.6f})"
            for fy, n, e, d in bad[:8])
        + ("\n    ..." if len(bad) > 8 else ""))


def test_nothing_is_created_or_destroyed_over_the_whole_plan(scenario):
    """The same identity in aggregate, as a backstop.

    A per-year check can be defeated by an error that alternates sign across
    years and nets out -- which is exactly what an earlier version of this file
    did, mixing a cash-basis outflow with an accrual figure and producing
    +5.596 in FY9 against -5.596 in FY10 on s3_4chunk. Summing first cannot be
    fooled that way.
    """
    label, state = scenario
    p_rows, p_yearly, _h, _hy = _run(state)

    withdrawn = sum(r.wd_debt + r.wd_equity + r.wd_other for r in p_rows)
    reported = sum(y.swp_cash_personal - y.topup_from_huf for y in p_yearly)

    assert abs(withdrawn - reported) <= TOL * max(1, len(p_yearly)), (
        f"{label}: over the plan {withdrawn:.6f}L was redeemed monthly but"
        f" {reported:.6f}L is reported yearly. Residual"
        f" {withdrawn - reported:+.6f}L.")


def test_the_new_summary_fields_are_actually_populated(scenario):
    """Guard against the new fields silently defaulting to zero.

    Both default to 0.0 so that adding them moved no snapshot. That leaves a
    future refactor free to stop populating them, at which point the two tests
    above would pass vacuously -- subtracting nothing, comparing a smaller
    number against a smaller number. This makes that loud.
    """
    label, state = scenario
    _pm, p_yearly, _hm, _hy = _run(state)

    assert any(y.other_income_personal > TOL for y in p_yearly), (
        f"{label}: other_income_personal is zero in every year. Either this"
        f" scenario genuinely has no pension, rent or interest, or the field"
        f" has stopped being populated.")

    # swp_cash_personal is ann_wd_p + ann_topup_p, so the top-up cannot exceed
    # it. A violation means the two have drifted apart.
    bad = [(y.year, y.topup_from_huf, y.swp_cash_personal) for y in p_yearly
           if y.topup_from_huf > y.swp_cash_personal + TOL]
    assert not bad, (
        f"{label}: topup_from_huf exceeds swp_cash_personal, which contains it"
        f" by construction: {bad[:5]}")


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
