"""Tests for fund_filters.

THE TWO THAT MATTER are test_a_sponsor_filter_would_not_have_caught_dhfl and
test_the_monitor_does_not_claim_to_predict. Both pin NEGATIVE results that were
measured, and both exist because the natural next edit is to quietly start
believing this package screens credit risk. It does not, and these say so in a
form that fails.
"""
import pytest

from swp_planner.fund_filters import amc_registry as A
from swp_planner.fund_filters import deviation as D


def month_ends(n, start=2005):
    out, y, m = [], start, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-28")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def flat_fund(me, monthly, category="Liquid Fund", amc="x", dip=None,
              seed=0):
    """
    PEERS MUST VARY. Identical comparators have a MAD of zero, and robust_z
    then correctly reports NO VIEW rather than a huge z -- which is right, and
    which made three of these tests fail until the fixture was fixed rather
    than the code.
    """
    import random
    rng = random.Random(seed)
    nav, cur = {}, 100.0
    for i, m in enumerate(me):
        r = monthly if dip is None or i < dip else monthly * 0.2
        cur *= (1 + r + rng.gauss(0, monthly * 0.05))
        nav[m] = cur
    return dict(nav=nav, category=category, amc=amc)


# ── the registry ────────────────────────────────────────────────────────────

def test_a_sponsor_filter_would_not_have_caught_dhfl():
    """
    MEASURED, AND THE REASON THIS PACKAGE MUST NOT BE READ AS CREDIT
    PROTECTION. Every fund in nav_sample.csv that took a permanent writedown
    is run by Tata, UTI or LIC MF, and all three are admitted.
    """
    for name in ("Tata Treasury Advantage Fund", "Tata Money Market Fund",
                 "UTI Ultra Short Duration Fund", "UTI Low Duration Fund",
                 "LIC MF Low Duration Fund"):
        ok, why = A.admit(name)
        assert ok, f"{name} was refused: {why}"


def test_the_longest_matching_fragment_wins():
    """'ICICI Prudential' must not resolve on a bare 'prudential'."""
    assert A.identify("ICICI Prudential Savings Fund")[0] == "icici prudential"
    assert A.identify("PGIM India Liquid Fund")[0] == "pgim"


def test_an_unknown_amc_is_refused_rather_than_waved_through():
    ok, why = A.admit("Completely Unheard Of Capital Liquid Fund")
    assert not ok and "add it rather than assume" in why


def test_unknown_can_be_admitted_deliberately():
    ok, _ = A.admit("Unheard Of Capital Fund", unknown_ok=True)
    assert ok


def test_new_entrants_are_excluded_for_want_of_history_not_quality():
    ok, why = A.admit("Jio BlackRock Liquid Fund")
    assert not ok and "new_entrant" in why


def test_a_documented_house_event_is_surfaced_on_admission():
    ok, why = A.admit("Franklin India Liquid Fund")
    assert ok
    assert "wound up six debt schemes" in why


def test_every_registry_entry_has_an_archetype_and_a_note():
    for frag, (arch, note) in A.AMC_REGISTRY.items():
        assert arch in (A.BANK, A.INSURER, A.GLOBAL_JV, A.CONGLOMERATE,
                        A.STANDALONE, A.NEW_ENTRANT), frag
        assert note and isinstance(note, str), frag


def test_partition_splits_without_loss():
    names = ["HDFC Liquid Fund", "Jio BlackRock Liquid Fund", "Nonesuch Fund"]
    yes, no = A.partition(names)
    assert len(yes) + len(no) == len(names)
    assert {n for n, _ in yes} | {n for n, _ in no} == set(names)


# ── the monitor ─────────────────────────────────────────────────────────────

def test_the_monitor_does_not_claim_to_predict():
    """
    A fund identical to its peers until the moment it breaks must show NO
    signal beforehand. Measured on the real episode, the five DHFL and IL&FS
    funds sat inside the control distribution for eighteen months.
    """
    me = month_ends(120)
    funds = {f"p{i}": flat_fund(me, 0.005, amc=f"a{i}", seed=i)
             for i in range(8)}
    funds["victim"] = flat_fund(me, 0.005, amc="v", dip=100, seed=99)
    before = D.signals(funds, me[:100])
    assert not any(v["firing"] for v in before["victim"].values())


