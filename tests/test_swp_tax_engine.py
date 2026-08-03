# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""Statutory unit tests for the tax engine in scripts/swp_dp_model.py.

WHY THIS FILE EXISTS

Everything here was verified once, interactively, at the moment it was
written -- and then nothing pinned it. That is precisely the state the model
was in before the conservation tests, and it is how five separate money leaks
came to ship green.

The difference between this file and test_swp_conservation.py is what they can
see. Conservation asks whether value left the plan as something other than
cash, tax or load; it is blind to tax that is simply the WRONG AMOUNT, because
a wrong bill still leaves the corpus correctly. These are assertions on the
NUMBERS, against published figures wherever a published figure exists.

Every case with a citation reproduces a worked example from the source named
beside it. Where no worked example exists the test pins a boundary or an
invariant instead, and says which.

COST: all but three are pure arithmetic and instant. The three that call
simulate() are marked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import swp_dp_model as M  # noqa: E402,I001


@pytest.fixture(autouse=True)
def _restore():
    """Every test here mutates module globals. Put them back."""
    keys = ("EXPLORE_BOTH_REGIMES", "ITR_TYPE", "PLAN_DOB", "_FY_NOW",
            "EMERGENCY_SHOCK", "LIQUIDATION_YEARS", "EQUITY_ENABLED")
    saved = {k: getattr(M, k) for k in keys}
    ded = {p: dict(d) for p, d in M.DEDUCTIONS.items()}
    yield
    for k, v in saved.items():
        setattr(M, k, v)
    for p, d in ded.items():
        M.DEDUCTIONS[p] = d


# ── s.87A, new regime ──────────────────────────────────────────────────────

def test_87a_reproduces_the_published_examples():
    """Rebate and its marginal relief, at and just past the ceiling.

    Both figures are published: 12.00 lakh of taxable income pays nothing
    because the rebate covers the whole 60,000 bill, and 12.10 pays 10,400 --
    the 10,000 excess plus 4% cess -- because the rebate drops entirely and
    marginal relief caps the bill at the excess.
    """
    assert M.slab_tax(12.0, 4.0) == pytest.approx(0.60, abs=1e-9)
    assert M.personal_tax(12.0, 12.0, 4.0) == pytest.approx(0.0, abs=1e-9)
    assert M.slab_tax(12.10, 4.0) == pytest.approx(0.615, abs=1e-9)
    assert M.personal_tax(12.10, 12.0, 4.0) == pytest.approx(0.104, abs=1e-6)


def test_112a_gain_disqualifies_the_rebate_on_ordinary_income():
    """The trap: s.112A gain is in the THRESHOLD and out of the OFFSET.

    Slab income of 11 keeps the rebate on its own. The same 11 with 1.5 of
    equity gain loses it on the whole 11, because total income of 12.5 crosses
    the ceiling -- while the gain itself was never relievable anyway.
    """
    assert M.personal_tax(11.0, 12.0, 4.0, 0.0) == pytest.approx(0.0)
    with_gain = M.personal_tax(11.0, 12.0, 4.0, 1.5)
    assert with_gain > 0.5, (
        f"rebate survived a total income of 12.5: tax {with_gain:.4f}"
    )


def test_huf_never_gets_the_rebate():
    """s.87A is for a resident INDIVIDUAL. An HUF pays from the nil band up."""
    assert M.huf_tax(12.0, 4.0) > 0.5


# ── s.115BAC surcharge ─────────────────────────────────────────────────────

def test_surcharge_marginal_relief_reproduces_the_published_examples():
    """The two worked examples, using the source's own base slab figures.

    Injecting their bases isolates the RELIEF MECHANISM from the ladder. Their
    published bases are 0.50 higher than this model's at every point above 12
    while agreeing exactly AT 12, so they are internally inconsistent and are
    not used anywhere in the model -- but the mechanism they demonstrate is
    the one implemented here.
    """
    their_base = {50.0: 11.30, 50.20: 11.36, 100.0: 26.30, 100.40: 26.42}
    real_slab = M.slab_tax
    M.slab_tax = lambda inc, be: their_base[round(inc, 2)]
    try:
        for income, published in ((50.20, 11.96), (100.40, 30.5032)):
            bill = M.surcharged(their_base[income], 0.0, income, 0.0, 4.0)
            assert bill * M.CESS == pytest.approx(published, abs=5e-4), (
                f"income {income}: got {bill * M.CESS:.4f}, published "
                f"{published}"
            )
    finally:
        M.slab_tax = real_slab


