"""Tests for fund_filters.caps.

THE ONE THAT MATTERS is test_an_nbfc_is_not_a_bank. A substring test for 'bank'
put 'Non-Banking Financial Company (NBFC)' in the 12.5% band -- 801 of the 875
instruments that first landed there were NBFCs, which is the category DHFL and
IL&FS belonged to. The relief went almost entirely to the riskiest sector in
the file.
"""
import pytest

from swp_planner.fund_filters import caps as C
from swp_planner.fund_filters import instruments as I

BANK = "Private Sector Bank (IN050102002)"
PSU_BANK = "Public Sector Bank (IN050102001)"
FI = "Financial Institution (IN050101001)"
NBFC = "Non-Banking Financial Company (NBFC) (IN050101004)"
HFC = "Housing Finance Company (IN050101002)"


def cap(**kw):
    kw.setdefault("tier", I.CORPORATE)
    return C.cap_for(**kw)[0]


# ── the trap ────────────────────────────────────────────────────────────────

def test_an_nbfc_is_not_a_bank():
    """'Non-Banking Financial Company' contains 'bank'. DHFL was an HFC and
    IL&FS an NBFC; neither may take the bank band."""
    assert not C.is_bank_or_fi(NBFC)
    assert not C.is_bank_or_fi(HFC)
    assert cap(grade="AAA", sector=NBFC, ownership="Non PSU") == C.GENERAL_CAP
    assert cap(grade="AAA", sector=HFC, ownership="Non PSU") == C.GENERAL_CAP


def test_a_real_bank_is_a_bank():
    assert C.is_bank_or_fi(BANK) and C.is_bank_or_fi(PSU_BANK)
    assert C.is_bank_or_fi(FI)


def test_non_psu_is_not_psu():
    """The same substring trap one column over."""
    assert not C.is_psu("Non PSU")
    assert C.is_psu("Public Sector Undertaking (PSU)")
    assert not C.is_psu("")


# ── the bands ───────────────────────────────────────────────────────────────

def test_a_psu_bank_at_aaa_is_exempt():
    assert cap(grade="AAA", sector=PSU_BANK,
               ownership="Public Sector Undertaking (PSU)") == C.EXEMPT


def test_a_private_bank_at_aaa_gets_the_wide_band():
    assert cap(grade="AAA", sector=BANK, ownership="Non PSU") == \
        C.PRIVATE_BANK_AAA_CAP


def test_the_sector_column_wins_when_it_contradicts_ownership():
    """Nine instruments read sector 'Public Sector Bank' while ownership does
    not say PSU. A public-sector bank IS a PSU."""
    assert cap(grade="AAA", sector=PSU_BANK, ownership="Non PSU") == C.EXEMPT


def test_aaa_alone_buys_nothing():
    """DHFL WAS AAA. IL&FS WAS AAA."""
    assert cap(grade="AAA", sector="Electric Utilities",
               ownership="Non PSU") == C.GENERAL_CAP


def test_a_lower_grade_bank_gets_no_band():
    """Yes Bank was AA+ before reconstruction."""
    assert cap(grade="AA+", sector=BANK, ownership="Non PSU") == C.GENERAL_CAP


def test_sovereign_is_uncapped_and_state_is_wide():
    assert C.cap_for(I.SOVEREIGN)[0] is None
    assert C.cap_for(I.STATE)[0] == C.STATE_CAP


# ── seniority beats everything ──────────────────────────────────────────────

def test_subordinated_paper_of_a_psu_bank_is_still_capped_tight():
    """
    SBI's AT1 is not SBI's senior paper. An exemption leaking across the
    capital structure would be worse than no exemption: Yes Bank's AT1 went to
    zero while its senior debt was money-good.
    """
    assert C.cap_for(I.SUBORDINATED, grade="AAA", sector=PSU_BANK,
                     ownership="Public Sector Undertaking (PSU)")[0] == \
        C.SUBORDINATED_CAP


def test_the_subordinated_cap_is_sized_at_zero_recovery():
    """A 2% cap on AT1 means the FULL 2% is at risk, not 55% of it."""
    assert C.max_loss(C.SUBORDINATED_CAP, I.SUBORDINATED) == \
        pytest.approx(C.SUBORDINATED_CAP)
    assert C.max_loss(C.GENERAL_CAP) == pytest.approx(C.GENERAL_CAP * 0.55)


def test_credit_enhancement_removes_the_band():
    """AAA(CE) describes a guarantee, not the borrower."""
    assert cap(grade="AAA", sector=BANK, ownership="Non PSU",
               enhanced=True) == C.GENERAL_CAP


# ── shape ───────────────────────────────────────────────────────────────────

def test_caps_are_overridable_without_editing_the_module():
    """3% is not settled -- 2.75% and 3.5% are live questions."""
    assert cap(grade="BBB", sector=NBFC, general=0.0275) == 0.0275


def test_every_branch_returns_a_reason():
    for kw in (dict(tier=I.SOVEREIGN), dict(tier=I.STATE),
               dict(tier=I.SUBORDINATED),
               dict(tier=I.CORPORATE, grade="AAA", sector=BANK),
               dict(tier=I.CORPORATE, grade="AAA", sector=NBFC),
               dict(tier=I.CORPORATE, grade="BB")):
        _, reason = C.cap_for(**kw)
        assert reason and len(reason) > 10


def test_max_loss_is_none_for_the_uncappable():
    assert C.max_loss(None) is None
    assert C.max_loss(C.EXEMPT) is None
