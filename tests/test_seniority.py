# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_seniority.py -- subordination, read out of the golden file's own words.

THE ONE THAT MATTERS is test_a_perpetual_is_capped_at_two_percent_not_three,
because it is the whole point stated as money: before this, every perpetual and
every AT1 in the NSDL outstanding universe was sized at the general 3% on a 45%
recovery assumption, and Yes Bank's AT1 holders recovered nothing.

THE ONES THAT KEEP IT HONEST are the false-positive tests. A text rule that
fires too eagerly is worse than no rule, because it is invisible afterwards:
"REDEEMABLE AT 1.05 TIMES" is senior paper with a redemption premium, and a
Hybrid Annuity Model road NCD is senior secured debt with nothing to do with
hybrid capital. Both were live candidates and both are pinned here.

WHY NO SECOND SOURCE IS NEEDED. The file contradicts itself where the rule is
wrong: a perpetual has no maturity, so a flagged row carrying a redemption date
and an unflagged corporate row carrying none are the two shapes of error, and
`seniority_audit` returns both in full so they can be read rather than counted.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fund_filters import caps as C  # noqa: E402
from fund_filters import instruments as I  # noqa: E402
from ingest import nsdl_securities as N  # noqa: E402


def _rec(isin="INE528G08300", itype="Bonds", desc="", redemption=None,
         grade="AAA", sector="Non-Banking Financial Company (NBFC)",
         ownership="Non PSU"):
    """A record shaped as parse_workbook returns one, tiered the same way.

    The default sector is an NBFC at AAA on purpose: that is the shape DHFL and
    IL&FS had, it draws no relief from caps.cap_for, and it is therefore the
    fixture that shows the general cap rather than the private-bank band.
    """
    return dict(isin=isin, issuer="SOME FINANCE LIMITED",
                issuer_key="SOME FINANCE",
                tier=I.classify(None, isin, f"{itype} {desc}",
                                maturity=redemption)["tier"],
                description=desc, instrument_type=itype, sector=sector,
                ownership=ownership, redemption=redemption, worst_grade=grade,
                enhanced=False, not_cooperating=False)


# ── the point, stated as money ──────────────────────────────────────────────

def test_a_perpetual_is_capped_at_two_percent_not_three():
    """THE ONE THAT MATTERS. Asserted through caps.cap_for rather than at the
    tier, because the tier is a means and the cap is the thing that binds."""
    r = _rec(desc="BASEL III COMPLIANT ADDITIONAL TIER 1 PERPETUAL BOND")
    assert r["tier"] == I.SUBORDINATED
    cap, why = C.cap_for_record(r)
    assert cap == C.SUBORDINATED_CAP
    assert cap < C.GENERAL_CAP
    assert "recovery" in why


def test_the_loss_on_a_subordinated_cap_is_the_whole_exposure():
    """2% at zero recovery is not 2% at 45%. The cap number understates the
    tightening unless the recovery assumption travels with it."""
    assert C.max_loss(C.SUBORDINATED_CAP, I.SUBORDINATED) == \
        pytest.approx(C.SUBORDINATED_CAP)
    assert C.max_loss(C.GENERAL_CAP, I.CORPORATE) < C.GENERAL_CAP


def test_a_psu_perpetual_still_gets_no_exemption():
    """caps.cap_for tests seniority before ownership, and this is what makes
    that ordering reachable from the NSDL file at all. SBI's AT1 is not SBI's
    senior paper."""
    r = _rec(desc="BASEL III AT1", sector="Public Sector Bank",
             ownership="Public Sector Undertaking")
    assert C.cap_for_record(r)[0] == C.SUBORDINATED_CAP


def test_a_senior_bond_from_the_same_issuer_is_untouched():
    """The rule must be capable of tightening and incapable of relaxing."""
    r = _rec(desc="7.90% SECURED REDEEMABLE NCD 2028",
             redemption="2028-01-01")
    assert r["tier"] == I.CORPORATE
    assert C.cap_for_record(r)[0] == C.GENERAL_CAP


# ── the false positives that were live candidates ───────────────────────────

def test_a_redemption_premium_is_not_additional_tier_one():
    """'REDEEMABLE AT 1.05 TIMES' contains 'AT 1'. The spaced forms of AT1 are
    deliberately absent from the marker table for exactly this string."""
    assert I.subordinated_text("REDEEMABLE AT 1.05 TIMES FACE VALUE") is None
    assert I.subordinated_text("PUT OPTION AT 10 YEARS") is None


def test_a_hybrid_annuity_road_ncd_is_not_hybrid_capital():
    """HAM project debt is senior secured. HYBRID is matched only next to
    TIER, CAPITAL, DEBT, BOND or INSTRUMENT."""
    assert I.subordinated_text("HYBRID ANNUITY MODEL ROAD PROJECT NCD") is None
    assert I.subordinated_text("HYBRID CAPITAL INSTRUMENT") is not None