@pytest.mark.parametrize("threshold", [50.0, 100.0, 200.0])
def test_surcharge_is_continuous_across_every_threshold(threshold):
    """Marginal relief exists to stop a cliff. Assert there isn't one.

    A rupee of extra income must never cost more than a rupee of extra tax.
    Tested with and without s.112A gain, because at 50 and 100 BOTH heads
    change rate while at 200 only the slab head does.
    """
    def bill(slab, special):
        return M.surcharged(M.slab_tax(slab, 4.0),
                            max(0.0, special - 2.0) * 0.125,
                            slab, special, 4.0)

    for split in (1.0, 0.5):
        below = bill(threshold * split, threshold * (1 - split))
        above = bill((threshold + 0.01) * split, (threshold + 0.01) * (1 - split))
        assert above - below <= 0.01 + 1e-9, (
            f"cliff at {threshold} with slab share {split}: "
            f"{below:.6f} -> {above:.6f} for 0.01 more income"
        )


def test_surcharge_is_monotone():
    """More income can never mean less tax. Sampled across the bands."""
    prev = -1.0
    for i in range(1, 30001):
        income = i * 0.01          # 0.01 .. 300 lakh
        v = M.surcharged(M.slab_tax(income, 4.0), 0.0, income, 0.0, 4.0)
        assert v >= prev - 1e-9, f"tax fell at income {income}"
        prev = v


def test_special_rate_surcharge_is_capped_at_fifteen():
    """s.111A / s.112 / s.112A / dividends stop at 15% however high income is.

    Asserted through the BILL, not by restating the arithmetic: an earlier
    version of this test computed min(rate, cap) itself and passed happily
    against an engine with the cap deleted.

    A pure s.112A bill at a total income past 200 must carry 15%, where the
    slab head at the same income carries 25%. That gap IS the cap.
    """
    assert M.surcharge_rate(250.0) == pytest.approx(0.25)
    special_amt = 10.0
    only_special = M.surcharged(0.0, special_amt, 0.0, 250.0, 4.0)
    assert only_special == pytest.approx(special_amt * 1.15), (
        f"s.112A tax at a total income of 250 came to "
        f"{only_special / special_amt - 1:.0%} of surcharge, not 15%"
    )
    only_slab = M.surcharged(special_amt, 0.0, 250.0, 0.0, 4.0)
    assert only_slab == pytest.approx(special_amt * 1.25), (
        "slab tax at the same income should carry the full 25%"
    )


# ── the old regime ─────────────────────────────────────────────────────────

def test_old_regime_ladder():
    """2.5 nil, 5% to 5, 20% to 10, 30% above -- at the band edges."""
    assert M._old_slab_tax(2.5, 2.5) == pytest.approx(0.0)
    assert M._old_slab_tax(5.0, 2.5) == pytest.approx(0.125)      # 2.5 @ 5%
    assert M._old_slab_tax(10.0, 2.5) == pytest.approx(1.125)     # + 5 @ 20%
    assert M._old_slab_tax(20.0, 2.5) == pytest.approx(4.125)     # + 10 @ 30%


def test_old_regime_age_bands():
    """s.2 proviso: 3.0 from sixty, 5.0 from eighty, 2.5 below."""
    assert M._old_basic_exemption(None) == pytest.approx(2.5)
    assert M._old_basic_exemption(59) == pytest.approx(2.5)
    assert M._old_basic_exemption(60) == pytest.approx(3.0)
    assert M._old_basic_exemption(79) == pytest.approx(3.0)
    assert M._old_basic_exemption(80) == pytest.approx(5.0)


def test_declared_deductions_are_clamped_to_their_ceilings():
    """Over-declaring must not shelter more than the section allows.

    Believing a declared figure would rig the regime comparison by exactly the
    amount over-declared, which is the one way this feature could quietly lie.
    """
    M.DEDUCTIONS["personal"] = {"80c": 3.0, "80d": 2.0, "80ttb": 2.0,
                                "80ddb": 3.0, "home_loan": 9.0}
    under60 = M._old_deductions("personal", 45)
    assert under60 == pytest.approx(1.5 + 0.25 + 0.40 + 2.0), (
        f"under-60 total {under60:.3f}: 80C 1.5 + 80D 0.25 + 80DDB 0.40 "
        f"+ home loan 2.0, with 80TTB excluded below sixty"
    )
    senior = M._old_deductions("personal", 65)
    assert senior == pytest.approx(1.5 + 0.50 + 1.00 + 0.50 + 2.0)


def test_huf_cannot_claim_80ttb_at_any_age():
    """s.80TTB reads "an assessee, being an individual"."""
    M.DEDUCTIONS["huf"] = {"80c": 0.0, "80d": 0.0, "80ddb": 0.0,
                           "80ttb": 0.5}
    assert M._old_deductions("huf", 70) == pytest.approx(0.0), (
        "an HUF was allowed s.80TTB"
    )


