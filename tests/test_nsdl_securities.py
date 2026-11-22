"""Tests for ingest.nsdl_securities.

Fetch is not tested -- it needs the network. Everything downstream of the bytes
is, using a workbook built in-process so the tests neither need the file nor a
connection.

THE ONE THAT MATTERS is test_a_short_body_is_refused_not_saved: a 400 from
NSDL is 68 bytes of JSON, and saved under a .xlsx name it would pass every
check that looks at the filename.
"""
import datetime as dt

import openpyxl
import pytest

from swp_planner.ingest import nsdl_securities as NS

HEADER = [
    "Sr. No.", "ISIN", "Name of Issuer", "Series", "Security Description",
    "Type of Instrument", "Mode of Issue", "Category of Issue",
    "Face Value(in Rs.)", "Issue Size(in Rs.)", "Date of Allotment",
    "Date of Redemption/Conversion", "Coupon Rate (%)", "Coupon Type",
    "Frequency of Interest Payment", "Credit Rating", "Business Sector",
    "Type of Issuer-Ownership", "Type of Issuer-Nature", "Instrument Status",
]


def _row(n, isin, issuer, rating):
    return [str(n), isin, issuer, "P", f"{issuer} NCD", "Debentures",
            "Private Placement", "Taxable", "100000.000", "200000000000",
            "10-11-2023", "10-11-2033", "7.79", "Simple", "Once a year",
            rating, "Non-Banking Financial Company (NBFC) (IN050101004)",
            "Non PSU", "Other", "Active"]


