# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""Conservation and invariant tests for scripts/swp_dp_model.py.

WHY THIS FILE EXISTS

The model is a research script, not production code, and it has twice shipped a
silent money leak: surplus that was neither invested in debt nor in equity, and
simply vanished. Both leaks survived every summary statistic -- tax, corpus and
the year-by-year table all looked entirely plausible -- and were caught only by
a control run by hand. One of them removed 685 of corpus and produced a
confident, completely wrong conclusion about a design question.

The lesson is the one already recorded in HANDOVER 7.10: a verification that
lives in a docstring is a verification nobody runs. These are the invariants
that must hold whatever the parameters, expressed as assertions.

COST: the three integration tests each run a full 35-year simulation, which
replays every year up to SPILL_GRID x EQ_GRID times. Budget roughly a minute
for the file. The two unit tests are instant.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import swp_dp_model as M  # noqa: E402

# The no-equity path is the reference the equity code must not disturb. These
# are the values the leaking versions FAILED to reproduce -- one returned
# 74.855 / 859.0, which is the entire signature of a discarded surplus.
#
# RE-PINNED when the default income split changed from 5.95/9.85 to 4.85/10.45.
# Total income fell 0.50 (so need rose and corpus fell) while the TAXABLE share
# fell 1.10 (so the band widened and tax fell). Previous pin: 1.499788298 /
# 1907.619583. If this test fails after an intentional default change, re-pin
# it deliberately; if it fails after a routing change, suspect a discarded
# surplus and check the corpus for a large DROP.
NO_EQUITY_TAX = 0.554240407
NO_EQUITY_CORPUS = 1839.576279


@pytest.fixture(autouse=True)
def _restore_constants():
    """Every test here mutates module globals. Put them back."""
    saved = {k: getattr(M, k) for k in
             ("EQUITY_ENABLED", "DEBT_FLOOR_X", "EQUITY_MAX_SHARE",
              "EQUITY_RETURN", "N_EQ_FUNDS", "EQ_BUCKET_SHARE")}
    yield
    for k, v in saved.items():
        setattr(M, k, v)


def test_surplus_is_never_discarded():
    """With equity off, surplus must ALL return to debt.

    This is the control that caught both leaks. If any routing change drops the
    above-floor remainder, corpus collapses by hundreds and this fails loudly.
    Run it after touching anything in the surplus block.
    """
    M.EQUITY_ENABLED = False
    r = M.simulate("1chunk", 35, 8, vintage_every=5)
    assert r["tax"] == pytest.approx(NO_EQUITY_TAX, abs=1e-6), (
        f"no-equity lifetime tax moved to {r['tax']:.6f}. If corpus also fell "
        f"sharply, surplus is being discarded somewhere in the routing block."
    )
    assert r["corpus"] == pytest.approx(NO_EQUITY_CORPUS, abs=1e-3), (
        f"no-equity corpus moved to {r['corpus']:.4f} against "
        f"{NO_EQUITY_CORPUS}. A large DROP means money is vanishing, not that "
        f"the strategy changed."
    )


@pytest.mark.parametrize("scenario,horizon", [
    ("1chunk", 30), ("1chunk", 35), ("3chunk", 35),
])
def test_cash_raised_equals_requirement(scenario, horizon):
    """Every year's net requirement must be funded exactly -- no more, no less.

    Over-raising would flatter the corpus; under-raising would mean the plan
    silently fails to pay for the retirement it is modelling.
    """
    r = M.simulate(scenario, horizon, 8, vintage_every=5)
    raised = sum(x["personal_cash"] + x["huf_cash"] for x in r["rows"])
    required = sum(x["net_need"] for x in r["rows"])
    assert raised == pytest.approx(required, abs=1e-2), (
        f"{scenario}/{horizon}y raised {raised:.4f} against a requirement of "
        f"{required:.4f}"
    )


@pytest.mark.parametrize("scenario,horizon", [("1chunk", 35), ("3chunk", 35)])
def test_equity_gain_never_exceeds_the_exemption(scenario, horizon):
    """The equity harvest must stop at the s.112A limit, not run past it.

    Two assessees, so the annual ceiling is twice the per-assessee exemption.
    Overshooting would be a real tax bill the model reports as free.
    """
    r = M.simulate(scenario, horizon, 8, vintage_every=5)
    lad = M.build_ladders(horizon)
    for row in r["rows"]:
        ceiling = 2.0 * lad["eq_exempt"][row["fy"] - 1]
        assert row["equity_gain"] <= ceiling + 1e-6, (
            f"FY{row['fy']}: realised equity gain {row['equity_gain']:.4f} "
            f"exceeds the two-assessee ceiling of {ceiling:.2f}"
        )
        if row["equity_gain"] <= ceiling + 1e-9:
            assert row["tax_equity"] == pytest.approx(0.0, abs=1e-9), (
                f"FY{row['fy']}: equity tax {row['tax_equity']:.4f} charged on "
                f"gain that is inside the exemption"
            )


def test_equity_is_not_sold_inside_the_holding_period():
    """s.2(42A): listed equity is long term only after twelve months.

    A lot bought this year must be untouchable this year, and available next.
    """
    sl = M.Sleeve()
    sl.invest(100.0, 1.0, 5, bucket_target=100.0)

    gross, gain = sl.sell(2.0, 5, amount=50.0)      # same FY as purchase
    assert gross == pytest.approx(0.0), (
        f"sold {gross:.4f} of equity in its year of purchase"
    )
    assert sl.value(2.0) == pytest.approx(200.0)

    gross, gain = sl.sell(2.0, 6, amount=50.0)      # the following FY
    assert gross == pytest.approx(50.0)
    assert gain == pytest.approx(25.0)               # basis 1.0, nav 2.0


def test_harvest_preserves_value_and_reduces_embedded_gain():
    """Sell and rebuy at one NAV: value unchanged, basis stepped up.

    This is the whole point of the harvest. If it ever moves value, the sleeve
    is leaking; if it does not cut embedded gain, it is doing nothing.
    """
    sl = M.Sleeve()
    sl.invest(100.0, 1.0, 5, bucket_target=100.0)

    before_value = sl.value(2.0)
    before_gain = before_value - sl.cost()
    assert before_gain == pytest.approx(100.0)

    _gross, gain = sl.harvest(2.0, 6, gain_budget=30.0, bucket_target=100.0)

    assert gain == pytest.approx(30.0, abs=1e-6), (
        f"harvest realised {gain:.4f} against a budget of 30.0"
    )
    assert sl.value(2.0) == pytest.approx(before_value, abs=1e-6), (
        "harvest changed the value of the sleeve; it must be value-neutral"
    )
    after_gain = sl.value(2.0) - sl.cost()
    assert after_gain == pytest.approx(before_gain - 30.0, abs=1e-6), (
        f"embedded gain went {before_gain:.4f} -> {after_gain:.4f}; the "
        f"harvest should have cut it by exactly the 30.0 it realised"
    )