def test_the_monitor_catches_the_aftermath():
    me = month_ends(120)
    funds = {f"p{i}": flat_fund(me, 0.005, amc=f"a{i}", seed=i)
             for i in range(8)}
    funds["victim"] = flat_fund(me, 0.005, amc="v", dip=100, seed=99)
    after = D.signals(funds, me)
    assert after["victim"]["peers"]["firing"]
    assert after["victim"]["peers"]["consecutive"] >= 3


def test_a_single_bad_month_does_not_fire():
    """
    Persistence is the whole point; one observation is not a finding.

    The blip is a HALVING for one month against peers whose own noise is
    small, so the failure mode being excluded is unambiguous: a real one-month
    shock, not a rounding wobble.
    """
    me = month_ends(120)
    funds = {f"p{i}": flat_fund(me, 0.005, amc=f"a{i}", seed=i)
             for i in range(8)}
    funds["blip"] = flat_fund(me, 0.005, amc="b", dip=119, seed=98)
    for i in range(8):
        funds[f"p{i}"] = flat_fund(me, 0.005, amc=f"a{i}", seed=200 + i)
    s = D.signals(funds, me, min_months=3)
    assert s["blip"]["peers"]["consecutive"] < 3, \
        f"one bad month produced a run of {s['blip']['peers']['consecutive']}"
    assert not s["blip"]["peers"]["firing"]


def test_the_run_length_is_reported_not_just_a_boolean():
    """Three months in and twelve months in both read True and are not the
    same fund."""
    me = month_ends(140)
    funds = {f"p{i}": flat_fund(me, 0.005, amc=f"a{i}", seed=i)
             for i in range(8)}
    funds["slow"] = flat_fund(me, 0.005, amc="s", dip=100, seed=97)
    s = D.signals(funds, me)
    assert s["slow"]["peers"]["consecutive"] > 5


def test_self_reference_excludes_the_current_reading():
    """A fund decaying for a year would otherwise drag its own median down to
    meet it and look healthy."""
    me = month_ends(160)
    funds = {"solo": flat_fund(me, 0.005, amc="s", dip=110, seed=96)}
    zs = D.series_z(funds, "solo", me, reference="self")
    late = [z for _, z in zs[-6:] if z is not None]
    assert late and min(late) < -1.0


def test_family_reference_needs_an_amc_label():
    me = month_ends(80)
    funds = {"a": flat_fund(me, 0.005, amc=None, seed=1),
             "b": flat_fund(me, 0.005, amc=None, seed=2)}
    s = D.signals(funds, me)
    assert s["a"]["family"]["z"] is None


def test_an_unknown_reference_is_refused():
    me = month_ends(60)
    funds = {"a": flat_fund(me, 0.005)}
    with pytest.raises(ValueError):
        D.series_z(funds, "a", me, reference="nonsense")


def test_robust_z_needs_enough_comparators():
    assert D.robust_z([1.0, 2.0], 5.0) is None
    assert D.robust_z([1.0, 1.0, 1.0, 1.0], 1.0) is None      # zero MAD


def test_family_outranks_peers_in_the_priority_score():
    """A house-level problem shows across an AMC's schemes at once, and
    neither self nor peer comparison sees that as anything but bad luck."""
    sig = {"x": {"peers": dict(z=-3.0, consecutive=4, firing=True)},
           "y": {"family": dict(z=-3.0, consecutive=4, firing=True)}}
    rows = D.redemption_priority(sig)
    assert rows[0]["name"] == "y"


def test_a_fund_with_nothing_firing_scores_zero():
    sig = {"clean": {"peers": dict(z=0.1, consecutive=0, firing=False)}}
    assert D.redemption_priority(sig)[0]["score"] == 0.0


def test_format_priority_says_so_when_nothing_fires():
    sig = {"clean": {"peers": dict(z=0.1, consecutive=0, firing=False)}}
    assert "nothing firing" in D.format_priority(D.redemption_priority(sig))


# ── concentration ───────────────────────────────────────────────────────────

from swp_planner.fund_filters import concentration as K  # noqa: E402


def test_the_loss_from_one_default_is_arithmetic():
    """exposure x (1 - recovery). No model, no estimate, no judgement."""
    assert K.single_issuer_loss(0.03, 0.45) == pytest.approx(0.0165)
    assert K.single_issuer_loss(0.03, 0.00) == pytest.approx(0.03)
    assert K.single_issuer_loss(0.10, 0.45) == pytest.approx(0.055)


