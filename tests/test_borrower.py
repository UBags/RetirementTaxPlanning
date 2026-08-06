# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_borrower.py -- the eight categories, and the order they are tested in.

THE ORDER IS THE DESIGN. A PSU bank is also a bank and also a corporate, so
the first rule that fires decides, and the sequence encodes which fact matters
most. Every test here is really a test of the ordering.

THE ONE THAT MATTERS is test_an_nbfc_is_never_a_bank_however_it_is_spelled.
"Non-Banking Financial Company (NBFC)" contains the word "banking", and that
substring put 801 NBFCs in the private-bank band once already.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fund_filters import borrower as B  # noqa: E402
from fund_filters import instruments as I  # noqa: E402


def _r(issuer="SOME LIMITED", sector="", ownership="Non PSU",
       tier=I.CORPORATE, size=1e9):
    return dict(issuer=issuer, issuer_key=issuer.upper(), tier=tier,
                sector=sector, ownership=ownership, issue_size=size)


# ── the ordering ────────────────────────────────────────────────────────────

def test_an_nbfc_is_never_a_bank_however_it_is_spelled():
    """THE ONE THAT MATTERS. The sector string contains 'banking'."""
    for s in ("Non-Banking Financial Company (NBFC)",
              "Non Banking Financial Company", "NBFC", "Housing Finance"):
        assert B.categorise(_r("MUTHOOT FINANCE LIMITED", s)) == B.NBFC


def test_the_sovereign_and_the_states_outrank_everything():
    assert B.categorise(_r(tier=I.SOVEREIGN)) == B.GOI
    assert B.categorise(_r(tier=I.STATE)) == B.STATE


def test_a_psu_bank_is_a_psu_bank_before_it_is_a_bank():
    assert B.categorise(_r("STATE BANK OF INDIA", "Public Sector Bank",
                           "Public Sector Undertaking")) == B.PSU_BANK
    # Sector says public sector even where ownership does not. Nine
    # instruments in the file are like this.
    assert B.categorise(_r("BANK OF INDIA", "Public Sector Bank",
                           "Non PSU")) == B.PSU_BANK


def test_non_psu_is_not_read_as_psu():
    """'Non PSU' contains 'PSU' as a substring. Getting this backwards hands
    the PSU category to every private bank in the file."""
    assert B.categorise(_r("RBL BANK LIMITED", "Private Sector Bank",
                           "Non PSU")) == B.PVT_BANK


def test_the_five_large_private_banks_are_separated_from_the_rest():
    for name in ("HDFC BANK LIMITED", "ICICI BANK LIMITED",
                 "AXIS BANK LIMITED", "KOTAK MAHINDRA BANK LIMITED",
                 "INDUSIND BANK LIMITED"):
        assert B.categorise(_r(name, "Private Sector Bank")) == \
            B.LARGE_PVT_BANK
    for name in ("RBL BANK LIMITED", "DCB BANK LIMITED",
                 "BANDHAN BANK LIMITED"):
        assert B.categorise(_r(name, "Private Sector Bank")) == B.PVT_BANK


def test_hdfc_bank_is_large_but_hdfc_the_nbfc_is_not_a_bank():
    """The list matches on a distinctive token so the two do not collide."""
    assert B.categorise(_r("HDFC BANK LIMITED", "Private Sector Bank")) == \
        B.LARGE_PVT_BANK
    assert B.categorise(_r("HDFC LIMITED",
                           "Non-Banking Financial Company (NBFC)")) == B.NBFC
    assert B.categorise(_r("HDB FINANCIAL SERVICES LIMITED",
                           "Non-Banking Financial Company (NBFC)")) == B.NBFC


# ── the size test ───────────────────────────────────────────────────────────

def test_a_large_corporate_is_decided_by_size_not_by_a_list():
    """A hand-kept list of large companies is a list of the companies someone
    thought of. The file states each instrument's issue size."""
    big = B.LARGE_CORPORATE_FLOOR + 1
    assert B.categorise(_r("RELIANCE INDUSTRIES LIMITED", "Refineries"),
                        issuer_total=big) == B.LARGE_CORPORATE
    assert B.categorise(_r("TINY TRADERS PRIVATE LIMITED", "Trading"),
                        issuer_total=1e6) == B.OTHER_CORPORATE


def test_an_unknown_size_falls_to_other_corporate_not_upward():
    """An unknown borrower is not a large one."""
    assert B.categorise(_r("MYSTERY LIMITED", "Trading"),
                        issuer_total=None) == B.OTHER_CORPORATE


def test_the_size_test_sums_across_the_issuers_instruments():
    recs = [_r("BIG LIMITED", "Trading", size=B.LARGE_CORPORATE_FLOOR / 2),
            _r("BIG LIMITED", "Trading", size=B.LARGE_CORPORATE_FLOOR / 2 + 1)]
    out = B.categorise_all(recs)
    assert all(r["borrower_category"] == B.LARGE_CORPORATE for r in out)


def test_a_missing_issue_size_neither_promotes_nor_demotes():
    recs = [dict(_r("PART LIMITED", "Trading",
                    size=B.LARGE_CORPORATE_FLOOR + 1)),
            dict(_r("PART LIMITED", "Trading"), issue_size=None)]
    out = B.categorise_all(recs)
    assert all(r["borrower_category"] == B.LARGE_CORPORATE for r in out)


# ── the default, and the fallbacks ──────────────────────────────────────────

def test_everything_unclear_defaults_to_other_corporate():
    assert B.categorise(_r("UNKNOWN THING", "")) == B.OTHER_CORPORATE
    assert B.categorise({}) == B.OTHER_CORPORATE
    assert B.categorise(dict(issuer=None, sector=None,
                             ownership=None)) == B.OTHER_CORPORATE


def test_a_blank_sector_falls_back_to_the_name_for_banks():
    """The sector column is blank on 1,183 rows. The name is the fallback,
    not the primary test."""
    assert B.categorise(_r("PUNJAB NATIONAL BANK", "",
                           "Public Sector Undertaking")) == B.PSU_BANK
    assert B.categorise(_r("SOME FINANCE LIMITED", "")) == B.NBFC


def test_a_name_containing_bank_is_not_read_as_an_nbfc():
    assert B.categorise(_r("YES BANK LIMITED", "")) == B.PVT_BANK


def test_categorise_all_does_not_mutate_the_input():
    recs = [_r("ALPHA LIMITED", "Trading")]
    B.categorise_all(recs)
    assert "borrower_category" not in recs[0]


@pytest.mark.parametrize("cat", B.CATEGORIES)
def test_every_category_is_a_plain_string(cat):
    """These are stored in a NOT NULL text column and compared by callers."""
    assert isinstance(cat, str) and cat


def test_summarise_counts_only_categories_present():
    out = B.categorise_all([_r("A LIMITED", "Private Sector Bank"),
                            _r("B LIMITED", "Trading")])
    s = B.summarise(out)
    assert s[B.PVT_BANK] == 1 and s[B.OTHER_CORPORATE] == 1
    assert B.GOI not in s