def _workbook(tmp_path, rows, header=None, name="t.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "List_of_securities"
    ws.append(header or HEADER)
    for r in rows:
        ws.append(r)
    p = tmp_path / name
    wb.save(p)
    return str(p)


def _many(tmp_path, extra=()):
    rows = [_row(i, f"INE{i:03d}A0700{i % 10}", f"ISSUER {i} LIMITED",
                 "CRISIL AAA/Stable") for i in range(NS.MIN_PLAUSIBLE_ROWS + 5)]
    rows.extend(extra)
    return _workbook(tmp_path, rows)


# ── the header contract ─────────────────────────────────────────────────────

def test_an_unexpected_header_raises_rather_than_reading_by_position():
    """NSDL could reorder or insert a column, and a positional reader would
    load coupon rates into the rating field without complaining."""
    bad = ["Sr. No.", "Instrument Code", "Issuer"]
    path = _workbook(None if False else __import__("pathlib").Path("/tmp"),
                     [["1", "INE002A07809", "X"]], header=bad,
                     name="bad_header.xlsx")
    with pytest.raises(ValueError, match="missing"):
        NS.parse_workbook(path)


def test_a_short_file_is_refused():
    """
    A 400 from NSDL is 68 bytes. Saved as .xlsx it passes every check that
    looks at the extension, so the row count is the real gate.
    """
    path = _workbook(__import__("pathlib").Path("/tmp"),
                     [_row(1, "INE002A07809", "RELIANCE LIMITED", "CRISIL AAA")],
                     name="short.xlsx")
    with pytest.raises(ValueError, match="expected at least"):
        NS.parse_workbook(path)


# ── parsing ─────────────────────────────────────────────────────────────────

def test_multi_agency_cells_are_split_and_the_worst_wins(tmp_path):
    raw = ("BWR BB-/Stable () (ISSUER NOT COOPERATING*);IND AA+/Stable ();"
           "ACUITE AA/Stable ()")
    path = _many(tmp_path, [_row(99999, "INE00QS07014", "INDORE MUNICIPAL", raw)])
    recs = NS.parse_workbook(path)
    row = next(r for r in recs if r["isin"] == "INE00QS07014")
    assert len(row["ratings"]) == 3
    assert row["worst_grade"] == "BB-"
    assert row["not_cooperating"]


def test_not_cooperating_is_its_own_field(tmp_path):
    """
    A regulator-mandated disclosure: the issuer stopped supplying information
    to its agency. That is a decision the issuer took, not an opinion the
    agency formed, and it is the one forward-looking field in the file.
    """
    path = _many(tmp_path, [
        _row(99998, "INE00WJ08019", "JFC FINANCE INDIA LIMITED",
             "BWR C/No Outlook () (ISSUER NOT COOPERATING*)")])
    recs = NS.parse_workbook(path)
    row = next(r for r in recs if r["isin"] == "INE00WJ08019")
    assert row["not_cooperating"] and row["worst_grade"] == "C"


def test_an_unrated_row_is_kept_with_a_null_grade(tmp_path):
    """69.7% of the real file is unrated. Dropping those rows would lose most
    of the universe and make the coverage look far better than it is."""
    path = _many(tmp_path, [_row(99997, "INE999Z07001", "SMALL NBFC LIMITED", "")])
    recs = NS.parse_workbook(path)
    row = next(r for r in recs if r["isin"] == "INE999Z07001")
    assert row["worst_grade"] is None and row["ratings"] == []


def test_dates_and_numbers_are_typed(tmp_path):
    recs = NS.parse_workbook(_many(tmp_path))
    r = recs[0]
    assert r["redemption"] == dt.date(2033, 11, 10)
    assert r["coupon"] == pytest.approx(7.79)
    assert r["issue_size"] == pytest.approx(2e11)


def test_a_malformed_date_becomes_none_not_an_exception(tmp_path):
    assert NS._date("not a date") is None
    assert NS._date("") is None
    assert NS._date("2033-11-10") == dt.date(2033, 11, 10)


def test_every_row_gets_a_tier(tmp_path):
    recs = NS.parse_workbook(_many(tmp_path))
    assert all(r["tier"] == "corporate" for r in recs[:20])


# ── issuer resolution ───────────────────────────────────────────────────────

def test_corporate_suffixes_are_normalised_away():
    a = NS.canonical_issuer("Dewan Housing Finance Corporation Limited")
    b = NS.canonical_issuer("DEWAN HOUSING FINANCE CORP. LTD")
    assert a == b


def test_punctuation_and_case_do_not_split_an_issuer():
    assert NS.canonical_issuer("L&T Finance Ltd.") == \
        NS.canonical_issuer("L AND T FINANCE LIMITED")


def test_abbreviations_are_deliberately_not_merged():
    """
    'DHFL' and 'Dewan Housing Finance' stay separate. Fuzzy matching would
    understate concentration in exactly the direction that hurts, and a wrong
    merge is invisible afterwards. Unresolved names show up as distinct
    issuers for a human to look at.
    """
    assert NS.canonical_issuer("DHFL") != \
        NS.canonical_issuer("Dewan Housing Finance Limited")


def test_canonical_issuer_handles_empty():
    assert NS.canonical_issuer(None) == ""


# ── identity ────────────────────────────────────────────────────────────────

def test_the_content_hash_ignores_serialisation(tmp_path):
    """
    Three pulls minutes apart gave 2,919,397, 2,919,514 and 2,919,709 bytes --
    the workbook is regenerated per request, so byte hashing cannot tell a
    changed file from the same data serialised twice.
    """
    recs = NS.parse_workbook(_many(tmp_path))
    assert NS.content_hash(recs) == NS.content_hash(list(reversed(recs)))


def test_the_content_hash_moves_when_a_rating_moves(tmp_path):
    a = NS.parse_workbook(_many(tmp_path, [
        _row(99996, "INE202B07IH1", "DHFL LIMITED", "CARE AAA/Stable")]))
    b = NS.parse_workbook(_many(tmp_path, [
        _row(99996, "INE202B07IH1", "DHFL LIMITED", "CARE D")]))
    assert NS.content_hash(a) != NS.content_hash(b)


def test_summarise_reports_coverage_rather_than_hiding_it(tmp_path):
    path = _many(tmp_path, [_row(99995, "INE999Z07002", "UNRATED LIMITED", "")])
    s = NS.summarise(NS.parse_workbook(path))
    assert s["rated"] < s["rows"]
    assert 0 < s["rated_pct"] < 100
    assert s["issuers"] > 1