def test_at1_inside_an_isin_does_not_fire():
    """The mistake that put 801 NBFCs in the private-bank band was a substring
    test. Markers match on word boundaries."""
    assert I.subordinated_text("INE528G08AT1345") is None
    assert I.subordinated_text("FRONTIER FINANCE LIMITED NCD") is None


def test_the_marker_is_returned_so_a_surprise_can_be_traced():
    """A bare True cannot be argued with. The word that fired can be."""
    assert I.subordinated_text("UNSECURED SUBORDINATED BOND") == "SUBORDINATED"
    assert I.subordinated_text("UPPER TIER II SERIES IV") == "UPPER TIER"


@pytest.mark.parametrize("text,expected", [
    ("BASEL III TIER 2 BOND", True),
    ("PERPETUAL NON-CUMULATIVE BOND", True),
    ("INNOVATIVE PERPETUAL DEBT INSTRUMENT", True),
    ("SUB-DEBT SERIES III", True),
    ("TIER I BOND SERIES 12", True),
    ("LOWER TIER II BONDS", True),
    ("8.5% SECURED NCD 2030", False),
    ("ZERO COUPON BOND 2029", False),
    ("COMMERCIAL PAPER 91 DAYS", False),
    ("MASALA BOND", False),
])
def test_the_marker_table_on_realistic_strings(text, expected):
    assert (I.subordinated_text(text) is not None) is expected


# ── what the rule refuses to touch ──────────────────────────────────────────

def test_the_description_never_moves_a_government_security():
    """Nothing may drag a GoI or SDL security out of the sovereign tier on the
    strength of a word. The test is consulted for CORPORATE alone."""
    assert I.classify(None, "IN0020060045", "GOI PERPETUAL")["tier"] == \
        I.SOVEREIGN
    assert I.classify(None, "IN3120250011", "SDL TIER I")["tier"] == I.STATE


def test_the_description_never_moves_commercial_paper():
    """CP and CD are money market paper by construction and are never
    subordinated; a stray word must not retier them."""
    assert I.classify("CP", "INE721A14XY1", "TIER I")["tier"] == \
        I.MONEY_MARKET_PAPER


def test_a_sectype_that_already_says_subordinated_is_unchanged():
    c = I.classify("AT", "INE528G08300", "BASEL III AT1")
    assert c["tier"] == I.SUBORDINATED
    assert "SECTYPE AT" in c["why"]


def test_classify_without_a_description_behaves_exactly_as_before():
    """The parameter was accepted and ignored before this change. Callers that
    pass nothing must see no difference at all."""
    assert I.classify(None, "INE001A07TT9")["tier"] == I.CORPORATE
    assert I.classify(None, "INE001A07TT9", None)["tier"] == I.CORPORATE
    assert I.classify(None, "INE001A07TT9", "")["tier"] == I.CORPORATE


def test_the_reason_records_which_word_did_it():
    c = I.classify(None, "INE528G08300", "PERPETUAL BOND")
    assert "description says PERPETUAL" in c["why"]


# ── the instrument-type vocabulary ──────────────────────────────────────────

def test_the_audit_reports_the_instrument_type_vocabulary():
    """`Type of Instrument` holds exactly two values in the real file --
    Debentures and Bonds -- which settles that it cannot carry seniority. That
    is worth knowing definitively rather than by inference."""
    import datetime as dt
    recs = [_rec(itype="Bonds", desc="PERPETUAL", redemption=_sent()),
            _rec(itype="Bonds", desc="NCD", redemption=dt.date(2030, 1, 1)),
            _rec(itype="Debentures", desc="NCD",
                 redemption=dt.date(2030, 1, 1))]
    v = N.seniority_audit(recs)["instrument_types"]
    assert v["Bonds"]["count"] == 2 and v["Bonds"]["subordinated"] == 1
    assert v["Debentures"]["subordinated"] == 0


def test_the_audit_counts_which_marker_fired():
    import datetime as dt
    d = dt.date(2030, 1, 1)
    recs = [_rec(desc="PERPETUAL", redemption=d),
            _rec(desc="PERPETUAL", redemption=d),
            _rec(desc="SUBORDINATED DEBT", redemption=d)]
    m = N.seniority_audit(recs)["markers"]
    assert m["PERPETUAL"] == 2 and m["SUBORDINATED"] == 1


# ── the maturity sentinel ───────────────────────────────────────────────────

def _sent():
    import datetime as dt
    return dt.date(9999, 12, 31)


def test_a_perpetual_with_no_seniority_word_is_still_caught():
    """THE SECOND ONE THAT MATTERS. 67 rows in the file carry the sentinel and
    no seniority term at all -- 'REC LIMITED SR 222 7.98 PP BD', 'STATE BANK OF
    INDIA SR 1 8.10 BD PP', 'BANK OF INDIA SR VIII 8.57 BD'. No marker table
    reachable from those strings could ever have found them."""
    c = I.classify(None, "INE020B08EJ6", "Bonds REC LIMITED SR 222 7.98 PP BD",
                   maturity=_sent())
    assert c["tier"] == I.SUBORDINATED
    assert "no maturity" in c["why"]


