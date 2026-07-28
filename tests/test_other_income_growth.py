"""
HANDOVER §2.3 — per-stream growth rates on Other Income.

All seven streams were flat for all 30 years, and the engine flattened them
inline in five places (three for personal, two for HUF). Adding growth to five
inline expressions would give five copies of a compounding formula, each free
to drift — the same shape as the seven copies of the expected-return metric
collapsed in §2.5.

So `OtherIncome.taxable(fy)` / `.non_taxable(fy)` are the single definition
and every engine site delegates.

FY1 is the base year: `taxable(1)` returns the figures as entered.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from models import AppState, OtherIncome

# ── the growth calculation ───────────────────────────────────────────────────

def test_fy1_is_the_base_year():
    o = OtherIncome(salary=10.0, salary_growth=5.0)
    assert o.taxable(1) == pytest.approx(10.0)


def test_growth_compounds_from_fy2():
    o = OtherIncome(salary=10.0, salary_growth=5.0)
    assert o.taxable(2) == pytest.approx(10.5)
    assert o.taxable(3) == pytest.approx(11.025)
    assert o.taxable(30) == pytest.approx(10.0 * 1.05 ** 29)


def test_zero_growth_is_flat_for_thirty_years():
    """The compatibility requirement: an existing project must project
    exactly as it did."""
    o = OtherIncome(salary=10.0, rental=5.0, tax_free_interest=2.0)
    assert o.taxable(1) == o.taxable(30) == pytest.approx(15.0)
    assert o.non_taxable(1) == o.non_taxable(30) == pytest.approx(2.0)


def test_streams_grow_independently():
    """
    Per stream rather than one overall rate because they genuinely differ:
    rent tracks one index, a pension may be flat for life, salary stops at
    retirement.
    """
    o = OtherIncome(salary=10.0, salary_growth=8.0,
                    pension=5.0, pension_growth=0.0,
                    rental=4.0, rental_growth=3.0)
    at30 = 10.0 * 1.08 ** 29 + 5.0 + 4.0 * 1.03 ** 29
    assert o.taxable(30) == pytest.approx(at30)


def test_negative_growth_shrinks_the_stream():
    """A pension that is not index-linked loses real value; the model has to
    be able to say so."""
    o = OtherIncome(pension=10.0, pension_growth=-3.0)
    assert o.taxable(11) == pytest.approx(10.0 * 0.97 ** 10)
    assert o.taxable(30) < o.taxable(1)


def test_taxable_and_non_taxable_stay_separate():
    o = OtherIncome(salary=10.0, salary_growth=5.0,
                    tax_free_interest=8.0, tax_free_interest_growth=2.0)
    assert o.taxable(10) == pytest.approx(10.0 * 1.05 ** 9)
    assert o.non_taxable(10) == pytest.approx(8.0 * 1.02 ** 9)
    assert o.total(10) == pytest.approx(o.taxable(10) + o.non_taxable(10))


def test_every_stream_has_a_growth_field():
    """A stream without one would silently stay flat forever."""
    from dataclasses import fields
    names = {f.name for f in fields(OtherIncome)}
    for amount in ("salary", "taxable_interest", "tax_free_interest",
                   "pension", "rental", "other_taxable", "other_non_taxable"):
        assert amount in names
        assert f"{amount}_growth" in names, f"{amount} has no growth field"


def test_all_seven_streams_are_summed_somewhere():
    """Guards a stream being added to the dataclass and forgotten in the
    taxable/non-taxable split — it would vanish from every projection."""
    o = OtherIncome(**{k: 1.0 for k in (
        "salary", "taxable_interest", "tax_free_interest", "pension",
        "rental", "other_taxable", "other_non_taxable")})
    assert o.total(1) == pytest.approx(7.0)


def test_fy_below_one_is_clamped():
    o = OtherIncome(salary=10.0, salary_growth=5.0)
    assert o.taxable(0) == pytest.approx(10.0)


# ── persistence ──────────────────────────────────────────────────────────────

def test_growth_survives_a_round_trip():
    s = AppState()
    s.personal_income = OtherIncome(salary=10.0, salary_growth=5.0,
                                    rental=4.0, rental_growth=3.0)
    back = AppState.from_dict(s.to_dict()).personal_income
    assert back.salary_growth == pytest.approx(5.0)
    assert back.rental_growth == pytest.approx(3.0)


def test_a_project_saved_before_2_3_loads_flat():
    """No growth keys at all — every stream must default to 0.0 and the
    projection must be unchanged."""
    d = AppState().to_dict()
    d["personal_income"] = {"salary": 10.0, "rental": 5.0}
    inc = AppState.from_dict(d).personal_income
    assert inc.salary == pytest.approx(10.0)
    assert inc.salary_growth == 0.0
    assert inc.taxable(30) == pytest.approx(15.0)


def test_an_unknown_key_does_not_take_the_whole_load_down():
    """
    A file written by a newer build, or hand-edited, used to raise TypeError
    from `OtherIncome(**d2)` and fail the entire project load.
    """
    d = AppState().to_dict()
    d["personal_income"] = {"salary": 9.0, "some_future_field": 1}
    assert AppState.from_dict(d).personal_income.salary == pytest.approx(9.0)


# ── the engine delegates ─────────────────────────────────────────────────────

def test_no_engine_site_still_flattens_income_inline(project_root):
    """
    Five sites summed the streams by hand. With growth rates that would be
    five copies of a compounding expression — and §2.1 exists because two
    copies of a metric drifted apart.
    """
    src = (project_root / "engine.py").read_text(encoding="utf-8")
    offenders = [ln.strip() for ln in src.splitlines()
                 if "pi.salary" in ln or "hi.salary" in ln]
    assert not offenders, (
        "engine.py still sums the income streams inline instead of calling "
        f"OtherIncome.taxable()/.non_taxable(): {offenders}")


def test_the_optimiser_resolves_income_inside_the_fy_loop(project_root):
    """
    `_optimal_split` hoisted taxable_other out of the per-FY loop and computed
    it once — correct only while the streams were flat. A hoisted value would
    apply FY1 income to all thirty years and understate tax in every later one.
    """
    src = (project_root / "engine.py").read_text(encoding="utf-8")
    assert "# ── Personal other taxable income (constant) ──" not in src, (
        "the optimiser still describes other income as constant")
    # Match on WHAT is being asserted, not on one spelling of it. This test
    # has now broken three times on incidental changes to the same line: A2
    # added a months argument and split the call across lines, and B0 routed
    # it through assessable() so the slab sees income net of s.16(ia) and
    # s.24(a). The assertion is that the optimiser resolves income PER FY,
    # inside the FY loop -- not which method it calls or how many arguments
    # that method takes.
    # SCOPE THE SEARCH TO THE OPTIMISER. Broken a fourth time, by the HUF
    # ration: `_huf_ration` resolves `pi.taxable(fy, m_fy)` for its demand
    # ceiling, sits ~1500 lines earlier, and is correctly NOT inside an FY
    # loop -- it is called once per year from the monthly loop. The regex hit
    # that first and the backwards search for a loop then found nothing.
    #
    # Every one of the four breakages has the same cause: a whole-file search
    # for something that belongs to one function. The assertion is about
    # `optimize_withdrawal_split`, so search inside it and nowhere else.
    start = src.find("def optimize_withdrawal_split(")
    assert start != -1, "optimize_withdrawal_split has been renamed or removed"
    body = src[start:]
    m = re.search(r"pi\.(taxable|assessable)\(\s*\n?\s*fy\b", body)
    assert m, "the optimiser does not resolve income per FY"
    idx = m.start()
    # Match the loop by what it is, not by one spelling of it. This used to
    # look for the literal `for fy in range(1, 31):` and broke when Phase A1
    # replaced that with `state.fy_range()` — the assertion is about where the
    # resolution sits, not about how the horizon happens to be written.
    loop = max((m.start() for m in re.finditer(r"^\s*for fy in .+:$",
                                               body[:idx], re.M)), default=-1)
    assert loop != -1, "the per-FY resolution is not inside the FY loop"


def test_growth_reaches_the_projection():
    """
    End to end, isolating GROWTH from LEVEL: same rental amount either way,
    only the growth rate differs. FY1 must be identical (base year) and the
    final year must be higher.

    Comparing against no-rental-at-all would not work: adding taxable income
    raises the FD benchmark, which raises `tax_saved`, which is transferred to
    the HUF and deducted from personal net cash. On this corpus +5.0L of
    rental moves FY1 personal net cash by only +0.52L for that reason. Real
    behaviour, but it makes a naive delta assertion meaningless.
    """
    import copy
    import sys

    from engine import Engine
    sys.path.insert(0, str(pathlib.Path(__file__).parent / "golden"))
    from golden_projects import PROJECTS

    base = AppState.from_dict(PROJECTS["main"]["scenarios"][0])
    base.personal_income.rental = 5.0

    flat_state = copy.deepcopy(base)
    flat_state.personal_income.rental_growth = 0.0
    flat = Engine(flat_state).run()[1]

    grown_state = copy.deepcopy(base)
    grown_state.personal_income.rental_growth = 6.0
    grown = Engine(grown_state).run()[1]

    assert grown[0].net_cash_total == pytest.approx(
        flat[0].net_cash_total, abs=1e-9), (
        "FY1 is the base year and must be unaffected by the growth rate")
    assert grown[-1].net_cash_total > flat[-1].net_cash_total, (
        "a 6% growing stream did not compound into the final year")
    assert grown[-1].net_cash_total - flat[-1].net_cash_total > 1.0, (
        "the compounded difference is implausibly small over 30 years")


# ── the growth-rate parser ───────────────────────────────────────────────────

def _parse_growth(project_root):
    """Lift parse_growth out of dialogs.py without importing PySide6."""
    src = (project_root / "dialogs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "parse_growth")
    ns: dict = {}
    exec(compile(ast.Module([fn], []), "<parse_growth>", "exec"), ns)
    return ns["parse_growth"]


@pytest.mark.parametrize("text,expected", [
    ("", 0.0), ("   ", 0.0), ("5", 5.0), ("5.5", 5.5),
    ("-2.5", -2.5), ("7%", 7.0), (" 3 ", 3.0), ("0", 0.0),
])
def test_growth_parser_accepts(project_root, text, expected):
    assert _parse_growth(project_root)(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["abc", "150", "-101", "5,5", "1e999", "--3"])
def test_growth_parser_rejects_rather_than_zeroing(project_root, text):
    """
    A typo that quietly became 0% would flatten a stream for thirty years and
    show no sign of having done so. Empty means zero; malformed means error.
    """
    with pytest.raises(ValueError):
        _parse_growth(project_root)(text)
