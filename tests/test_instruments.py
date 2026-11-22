"""Tests for fund_filters.instruments.

THE ONE THAT MATTERS is test_aaa_corporates_get_no_relaxation. DHFL and IL&FS
were both rated AAA within weeks of default, so a rule permitting AAA paper
above the general cap would have authorised precisely the exposures that cost
these funds 2.2% to 8.8% of NAV overnight. If a later edit relaxes the
corporate tier, this test is the argument it has to answer.
"""
import pytest

from swp_planner.fund_filters import concentration as K
from swp_planner.fund_filters import instruments as I

# ── sovereign and state ─────────────────────────────────────────────────────

def test_government_paper_is_sovereign_and_uncapped():
    for st, isin in (("GS", "IN0020060045"), ("GZ", "IN0020210012"),
                     ("TB", "IN002026X123")):
        c = I.classify(st, isin)
        assert c["tier"] == I.SOVEREIGN and c["sovereign"]
    assert I.cap_for(I.SOVEREIGN, 0.02) is None


def test_the_state_code_comes_out_of_the_isin():
    assert I.classify("SG", "IN3120250011")["state"] == "Tamil Nadu"
    assert I.classify("SG", "IN4520260007")["state"] == "Telangana"
    assert I.classify("SG", "IN3920250013")["state"] == "Delhi (NCT)"


def test_delhi_is_present():
    """
    Code 39 was missing from the first draft and was caught by classifying the
    whole file and looking for unmapped codes, not by reading the map.
    """
    assert "39" in I.SDL_STATE


def test_an_unmapped_state_code_is_reported_not_swallowed():
    c = I.classify("SG", "IN9920250001")
    assert c["tier"] == I.STATE and c["state"] is None
    assert "not in the map" in c["why"]


def test_states_are_generous_but_not_uncapped():
    """No state has defaulted, but the RBI mechanism has never been tested by
    a genuine refusal to pay. Never happened is not cannot happen."""
    cap = I.cap_for(I.STATE, 0.02)
    assert cap is not None and cap > 0.02


# ── the corporate line ──────────────────────────────────────────────────────

def test_aaa_corporates_get_no_relaxation():
    """DHFL WAS AAA. IL&FS WAS AAA. Both defaulted within weeks of holding
    top-of-scale ratings."""
    assert I.TIER_CAP_MULTIPLE[I.CORPORATE] == 1.0
    assert I.cap_for(I.CORPORATE, 0.0182) == pytest.approx(0.0182)


def test_subordinated_paper_is_capped_tighter_than_senior():
    """Yes Bank's AT1 was written to ZERO in March 2020 while its senior debt
    was money-good. An issuer-level rating would have called both AA."""
    assert I.cap_for(I.SUBORDINATED, 0.02) < I.cap_for(I.CORPORATE, 0.02)
    for st in ("AT", "PF", "PZ"):
        assert I.classify(st, "INE528G08300")["tier"] == I.SUBORDINATED


def test_sectype_beats_isin_because_ine_cannot_separate_seniority():
    """INE covers every corporate instrument including the subordinated ones,
    so an ISIN-only rule would rate perpetuals as senior bank paper."""
    isin = "INE528G08300"
    assert I.classify("AT", isin)["tier"] == I.SUBORDINATED
    assert I.classify(None, isin)["tier"] == I.CORPORATE


def test_commercial_paper_is_its_own_tier():
    assert I.classify("CP", "INE721A14XY1")["tier"] == I.MONEY_MARKET_PAPER


# ── refusal ─────────────────────────────────────────────────────────────────

def test_an_unrecognised_instrument_is_unknown_not_sovereign():
    """Silently treating something unnameable as government paper is the worst
    error available to this module."""
    c = I.classify("ZZ", "XX1234567890")
    assert c["tier"] == I.UNKNOWN and not c["sovereign"]


def test_unknown_is_capped_tightly_rather_than_generously():
    assert I.cap_for(I.UNKNOWN, 0.02) < 0.02


def test_state_code_needs_a_plausible_isin():
    assert I.state_code(None) is None
    assert I.state_code("XY31") is None


# ── the whole file ──────────────────────────────────────────────────────────

def _wdm_rows():
    return [
        dict(SECTYPE="GS", **{"ISIN NO.": "IN0020060045"},
             ISSUE_DESC="GOI LOAN 8.33% 2036", MAT_DATE="07-Jun-2036"),
        dict(SECTYPE="SG", **{"ISIN NO.": "IN3120250011"},
             ISSUE_DESC="TAMILNADU SDL 7.4% 2035", MAT_DATE="01-Jan-2035"),
        dict(SECTYPE="DB", **{"ISIN NO.": "INE001A07TT9"},
             ISSUE_DESC="HDFC 7.9% 2028", MAT_DATE="01-Jan-2028"),
        dict(SECTYPE="AT", **{"ISIN NO.": "INE528G08300"},
             ISSUE_DESC="YES BANK AT1", MAT_DATE="PERPETUAL"),
    ]


def test_parse_wdmlist_keeps_the_source_fields():
    out = I.parse_wdmlist(_wdm_rows())
    assert len(out) == 4
    assert out[0]["isin"] == "IN0020060045"
    assert out[0]["maturity"] == "07-Jun-2036"


def test_summarise_counts_tiers_and_states():
    s = I.summarise(I.parse_wdmlist(_wdm_rows()))
    assert s["total"] == 4
    assert s["tiers"][I.SOVEREIGN] == 1
    assert s["states"]["Tamil Nadu"] == 1


def test_the_cap_structure_descends_from_a_loss_tolerance():
    """The whole point: no number in this module is chosen by feel. The
    general cap comes from concentration.cap_for_tolerance and the tiers are
    multiples of it."""
    general = K.cap_for_tolerance(0.01, K.DEFAULT_RECOVERY)
    assert I.cap_for(I.CORPORATE, general) == pytest.approx(general)
    assert I.cap_for(I.SUBORDINATED, general) < general
    assert I.cap_for(I.SOVEREIGN, general) is None
