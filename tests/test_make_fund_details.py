# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_make_fund_details.py -- the input list get_funds_data.py reads.

THE ONE THAT MATTERS is test_only_funds_with_holdings_are_listed. The metrics
file and the credit picture have to cover the same set, or fund_rank scores
funds it cannot check the paper of -- which is the weaker half of the answer
presented as the whole.
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swp_planner.ingest import fund_store as F  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "make_fund_details", _ROOT / "scripts" / "make_fund_details.py")
MFD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MFD)

AUG, JUL = dt.date(2026, 8, 7), dt.date(2026, 7, 31)


def _holding():
    return dict(company_id=1, instrument_code=3, company="REC Ltd.",
                sector="Financial", instrument_type="Debenture",
                asset_class="debt", percent=5.0, value=1e7, is_issuer=True,
                expected_in_nsdl=True, is_securitised=False, seniority=None,
                rating="AAA", rating_confidence=0.99,
                borrower_category="NBFC", gap=None)


@pytest.fixture()
def conn(dsn):
    import psycopg2

    from swp_planner.ingest import store as S
    c = psycopg2.connect(dsn)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS india CASCADE")
    c.commit()
    S.apply_schema(c)
    F.apply_schema(c)
    yield c
    c.close()


def test_only_funds_with_holdings_are_listed(conn):
    """THE METRICS FILE AND THE CREDIT PICTURE MUST COVER THE SAME SET.
    Otherwise fund_rank scores funds whose paper it cannot check, which is the
    weaker half of the answer presented as the whole."""
    F.upsert_fund(conn, 1, "Has Holdings", amfi_name="Has Holdings",
                  category="Debt Scheme - Liquid Fund", amfi_scheme_code=111,
                  as_on=AUG)
    F.upsert_fund(conn, 2, "No Holdings", amfi_name="No Holdings",
                  category="Debt Scheme - Liquid Fund", amfi_scheme_code=222,
                  as_on=AUG)
    F.load_month(conn, 1, JUL, [_holding()], today=AUG)
    names = [r["Fund Name"] for r in MFD.rows_from_db(conn)]
    assert names == ["Has Holdings"]
    assert len(MFD.rows_from_db(conn, require_holdings=False)) == 2


def test_a_fund_with_no_amfi_code_is_left_out(conn):
    """The code is the join key fund_rank uses. Listing a fund without one
    guarantees a name-match fallback, which paired 26 funds fuzzily last
    run."""
    F.upsert_fund(conn, 1, "No Code", amfi_name="No Code",
                  category="Debt Scheme - Liquid Fund", as_on=AUG)
    F.load_month(conn, 1, JUL, [_holding()], today=AUG)
    assert MFD.rows_from_db(conn) == []


def test_the_columns_are_exactly_what_get_funds_data_expects(conn, tmp_path):
    F.upsert_fund(conn, 1, "A Fund", amfi_name="A Fund",
                  category="Debt Scheme - Liquid Fund", amfi_scheme_code=111,
                  as_on=AUG)
    F.load_month(conn, 1, JUL, [_holding()], today=AUG)
    out = MFD.write(MFD.rows_from_db(conn), tmp_path / "Fund Details.csv")
    with open(out, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == ["Fund Type", "Fund Name", "Fund Size",
                                     "AMFI Code"]
        row = next(reader)
    assert row["AMFI Code"] == "111"
    assert row["Fund Size"] == "", "optional, and nothing of ours reads it"


def test_an_existing_list_is_backed_up_not_replaced(conn, tmp_path):
    """The existing list may have been curated by hand -- get_funds_data has a
    KNOWN_CODE_OVERRIDES dict precisely because some funds needed pinning --
    and that history should survive a regeneration."""
    path = tmp_path / "Fund Details.csv"
    path.write_text("Fund Type,Fund Name,Fund Size,AMFI Code\nold,row,,1\n")
    F.upsert_fund(conn, 1, "A Fund", amfi_name="A Fund",
                  category="Debt Scheme - Liquid Fund", amfi_scheme_code=111,
                  as_on=AUG)
    F.load_month(conn, 1, JUL, [_holding()], today=AUG)
    MFD.write(MFD.rows_from_db(conn), path)
    backups = list(tmp_path.glob("Fund Details.*.bak.csv"))
    assert len(backups) == 1
    assert "old,row" in backups[0].read_text()


def test_the_order_is_stable_between_runs(conn):
    """A diff between two generations should show what changed, not a
    reshuffle."""
    for i in range(5):
        F.upsert_fund(conn, i + 1, f"Fund {i}", amfi_name=f"Fund {i}",
                      category="Debt Scheme - Liquid Fund",
                      amfi_scheme_code=100 + i, as_on=AUG)
        F.load_month(conn, i + 1, JUL, [_holding()], today=AUG)
    assert MFD.rows_from_db(conn) == MFD.rows_from_db(conn)
