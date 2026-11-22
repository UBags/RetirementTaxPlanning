"""Tests for fund_filters.ratings.

THE TWO THAT MATTER are test_enhanced_ratings_are_not_investment_grade and
test_a_downgrade_is_detected_by_rank. The first because AAA(CE) rests on a
guarantee and several IL&FS SPVs carried (SO) grades on the parent's strength;
the second because the LEVEL fooled everyone in 2018 and 2019 and the direction
did not.
"""
import pytest

from swp_planner.fund_filters import ratings as R


@pytest.mark.parametrize("raw,agency,grade", [
    ("CRISIL AAA/Stable", "CRISIL", "AAA"),
    ("[ICRA]AA+(Stable)", "ICRA", "AA+"),
    ("CARE AAA; Stable", "CARE", "AAA"),
    ("IND AA-/Negative", "India Ratings", "AA-"),
    ("BWR A+", "Brickwork", "A+"),
    ("ACUITE AA", "Acuite", "AA"),
    ("CRISIL A1+", "CRISIL", "A1+"),
])
def test_every_house_style_parses(raw, agency, grade):
    """Five agencies write the same grade five ways. A parser that knows one
    silently returns nothing for four fifths of the file, and a missing rating
    looks exactly like an unrated instrument."""
    p = R.parse(raw)
    assert p["agency"] == agency and p["grade"] == grade


def test_the_agency_name_is_removed_before_hunting_for_the_grade():
    """'CARE A' must not read the A out of CARE, and 'IND D' must not read the
    D out of IND."""
    assert R.parse("CARE A")["grade"] == "A"
    assert R.parse("IND D")["grade"] == "D"


def test_longer_grades_win_so_aa_plus_is_not_read_as_aa():
    assert R.parse("CRISIL AA+")["grade"] == "AA+"
    assert R.parse("CRISIL AA-")["grade"] == "AA-"
    assert R.parse("CRISIL BBB-")["grade"] == "BBB-"


def test_enhanced_ratings_are_flagged():
    for raw in ("CRISIL AAA(CE)/Stable", "BWR A+ (SO)", "CARE AA (ce)"):
        assert R.parse(raw)["enhanced"], raw


def test_enhanced_ratings_are_not_investment_grade():
    """
    AAA(CE) IS NOT AAA. The grade rests on a guarantee, a letter of comfort or
    a payment structure. Several IL&FS group SPVs carried (SO) ratings on the
    parent's strength, and when the parent went the structure went with it.
    """
    assert not R.is_investment_grade(R.parse("CRISIL AAA(CE)"))
    assert R.is_investment_grade(R.parse("CRISIL AAA"))


def test_short_and_long_scales_are_kept_separate():
    """A1+ is not comparable to AAA. An issuer can hold a strong short-term
    grade while its long-term outlook rots -- the shape of a refinancing
    crisis."""
    assert R.parse("CRISIL A1+")["scale"] == "short"
    assert R.parse("CRISIL AAA")["scale"] == "long"
    assert R.notch_change(R.parse("CRISIL A1+"), R.parse("CRISIL AAA")) is None


def test_investment_grade_boundary_on_both_scales():
    assert R.is_investment_grade(R.parse("CARE BBB-"))
    assert not R.is_investment_grade(R.parse("CARE BB+"))
    assert R.is_investment_grade(R.parse("CRISIL A3"))
    assert not R.is_investment_grade(R.parse("CRISIL A4"))


def test_outlook_and_withdrawal_are_captured():
    assert R.parse("CRISIL AAA/Negative")["outlook"] == "negative"
    assert R.parse("Rating Withdrawn")["withdrawn"]


def test_an_unparseable_string_is_not_a_grade_of_none():
    """No grade means THIS STRING DID NOT SAY, which is not the same as
    unrated, and must never be read as investment grade."""
    p = R.parse("refer annexure")
    assert p["grade"] is None and not R.is_investment_grade(p)


def test_empty_input_is_handled():
    for raw in (None, "", "   "):
        assert R.parse(raw)["grade"] is None


def test_a_downgrade_is_detected_by_rank():
    """
    DHFL was AAA into early 2019; IL&FS was AAA days before default. The level
    fooled everyone. The direction did not.
    """
    before, after = R.parse("CRISIL AAA"), R.parse("CRISIL D")
    assert R.notch_change(before, after) == -19
    assert R.is_downgrade(before, after)
    assert not R.is_downgrade(after, before)


def test_a_small_downgrade_can_be_required_to_be_larger():
    a, b = R.parse("CRISIL AAA"), R.parse("CRISIL AA+")
    assert R.is_downgrade(a, b, notches=1)
    assert not R.is_downgrade(a, b, notches=2)


def test_notch_change_refuses_incomparable_inputs():
    assert R.notch_change(None, R.parse("CRISIL AAA")) is None
    assert R.notch_change(R.parse("junk"), R.parse("CRISIL AAA")) is None


def test_parse_any_falls_through_to_the_second_column():
    """NSDL puts the rating in CREDIT_RATING_CREDIT_RATING_AGENCY for most rows
    and in REMARKS for some."""
    got = R.parse_any("", "CARE AA+; Stable")
    assert got["grade"] == "AA+"


def test_parse_any_prefers_the_first_column_that_yields_a_grade():
    got = R.parse_any("ICRA AAA", "CARE D")
    assert got["grade"] == "AAA"


def test_worst_is_taken_when_agencies_disagree():
    """A policy reading the most flattering of five opinions is choosing its
    own answer."""
    got = R.worst([R.parse("CRISIL AAA"), R.parse("CARE A-"),
                   R.parse("[ICRA]AA")])
    assert got["grade"] == "A-"


def test_worst_ignores_unparsed_and_short_scale_entries():
    got = R.worst([R.parse("CRISIL AAA"), R.parse("junk"),
                   R.parse("CRISIL A1+")])
    assert got["grade"] == "AAA"
    assert R.worst([R.parse("junk")]) is None
