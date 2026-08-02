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
    """Run one simulation and return its per-year conservation residuals."""
    saved = {k: getattr(m, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(m, k, v)
        scenario = overrides.pop("_scenario", "2chunk")
        horizon = overrides.pop("_horizon", 35)
        r = m.simulate(scenario, horizon, m.MAX_FOLIOS, vintage_every=5)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
    return [(row["fy"], row["residual"]) for row in r["rows"]]


def _check(rows, label):
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
    r = m.simulate(scenario, horizon, m.MAX_FOLIOS, vintage_every=5)
    _check([(row["fy"], row["residual"]) for row in r["rows"]],
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

    KNOWN COVERAGE GAP, recorded rather than papered over. Breaking the
    s.112A settlement in run_waterfill is caught; breaking it in run_year
    alone is NOT, by any fixture here including this one, because in every
    regime tried the equity bill arises only in the water-fill branch. If a
    future change moves equity into the plain path's funding, this file needs
    a case that proves it.
    """
    _check(_residuals(**dict(WORKING_SET, EQUITY_MAX_SHARE=0.30,
                             DEBT_FLOOR_MULTIPLES=(16.0, 14.0, 12.0))),
           "equity in the plain path")


def test_conservation_holds_without_equity():
    """The control the module docstring mandates.

    With EQUITY_ENABLED off the surplus routing has nowhere to send the
    above-floor remainder, which is exactly where two earlier versions
    discarded it.
    """
    _check(_residuals(EQUITY_ENABLED=False), "no equity")


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
