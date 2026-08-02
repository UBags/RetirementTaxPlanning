"""Every rupee that leaves a purse is cash drawn, tax paid, or exit load.

Anything else is a leak. Five have now been found by checking exactly this,
and every one of them shipped green because the fixtures in place at the time
could not see value vanishing -- they checked that the reported numbers were
self-consistent, which they always were.

The identity, per financial year:

    value_open - cash_p - cash_h - tax - exit_load == value_close

`value_open` is taken after growth and inflows and before anything is sold;
`value_close` after the year is committed. `exit_load` rides on each Fund and
is carried through clone(), so close-minus-open is the load that really
happened on the winning lineage rather than the load charged across the
hundreds of candidate clones the grid searches discard.

What each case here would have caught, had it existed:

    Sleeve.invest dropping the remainder        FY7,  10.36 destroyed
    the s.112A bill never debited               FY34, 1.93 and FY35, 2.36
    a bill charged to an exhausted book         FY31, 0.369
    the water-fill drawing past `need`          spread across the late years

Run: pytest tests/test_swp_conservation.py -q
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import swp_dp_model as m  # noqa: E402,I001


# (scenario, horizon) across the shipped defaults, plus the parameter sets that
# actually exercise the sleeve. The defaults alone are NOT sufficient coverage:
# none of them drives enough surplus into equity to reach the paths where four
# of the five leaks lived, which is precisely why those leaks survived.
DEFAULT_CASES = [(sc, hz) for sc in ("1chunk", "2chunk", "3chunk")
                 for hz in (30, 35)]


# The whole parameter set, not a few globals. A partial override does NOT
# reach the leak paths: an earlier draft of this file set only the rates and
# the floor, left the 350 corpus and the 35-52.5 requirement ladder alone, and
# passed against a deliberately re-broken Sleeve.invest. bucket_target is a
# share of the excess over the debt floor, so it is the CORPUS and the
# REQUIREMENT together that decide whether a deployment can overflow a cycle.
# Mutation-tested: re-break any of the five fixed leaks and this suite fails.
WORKING_SET = dict(
    EQUITY_RETURN=0.125,
    INTEREST_RATES=(0.06, 0.0525),
    EQUITY_MAX_SHARE=0.125,
    DEBT_FLOOR_MULTIPLES=(20.0, 20.0, 18.0),
    PERSONAL_INFLOWS=((500.0, 1),),
    HUF_INFLOWS=((190.0, 5),),
    REQUIREMENT_SCHEDULE=((40.0, 1), (45.0, 7), (50.0, 13), (55.0, 19),
                          (57.5, 25), (60.0, 31)),
    TAXABLE_OTHER_INCOME=0.0,
    TAXFREE_OTHER_INCOME=4.7,
    SALARY_INCOME=1.35,
    RENTAL_INCOME=1.8,
)


def _residuals(**overrides):
    """Run one simulation and return its per-year conservation residuals.

    The leading-underscore keys are run controls, not module globals, so they
    come out BEFORE anything is saved or patched -- an earlier version of this
    helper tried to getattr("_horizon") off the module and would have raised.
    """
    scenario = overrides.pop("_scenario", "2chunk")
    horizon = overrides.pop("_horizon", 35)
    saved = {k: getattr(m, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(m, k, v)
        r = m.simulate(scenario, horizon, m.MAX_FOLIOS, vintage_every=5)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
    return r


def _check(r, label):
    """Two identities, not one.

    CONSERVATION says nothing left the plan except cash, tax and load. It is
    blind to a year that was never FUNDED, because raising no cash conserves
    perfectly -- which is exactly how the plan came to stop paying the
    requirement from FY42 at a 50-year horizon while its corpus climbed. So
    funding is asserted separately.
    """
    rows = [(row["fy"], row["residual"]) for row in r["rows"]]
    unfunded = [(row["fy"],
                 row["net_need"] - row["personal_cash"] - row["huf_cash"])
                for row in r["rows"]
                if abs(row["net_need"] - row["personal_cash"]
                       - row["huf_cash"]) > m.PLAN_TOL]
    assert not unfunded, (
        f"{label}: the requirement went unfunded in {len(unfunded)} year(s), "
        f"first FY{unfunded[0][0]} short by {unfunded[0][1]:.3f}. A year that "
        f"raises no cash still conserves, so this is invisible to the "
        f"residual."
    )
    assert r["shortfall"] <= m.PLAN_TOL, (
        f"{label}: reported shortfall {r['shortfall']:.3f}"
    )
    bad = [(fy, res) for fy, res in rows if abs(res) > m.PLAN_TOL]
    assert not bad, (
        f"{label}: conservation broken in {len(bad)} year(s), worst "
        f"FY{max(bad, key=lambda t: abs(t[1]))[0]} at "
        f"{max(abs(r) for _f, r in bad):.6f} against PLAN_TOL "
        f"{m.PLAN_TOL}. Value left the plan as something other than cash "
        f"drawn, tax paid or exit load."
    )


@pytest.mark.parametrize("scenario,horizon", DEFAULT_CASES)
def test_conservation_holds_on_the_defaults(scenario, horizon):
    _check(m.simulate(scenario, horizon, m.MAX_FOLIOS, vintage_every=5),
           f"{scenario}/{horizon}")


def test_conservation_holds_with_a_large_equity_sleeve():
    """The case the defaults cannot reach.

    A 12.5% sleeve against 6.00%/5.25% debt drives the equity share to 64% by
    FY35 and puts real money through Sleeve.invest, the s.112A settlement and
    the exhausted-book fallback -- the three paths that hid leaks.
    """
    _check(_residuals(**WORKING_SET), "large sleeve")


def test_conservation_holds_when_a_purse_is_exhausted():
    """A bill charged against an empty book must still leave the corpus.

    pay_tax_bill returns (0, 0) the moment book.value is zero, so before the
    sleeve fallback existed the HUF's slab tax at FY31 was reported and never
    paid. High other income drains the HUF hardest.
    """
    _check(_residuals(**dict(WORKING_SET, TAXABLE_OTHER_INCOME=12.0)),
           "exhausted purse")


def test_conservation_holds_with_a_much_larger_sleeve():
    """A third regime: 30% deployment cap against a 16/14/12 debt floor.

    KNOWN COVERAGE GAP, now with its reason rather than a shrug. Breaking the
    s.112A settlement in run_waterfill is caught; breaking it in run_year
    alone is NOT, by any fixture here. It is not that the path is unreachable
    -- probing shows run_year carrying an equity bill once or twice per run --
    but that it can only ever be a TINY one. run_year's own equity sales are
    gain-capped at eq_exempt, so its s.112A bill is zero by construction; the
    only gain that escapes the cap is what the exhausted-book fallback
    realises, and that is small. The resulting bill sits below PLAN_TOL, so no
    conservation fixture can distinguish its settlement from its absence.
    Covering it needs an assertion on the tax figure, not on the identity.
    """
    _check(_residuals(**dict(WORKING_SET, EQUITY_MAX_SHARE=0.30,
                             DEBT_FLOOR_MULTIPLES=(16.0, 14.0, 12.0))),
           "equity in the plain path")


def test_conservation_holds_when_the_sleeves_cannot_pay_the_equity_bill():
    """Drive the s.112A fallback onto the debt book.

    A 7% sleeve under a 10/9/8 floor with heavy taxable other income drains
    the equity almost to nothing in the late years, so settle_equity_tax runs
    out of units and the balance falls to pay_external_bill. That path used
    pay_tax_bill, which converges on the whole slab bill rather than on this
    one -- a residual of 0.0026 dragged 1.13 of debt out behind it. The
    conservation identity does NOT catch that on its own, because the
    over-draw is reported as tax and the identity still closes; this case is
    here so the path is at least exercised.
    """
    _check(_residuals(**dict(WORKING_SET, EQUITY_RETURN=0.07,
                             EQUITY_MAX_SHARE=0.30,
                             TAXABLE_OTHER_INCOME=18.0,
                             DEBT_FLOOR_MULTIPLES=(10.0, 9.0, 8.0))),
           "sleeve cannot pay")
    # The same shape at 60 years on the shipped income and corpus, where the
    # fallback fires with a bill large enough to matter.
    _check(_residuals(_horizon=60, EQUITY_RETURN=0.07,
                      TAXABLE_OTHER_INCOME=18.0,
                      DEBT_FLOOR_MULTIPLES=(10.0, 9.0, 8.0)),
           "sleeve cannot pay, 60 years")


def test_conservation_holds_at_a_long_horizon():
    """Fifty years, where the debt books empty and equity funds the plan.

    This is the case that closes the run_year coverage gap noted above. Once
    the last-resort draw exists, run_year realises UNCAPPED equity gain to
    fund the year, so its s.112A bill is no longer bounded by the annual
    exemption and no longer sits under PLAN_TOL.
    """
    _check(_residuals(_horizon=50, **WORKING_SET), "50 years, working set")
    # AND at the shipped defaults, which is a materially different plan: this
    # is the run where the PLAIN path, not the water-fill, commits s.112A
    # bills of several lakh. Mutation-tested -- removing run_year's settlement
    # is caught here and by nothing else.
    _check(_residuals(_horizon=50), "50 years, defaults")
    _check(_residuals(_horizon=60, _scenario="3chunk"), "60 years, 3chunk")


def test_conservation_holds_without_equity():
    """The control the module docstring mandates.

    With EQUITY_ENABLED off the surplus routing has nowhere to send the
    above-floor remainder, which is exactly where two earlier versions
    discarded it.
    """
    _check(_residuals(EQUITY_ENABLED=False), "no equity")


def test_pay_external_bill_draws_only_what_the_bill_needs():
    """A UNIT test, because no end-to-end fixture can catch this one.

    pay_external_bill exists because pay_tax_bill converges on the whole slab
    bill, which the caller has already settled, and so drains the book a
    second time. That over-draw is REPORTED AS TAX -- the caller adds the gain
    to gpt and recomputes txp -- so the yearly conservation identity closes
    perfectly and every scenario fixture passes. Mutation-tested: reverting
    the convergence rule is caught by nothing else in this file.

    The assertion is therefore on the CASH, not on an identity: settling a
    trivial bill against a book carrying a large standing slab liability must
    draw the bill and the incremental slab tax it creates, and nothing more.
    """
    book = m.Book(cap=1, n_funds=1)
    navs = [2.0]
    book.folios[0].funds[0].lots.append([500.0, 1.0, 0])   # 100% gain at nav 2
    bill = 0.01
    base_gain = 30.0            # the year has already realised a lot
    standing = m.personal_tax(2.0 + base_gain, 20.0, 8.0)
    assert standing > 1.0, "fixture is not exercising a standing slab bill"

    drawn, gain = m.pay_external_bill(book, bill, base_gain, navs, 1,
                                      exempt_ceiling=20.0, taxable_other=2.0,
                                      basic_exemption=8.0)
    assert drawn >= bill - m.EPS, f"under-drew: {drawn} against a bill of {bill}"
    assert drawn < bill + 1.0, (
        f"over-drew: {drawn:.4f} for a bill of {bill}. The standing slab bill "
        f"is {standing:.4f}; drawing that again is the pay_tax_bill defect."
    )


def test_exit_load_is_the_only_thing_that_leaves_unaccounted():
    """The residual is zero, not merely small.

    If this ever starts failing while the tests above pass, the load is being
    double-counted somewhere rather than value being lost.
    """
    r = m.simulate("3chunk", 35, m.MAX_FOLIOS, vintage_every=5)
    worst = max(abs(row["residual"]) for row in r["rows"])
    assert worst < 1e-9, f"residual {worst:.3e} is above float noise"
    assert sum(row["exit_load"] for row in r["rows"]) > 0.0, (
        "no exit load at all -- the ledger is not being fed, so the identity "
        "is closing for the wrong reason"
    )