def test_the_words_still_catch_what_the_sentinel_misses():
    """Nine rows the words catch were filed with a CALL date instead of the
    sentinel and are plainly perpetual -- SBI's AT1, UCO Bank, Canara Bank.
    Neither test alone is complete, which is why the rule is the union."""
    import datetime as dt
    c = I.classify(None, "INE062A08462",
                   "Bonds STATE BANK OF INDIA SR 1 AT1 7.98 BD PP",
                   maturity=dt.date(2033, 1, 1))
    assert c["tier"] == I.SUBORDINATED
    assert "description says" in c["why"]


def test_dated_subordination_is_reachable_by_words_alone():
    """Tata Capital Tier II, DCB Tier 2, IIFL Tier 2 and SMFG sub-debt really
    do mature. No maturity test can ever reach them."""
    import datetime as dt
    for desc in ("TATA CAPITAL LIMITED TIER II SR A 9.32 BD 28DC28",
                 "DCB BANK LIMITED SR I TIER 2 9.20 BD 18NV34",
                 "SMFG INDIA HOME FINANCE LIMITED SR SUBDEBT 01 8.50 NCD"):
        assert I.classify(None, "INE306N08300", desc,
                          maturity=dt.date(2034, 1, 1))["tier"] == \
            I.SUBORDINATED


def test_only_the_exact_sentinel_counts():
    """The file also holds 18 rows dated 2999 and three at 2903, 2927 and
    3034. Those are unexplained; 2903 and 3034 look like typos for 2033 and
    2034, and a range would silently absorb both readings."""
    import datetime as dt
    for y in (2903, 2927, 2999, 3034):
        assert I.classify(None, "INE001A07TT9", "PLAIN NCD",
                          maturity=dt.date(y, 1, 1))["tier"] == I.CORPORATE
    assert I.is_perpetual_maturity(_sent())
    assert not I.is_perpetual_maturity(dt.date(2999, 12, 31))
    assert not I.is_perpetual_maturity(None)


def test_the_sentinel_never_moves_a_government_security():
    assert I.classify(None, "IN0020060045", "GOI LOAN",
                      maturity=_sent())["tier"] == I.SOVEREIGN


def test_the_reason_names_both_tests_when_both_fire():
    c = I.classify(None, "INE01I508016", "KLM AXIVA SR PDI NCD PERPETUAL",
                   maturity=_sent())
    assert "no maturity" in c["why"] and "description says" in c["why"]


def test_classify_without_a_maturity_behaves_exactly_as_before():
    assert I.classify(None, "INE001A07TT9", "PLAIN NCD")["tier"] == I.CORPORATE
    assert I.classify(None, "INE001A07TT9", "PLAIN NCD",
                      maturity=None)["tier"] == I.CORPORATE


# ── the cross-tab that replaced the dead check ──────────────────────────────

def _r(isin, desc, red, itype="Bonds"):
    return dict(isin=isin, instrument_type=itype, description=desc,
                tier=I.classify(None, isin, f"{itype} {desc}",
                                maturity=red)["tier"],
                redemption=red)


def test_the_audit_separates_what_each_test_found():
    import datetime as dt
    recs = [_r("A", "REC SR 222 7.98 PP BD", _sent()),
            _r("B", "SBI SR 1 AT1 7.98 BD PP", dt.date(2033, 1, 1)),
            _r("C", "KLM PDI NCD PERPETUAL", _sent()),
            _r("D", "RIL SR P 7.79 NCD", dt.date(2033, 11, 10))]
    a = N.seniority_audit(recs)
    assert len(a["sentinel_only"]) == 1 and a["sentinel_only"][0]["isin"] == "A"
    assert len(a["words_only"]) == 1 and a["words_only"][0]["isin"] == "B"
    assert len(a["both"]) == 1 and a["both"][0]["isin"] == "C"


def test_the_dead_undated_check_is_gone_not_merely_empty():
    """It returned zero on every real file and read as a clean bill of health.
    Keeping it as a decoration would be worse than deleting it."""
    import datetime as dt
    a = N.seniority_audit([_r("D", "PLAIN NCD", dt.date(2030, 1, 1))])
    assert "undated_unflagged" not in a and "dated_flagged" not in a


def test_unexplained_far_dates_are_reported_not_absorbed():
    import datetime as dt
    a = N.seniority_audit([_r("E", "ODD SR I 9 NCD", dt.date(2999, 1, 1)),
                           _r("A", "REC SR 222 PP BD", _sent())])
    assert len(a["far_dated"]) == 1 and a["far_dated"][0]["isin"] == "E"


def test_the_report_names_the_union_and_both_residues():
    import datetime as dt
    recs = [_r("A", "REC SR 222 7.98 PP BD", _sent()),
            _r("B", "SBI SR 1 AT1 7.98 BD PP", dt.date(2033, 1, 1))]
    txt = N.format_seniority_audit(N.seniority_audit(recs))
    assert "SENTINEL ONLY" in txt and "WORDS ONLY" in txt and "union" in txt