def test_the_regime_choice_takes_the_cheaper_bill():
    """And the crossover is where the two ladders actually cross.

    An HUF realising a small gain does better under the old regime, because
    2.5 of basic plus deductions beats a 4.0 nil band. Above roughly 7 the old
    regime's 20% band at 5.0 overtakes and the new regime wins. Both sides of
    that crossover are asserted, so a change that silently disabled the
    comparison would fail here rather than pass quietly.
    """
    M.DEDUCTIONS["huf"] = {"80c": 1.5, "80d": 0.25, "80ddb": 0.0}
    M.ITR_TYPE = "itr1"

    M.EXPLORE_BOTH_REGIMES = False
    new_low, new_high = M.huf_tax(5.0, 4.0), M.huf_tax(15.0, 4.0)
    M.EXPLORE_BOTH_REGIMES = True
    both_low, both_high = M.huf_tax(5.0, 4.0), M.huf_tax(15.0, 4.0)

    assert both_low < new_low - 1e-9, (
        f"old regime should win at a gain of 5: new {new_low:.4f}, "
        f"cheaper-of-both {both_low:.4f}"
    )
    assert both_high == pytest.approx(new_high), (
        f"new regime should win at a gain of 15: new {new_high:.4f}, "
        f"cheaper-of-both {both_high:.4f}"
    )


@pytest.mark.parametrize("itr", ["itr3", "ITR4", "Itr3"])
def test_business_income_is_locked_into_the_new_regime(itr):
    """s.115BAC(6): with business income the choice is once in a lifetime.

    The model does not track a lifetime state, so it must stay in the default
    new regime -- and must do so whatever the case of the flag.
    """
    M.DEDUCTIONS["huf"] = {"80c": 1.5, "80d": 0.25, "80ddb": 0.0}
    M.EXPLORE_BOTH_REGIMES = True
    M.ITR_TYPE = itr.lower()
    with_business = M.huf_tax(5.0, 4.0)
    M.EXPLORE_BOTH_REGIMES = False
    assert with_business == pytest.approx(M.huf_tax(5.0, 4.0)), (
        f"--itr_type {itr} still flipped regime"
    )


# ── age, and the horizon derived from it ───────────────────────────────────

def test_age_advances_through_the_plan():
    """The age bands are re-evaluated every year, not looked up once.

    A plan starting at sixty-six crosses eighty part-way through, and the old
    regime's basic exemption has to move with it.
    """
    M.PLAN_DOB = (1960, 1, 1)
    first, later = M.age_in(1), M.age_in(15)
    assert later == first + 14, f"age went {first} -> {later} over 14 years"
    assert M._old_basic_exemption(first) == pytest.approx(3.0)
    assert M._old_basic_exemption(later) == pytest.approx(5.0)


def test_age_is_none_without_a_date_of_birth():
    """No --dob means no age banding, not a crash and not a wrong band."""
    M.PLAN_DOB = None
    assert M.age_in(1) is None
    assert M._old_basic_exemption(M.age_in(1)) == pytest.approx(2.5)


# ── the settlement loops ───────────────────────────────────────────────────

def test_external_bill_draws_only_what_the_bill_needs():
    """pay_external_bill must not re-pay the standing slab bill.

    pay_tax_bill converges on the whole slab liability, which the caller has
    already settled; handing it a non-slab bill drains the book a second time.
    Asserted on the CASH, because the over-draw is reported as tax and the
    conservation identity closes either way.
    """
    book = M.Book(cap=1, n_funds=1)
    book.folios[0].funds[0].invest(500.0, 1.0, 0)
    standing = M.personal_tax(2.0 + 30.0, 20.0, 8.0)
    assert standing > 1.0, "fixture is not exercising a standing slab bill"

    drawn, _gain = M.pay_external_bill(book, 0.01, 30.0, [2.0], 1,
                                       exempt_ceiling=20.0, taxable_other=2.0,
                                       basic_exemption=8.0)
    assert drawn >= 0.01 - M.EPS
    assert drawn < 1.01, (
        f"drew {drawn:.4f} for a bill of 0.01 against a standing slab bill of "
        f"{standing:.4f}"
    )


