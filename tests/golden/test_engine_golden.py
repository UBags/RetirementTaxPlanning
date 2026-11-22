"""
Golden-master snapshots for Engine.run().

Purpose: freeze current behaviour so the Phase 3 refactor of Engine.run()
(1,304 lines, Personal and HUF near-duplicated) can be proved behaviour-neutral.
If a snapshot moves during a refactor, the refactor is wrong.

Snapshots tell you something changed. Invariants tell you the change was wrong.
Both are needed.
"""
from __future__ import annotations

import csv
import io

import pytest

REL_TOL = 1e-9
ABS_TOL = 1e-9

YEARLY_FIELDS = [
    "year",
    "corpus_debt_personal", "corpus_equity_personal", "corpus_other_personal",
    "corpus_debt_huf", "corpus_equity_huf", "corpus_other_huf",
    "tax_personal", "tax_huf",
    "net_cash_personal", "net_cash_huf", "net_cash_total",
    "swp_cash_personal", "fd_tax_benchmark", "tax_saved",
    "rebalance_tax_paid",
]


def _run(state):
    # project_plan, not Engine: annual_requirements is a NET target since
    # Phase B, and the app solves for the schedule that delivers it. A golden
    # file generated from Engine alone would pin behaviour nothing runs.
    from swp_planner.core.net_income_plan import project_plan
    p_monthly, p_yearly, h_monthly, h_yearly, _ = project_plan(state)
    return p_monthly, p_yearly, h_monthly, h_yearly


def _yearly_to_csv(rows, fingerprint: str) -> str:
    buf = io.StringIO()
    buf.write(f"# config: {fingerprint}\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(YEARLY_FIELDS)
    for r in rows:
        w.writerow([getattr(r, f) if f == "year" else f"{getattr(r, f, 0.0):.10f}"
                    for f in YEARLY_FIELDS])
    return buf.getvalue()


def _parse(text: str):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    fp = next((ln for ln in lines if ln.startswith("# config:")), "")
    body = [ln for ln in lines if not ln.startswith("#")]
    return fp.replace("# config: ", ""), list(csv.DictReader(io.StringIO("\n".join(body))))


# ── Snapshots ────────────────────────────────────────────────────────────────

def test_yearly_summary_snapshot(scenario, snapshot_dir, regen_golden,
                                 config_fingerprint):
    label, state = scenario
    _, yearly, _, _ = _run(state)

    actual = _yearly_to_csv(yearly, config_fingerprint)
    path = snapshot_dir / f"{label}_annual.csv"

    if regen_golden or not path.exists():
        path.write_text(actual, encoding="utf-8")
        pytest.skip(f"{'regenerated' if regen_golden else 'created'} {path.name}"
                    f"{'' if regen_golden else ' — review and commit it'}")

    expected_fp, expected_rows = _parse(path.read_text(encoding="utf-8"))
    actual_fp, actual_rows = _parse(actual)

    assert expected_fp == actual_fp, (
        "RetirementTaxPlanning.configuration changed since this snapshot was "
        f"taken.\n  snapshot: {expected_fp}\n  current:  {actual_fp}\n"
        "Either revert the config or regenerate with --regen-golden."
    )
    assert len(actual_rows) == len(expected_rows), (
        f"row count changed: {len(expected_rows)} -> {len(actual_rows)}")

    diffs = []
    for exp, act in zip(expected_rows, actual_rows):
        for field in YEARLY_FIELDS:
            e, a = float(exp[field]), float(act[field])
            if abs(e - a) > max(ABS_TOL, REL_TOL * abs(e)):
                diffs.append(f"    FY{exp['year']:>2} {field}: {e:.6f} -> {a:.6f}")

    assert not diffs, (f"{len(diffs)} value(s) moved in {label}:\n"
                       + "\n".join(diffs[:25])
                       + ("\n    ..." if len(diffs) > 25 else ""))


# ── Invariants — hold for any input ──────────────────────────────────────────

def test_monthly_withdrawal_components_sum(scenario):
    """principal + gain == withdrawal, per category, per month, both entities."""
    label, state = scenario
    p_rows, _, h_rows, _ = _run(state)
    for entity, rows in (("personal", p_rows), ("huf", h_rows)):
        for r in rows:
            for cat in ("debt", "equity", "other"):
                wd = getattr(r, f"wd_{cat}")
                pr = getattr(r, f"principal_{cat}")
                gn = getattr(r, f"gain_{cat}")
                assert abs((pr + gn) - wd) < 1e-6, (
                    f"{label}/{entity} m{r.month_idx} {cat}: "
                    f"principal {pr:.6f} + gain {gn:.6f} != withdrawal {wd:.6f}")


def test_fund_detail_reconciles_with_aggregate(scenario):
    label, state = scenario
    p_rows, _, _, _ = _run(state)
    for r in p_rows:
        if not getattr(r, "fund_withdrawals", None):
            continue
        detail = sum(fd.withdrawal for fd in r.fund_withdrawals)
        agg = r.wd_debt + r.wd_equity + r.wd_other
        assert abs(detail - agg) < 1e-6, (
            f"{label} m{r.month_idx}: fund detail {detail:.6f} != aggregate {agg:.6f}")


def test_corpus_never_negative(scenario):
    label, state = scenario
    _, yearly, _, _ = _run(state)
    for r in yearly:
        for f in ("corpus_debt_personal", "corpus_equity_personal",
                  "corpus_other_personal", "corpus_debt_huf",
                  "corpus_equity_huf", "corpus_other_huf"):
            assert getattr(r, f) >= -1e-6, f"{label} FY{r.year} {f}={getattr(r, f):.6f}"


def test_tax_never_negative(scenario):
    label, state = scenario
    _, yearly, _, _ = _run(state)
    for r in yearly:
        assert r.tax_personal >= -1e-9, f"{label} FY{r.year} tax_personal={r.tax_personal}"
        assert r.tax_huf >= -1e-9, f"{label} FY{r.year} tax_huf={r.tax_huf}"


def test_rebalance_tax_does_not_reduce_net_cash(scenario):
    """v7 'Bug #1': rebalancing tax is self-funded, never out of spendable income."""
    label, state = scenario
    _, yearly, _, _ = _run(state)
    for r in yearly:
        if getattr(r, "rebalance_tax_paid", 0.0) <= 0:
            continue
        assert r.net_cash_personal >= -1e-6, (
            f"{label} FY{r.year}: net_cash_personal {r.net_cash_personal:.6f} "
            f"negative in a year with rebalance tax {r.rebalance_tax_paid:.6f}")


def test_determinism(scenario):
    """Two runs of the same AppState must agree exactly — the premise of snapshots."""
    import copy
    label, state = scenario
    _, y1, _, _ = _run(copy.deepcopy(state))
    _, y2, _, _ = _run(copy.deepcopy(state))
    for a, b in zip(y1, y2):
        for f in YEARLY_FIELDS:
            assert getattr(a, f) == getattr(b, f), (
                f"{label} FY{a.year} {f} differs between identical runs")