def test_implied_exposure_reproduces_the_uti_low_duration_case():
    """
    The fund fell 8.77% the day DHFL was marked down. At a 75% write-down
    that implies about 11.7% of NAV in one issuer -- just above SEBI's 10%
    limit and roughly at the 12% trustee-approved extension.
    """
    assert K.implied_exposure(0.0877, 0.75) == pytest.approx(0.117, abs=0.002)


def test_assuming_a_full_writedown_understates_the_exposure():
    """Funds mark down in stages, so 100% is the flattering assumption."""
    assert K.implied_exposure(0.0877, 1.00) < K.implied_exposure(0.0877, 0.75)


def test_the_historical_losses_happened_inside_the_regulatory_limit():
    """
    THE FINDING THAT JUSTIFIES A TIGHTER HOUSE RULE. Every implied exposure in
    nav_sample.csv sits at or under SEBI's extended 12% ceiling, and the funds
    still lost between 2.2% and 8.8% of NAV overnight. A limit that permits
    that is bounding the disaster, not preventing it.
    """
    for drop in (0.0877, 0.0609, 0.0595, 0.0406, 0.0278):
        assert K.implied_exposure(drop, 0.75) <= K.SEBI_SINGLE_ISSUER_EXTENDED


def test_cap_for_tolerance_inverts_the_arithmetic():
    """State the loss you will accept; the cap follows. A cap chosen by feel
    is a number with no argument behind it."""
    cap = K.cap_for_tolerance(0.01, 0.45)
    assert K.single_issuer_loss(cap, 0.45) == pytest.approx(0.01)
    assert cap < K.SEBI_SINGLE_ISSUER


def test_a_lower_recovery_assumption_demands_a_tighter_cap():
    assert K.cap_for_tolerance(0.01, 0.00) < K.cap_for_tolerance(0.01, 0.60)


def test_min_issuers_is_a_feasibility_check():
    """At 3% a fund needs 34 issuers. A cap the fund cannot meet is not a
    policy, it is a reason to exclude the fund."""
    assert K.min_issuers(0.03) == 34
    assert K.min_issuers(0.10) == 10
    with pytest.raises(ValueError):
        K.min_issuers(0.0)


def test_the_sector_cap_is_the_binding_constraint_not_the_issuer_cap():
    """
    THE ONE MOST EASILY MISSED. IL&FS was September 2018 and DHFL June 2019 --
    both NBFC/HFC, nine months apart. A perfect 3% issuer rule sitting under
    SEBI's 40% financial-sector allowance still permits a 22% loss.
    """
    perfect_issuer_rule = K.single_issuer_loss(0.03, 0.45)
    sector_event = K.sector_loss(K.sebi_max_financial_sector(), 1.0, 0.45)
    assert sector_event > 10 * perfect_issuer_rule
    assert K.sebi_max_financial_sector() == pytest.approx(0.40)


def test_a_partial_sector_default_scales_linearly():
    full = K.sector_loss(0.40, 1.0, 0.45)
    half = K.sector_loss(0.40, 0.5, 0.45)
    assert half == pytest.approx(full / 2)


def test_the_rating_bands_tighten_as_credit_worsens():
    assert K.sebi_issuer_cap("AAA") > K.sebi_issuer_cap("AA") > \
        K.sebi_issuer_cap("A")
    assert K.sebi_issuer_cap("AA", extended=True) == pytest.approx(0.10)


def test_an_unknown_rating_falls_back_to_the_general_limit():
    assert K.sebi_issuer_cap("unrated") == K.SEBI_SINGLE_ISSUER


def test_policy_derives_both_caps_and_flags_them_against_sebi():
    p = K.policy(0.01, 0.03, sector_fraction_defaulting=0.5)
    assert p["issuer_tighter_than_sebi"] and p["sector_tighter_than_sebi"]
    assert p["worst_single"] == pytest.approx(0.01)
    assert p["worst_sector"] == pytest.approx(0.03)
    assert p["min_issuers"] >= 50


def test_the_default_recovery_is_not_optimistic():
    """DHFL resolved at 44-46%, part of it in ten-year paper rather than cash.
    A plan that only survives at 60% has not been stress-tested."""
    assert K.DEFAULT_RECOVERY == pytest.approx(0.45)
    assert K.STRESS_RECOVERY == 0.0


def test_the_sweep_table_renders():
    text = K.format_sweep(K.sweep())
    assert "cap" in text and "%" in text