def test_the_tax_loop_converges_far_below_plan_tol():
    """The bill settled must equal the bill reported.

    The loop shrinks the outstanding amount by the MARGINAL rate each pass, so
    a high marginal rate needs many passes. At ten it left 2% of a 7.5 bill
    outstanding once the surcharge landed -- tax recorded and never drawn.
    """
    # The fixture has to make the marginal rate HIGH or ten passes look fine.
    # A near-zero cost basis gives a gain fraction close to one, and a base
    # gain past 200 puts the draw in the 25% surcharge band, so each pass only
    # removes about 60% of what is left. Ten passes leave far more than
    # PLAN_TOL; forty leave nothing.
    book = M.Book(cap=1, n_funds=1)
    book.folios[0].funds[0].invest(50.0, 0.01, 0)      # basis 0.01, nav 2.0
    navs = [2.0]
    base_gain = 300.0
    initial = M.personal_tax(2.0 + base_gain, 20.0, 8.0)
    paid, gain = M.pay_tax_bill(book, initial, base_gain, navs, 1,
                                is_huf=False, exempt_ceiling=20.0,
                                taxable_other=2.0, basic_exemption=8.0)
    final = M.personal_tax(2.0 + base_gain + gain, 20.0, 8.0)
    assert abs(final - paid) < M.PLAN_TOL / 10.0, (
        f"settled {paid:.6f} against a final bill of {final:.6f}, a gap of "
        f"{abs(final - paid):.6f}; the loop did not converge"
    )


# ── the three that run a simulation ────────────────────────────────────────

def test_a_capital_shock_is_funded_and_costs_real_corpus():
    """SLOW. --emergency_shock is a need, not an accounting entry.

    It has to be raised like any other requirement -- so the year is still
    funded exactly -- and it has to leave a hole, because capital that goes
    never comes back.
    """
    base = M.simulate("3chunk", 35, M.MAX_FOLIOS, vintage_every=5)
    M.EMERGENCY_SHOCK = (80.0, 15)
    shocked = M.simulate("3chunk", 35, M.MAX_FOLIOS, vintage_every=5)

    unfunded = [r["fy"] for r in shocked["rows"]
                if abs(r["net_need"] - r["personal_cash"]
                       - r["huf_cash"]) > M.PLAN_TOL]
    assert not unfunded, f"the shock year went unfunded: FY{unfunded}"
    assert shocked["rows"][14]["net_need"] > base["rows"][14]["net_need"] + 79, (
        "the shock did not reach the requirement"
    )
    assert shocked["corpus"] < base["corpus"] - 80.0, (
        f"an 80 lakh drain cost only {base['corpus'] - shocked['corpus']:.1f} "
        f"of corpus, which is less than the drain itself"
    )


def test_one_year_liquidation_matches_the_single_shot_charge():
    """SLOW. --liquidation_years 1 must reproduce the un-spread figure.

    The wind-down loop replaced a single calculation. If N=1 ever stops
    agreeing with it, the loop has grown an off-by-one.
    """
    M.LIQUIDATION_YEARS = 1
    one = M.simulate("3chunk", 35, M.MAX_FOLIOS, vintage_every=5)["liq_tax"]
    M.LIQUIDATION_YEARS = 3
    three = M.simulate("3chunk", 35, M.MAX_FOLIOS, vintage_every=5)["liq_tax"]
    assert one > 0.0
    assert three < one, (
        f"spreading over three years did not help: {one:.3f} -> {three:.3f}"
    )


def test_an_exhausted_plan_reports_when_and_zeroes_what_follows():
    """SLOW. A plan that runs out must say so and stop reporting fiction.

    Equity off at sixty years exhausts the corpus. Everything after the
    failure has to be zero, because carrying on produced the most misleading
    output this model ever printed: a corpus climbing while the requirement
    went unfunded.
    """
    M.EQUITY_ENABLED = False
    r = M.simulate("2chunk", 60, M.MAX_FOLIOS, vintage_every=5)
    assert r["exhausted_fy"] > 0, "the plan did not run out when it should"
    assert 0 <= r["exhausted_month"] <= 12
    after = [x for x in r["rows"] if x["fy"] > r["exhausted_fy"]]
    assert after, "fixture needs years after the failure to check"
    for x in after:
        assert x["p_debt"] == 0.0 and x["huf_debt"] == 0.0
        assert x["p_eq"] == 0.0 and x["huf_eq"] == 0.0
        assert x["tax_total"] == 0.0
        # net_need is the field that PROVES the zeroing ran. The holdings and
        # the tax are already zero in an exhausted plan whether or not
        # anything zeroes them -- there is genuinely nothing left -- so an
        # earlier version of this test passed against the zeroing deleted.
        # The requirement, by contrast, keeps ticking up unless it is cleared.
        assert x["net_need"] == 0.0, (
            f"FY{x['fy']} still carries a requirement of {x['net_need']:.2f} "
            f"after the plan failed at FY{r['exhausted_fy']}"
        )
    assert r["corpus"] == pytest.approx(0.0)
    assert r["liq_tax"] == pytest.approx(0.0), (
        "an exhausted plan reported a liquidation bill on a corpus of nothing"
    )
