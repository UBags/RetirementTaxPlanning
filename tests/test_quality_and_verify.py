"""Tests for data_synthesizer.quality and data_synthesizer.verify.

THE MOST IMPORTANT TEST IN THIS FILE IS test_the_verifier_rejects_a_bad_series.
The engine this package replaces shipped a batch harness reporting a 20/20 pass
rate, and it reported that because its "historical" inputs were Gaussian noise
possessing none of the properties under test. A harness nobody has watched fail
is not evidence of anything.

The quality tests are built from the two real events in nav_sample.csv and the
two real defects, because those are the cases the screen exists to tell apart:

    2019-06-04  slot 28 falls 8.77% and STAYS down     -> DHFL, keep
    2010-04-08  slot 21 rises 6.46%, gives it back     -> a bad print, drop
"""
import math

import pytest

from data_synthesizer import metrics as M
from data_synthesizer import quality as Q
from data_synthesizer import verify as V


def daily_dates(n, start_year=2010):
    out, y, m, d = [], start_year, 1, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
        d += 1
        if d > 30:
            d, m = 1, m + 1
            if m > 12:
                m, y = 1, y + 1
    return out


def series(moves, start=100.0):
    """moves are simple returns; returns (dates, nav)."""
    lv = [start]
    for mv in moves:
        lv.append(lv[-1] * (1 + mv))
    dates = daily_dates(len(lv))
    return dates, {d: v for d, v in zip(dates, lv)}


# ── the physical bound ──────────────────────────────────────────────────────

def test_cap_lookup_tolerates_real_category_strings():
    assert Q._cap_for("Liquid Fund") == Q.MAX_DAILY_MOVE["liquid"]
    assert Q._cap_for("LIQUID") == Q.MAX_DAILY_MOVE["liquid"]
    assert Q._cap_for("Ultra-Short Duration Fund") == \
        Q.MAX_DAILY_MOVE["ultra short duration"]


def test_an_unknown_category_gets_the_permissive_default():
    """Unknown must not mean STRICT. Screening an unrecognised fund at a
    liquid fund's bound would flag every genuine equity crash as a defect."""
    assert Q._cap_for("Flexi Cap Fund") == Q.DEFAULT_MAX_DAILY_MOVE


def test_an_overnight_fund_has_a_far_tighter_bound_than_a_gilt():
    assert Q.MAX_DAILY_MOVE["overnight"] < Q.MAX_DAILY_MOVE["gilt"] / 10


# ── telling a writedown from a typo ─────────────────────────────────────────

def test_a_permanent_fall_is_a_credit_event_not_a_defect():
    """The DHFL shape: step down, stay down."""
    dates, nav = series([0.0002] * 50 + [-0.0877] + [0.0002] * 50)
    found = Q.implausible_moves(nav, dates, "Low Duration Fund")
    assert len(found) == 1
    assert found[0]["verdict"] == "permanent_loss"


def test_a_spike_that_reverts_is_a_defect_counted_once():
    """
    The 2010-04-08 shape. BOTH LEGS ARE ONE EVENT. Counting the give-back
    separately turned a bad print into a 6% credit loss in a liquid fund --
    an instrument that has never had one and structurally cannot.
    """
    dates, nav = series([0.0001] * 50 + [0.0646, -0.0605] + [0.0001] * 50)
    found = Q.implausible_moves(nav, dates, "Liquid Fund")
    assert len(found) == 1, [f["verdict"] for f in found]
    assert found[0]["verdict"] == "reverting"


def test_a_move_inside_the_bound_is_not_flagged_at_all():
    """2020-03-27: the RBI's 75bp cut moved low duration funds ~1.5%. Real."""
    dates, nav = series([0.0002] * 50 + [0.015] + [0.0002] * 50)
    assert Q.implausible_moves(nav, dates, "Low Duration Fund") == []


def test_the_same_move_is_flagged_in_a_fund_that_cannot_make_it():
    dates, nav = series([0.0002] * 50 + [0.015] + [0.0002] * 50)
    assert len(Q.implausible_moves(nav, dates, "Overnight Fund")) == 1


def test_a_rebase_is_not_also_counted_as_a_credit_event():
    """
    Slot 27 steps to a NAV of exactly 100.0000. Without this the synthesizer
    would learn to produce a 9.6% default in a money market fund.
    """
    dates, nav = series([0.0002] * 50)
    keys = list(dates)
    nav[keys[25]] = 100.0
    nav[keys[24]] = 110.653
    found = Q.implausible_moves(nav, dates, "Money Market Fund")
    assert all(f["date"] != keys[25] for f in found)
    assert any(r["date"] == keys[25] for r in Q.rebases(nav, dates))


# ── the other screens ───────────────────────────────────────────────────────

def test_spike_reversals_finds_a_planted_pair():
    dates, nav = series([0.0001] * 200 + [0.05, -0.0476] + [0.0001] * 200)
    assert len(Q.spike_reversals(nav, dates)) == 1


def test_spike_reversals_ignores_a_permanent_step():
    dates, nav = series([0.0001] * 200 + [-0.05] + [0.0001] * 200)
    assert Q.spike_reversals(nav, dates) == []


def test_flat_runs_finds_a_stale_feed():
    dates, nav = series([0.001] * 20 + [0.0] * 15 + [0.001] * 20)
    runs = Q.flat_runs(nav, dates, min_length=10)
    assert len(runs) == 1 and runs[0]["length"] >= 15


def test_a_healthy_series_is_flagged_for_nothing():
    dates, nav = series([0.0003] * 300)
    rep = Q.screen(nav, dates, name="clean", category="Corporate Bond Fund")
    assert not rep["spike_reversals"] and not rep["implausible"]
    assert not rep["credit_events"] and not rep["rebases"]


def test_coincident_spikes_needs_more_than_one_fund():
    d1, n1 = series([0.0001] * 200 + [0.05, -0.0476] + [0.0001] * 200)
    assert Q.coincident_spikes({"a": (n1, d1)}) == {}
    d2, n2 = series([0.0001] * 200 + [0.05, -0.0476] + [0.0001] * 200)
    assert len(Q.coincident_spikes({"a": (n1, d1), "b": (n2, d2)})) == 1


# ── the verifier ────────────────────────────────────────────────────────────

def _gbm(n, mu, sigma, seed, start=100.0):
    import random
    rng = random.Random(seed)
    lv, d = [start], 1.0 / 365.0
    for _ in range(n):
        lv.append(lv[-1] * math.exp(rng.gauss(mu * d - 0.5 * sigma * sigma * d,
                                              sigma * math.sqrt(d))))
    dates = daily_dates(len(lv))
    return dates, {k: v for k, v in zip(dates, lv)}


def test_build_targets_marks_unresolvable_criteria_as_unmeasurable():
    """
    Nineteen years cannot resolve an eighteen-month variance ratio. Anything
    whose band across contiguous sub-periods is wider than the estimate itself
    must NOT vote, or the generator is held to precision nobody has.
    """
    dates, nav = _gbm(365 * 19, 0.11, 0.20, seed=1)
    t = V.build_targets(nav, dates)
    assert "vr18" in t
    assert any(not v["enforceable"] for v in t.values())


def test_a_series_matching_itself_passes():
    dates, nav = _gbm(365 * 19, 0.11, 0.20, seed=2)
    ok, findings = V.verify(nav, dates, nav, dates)
    assert ok, V.format_findings(findings)


def test_the_verifier_rejects_a_bad_series():
    """
    THE TEST THAT MAKES EVERY OTHER RESULT MEAN SOMETHING. A harness nobody has
    watched fail proves nothing, and the harness this package replaces reported
    20/20 while its inputs had none of the properties being tested.
    """
    dates, real = _gbm(365 * 19, 0.11, 0.20, seed=3)
    _, wrong = _gbm(365 * 19, 0.02, 0.45, seed=4)      # wrong drift AND vol
    ok, findings = V.verify(real, dates, wrong, dates)
    assert not ok
    failed = {f["name"] for f in findings if f["verdict"] == "FAIL"}
    assert "cagr" in failed and "vol" in failed


def test_an_exact_criterion_fails_on_a_miss_far_below_any_band():
    """CAGR and vol are SOLVED for, so a miss is a bug, not variation."""
    dates, real = _gbm(365 * 19, 0.11, 0.20, seed=5)
    ok, findings = V.verify(real, dates, real, dates,
                            target_cagr=M.measure(real, dates)["cagr"] + 0.001)
    assert not ok
    assert any(f["name"] == "cagr" and f["verdict"] == "FAIL"
               for f in findings)


def test_a_single_path_band_wrongly_rejects_a_correct_generator():
    """
    THE REASON verify_ensemble EXISTS. Two independent draws from an IDENTICAL
    process disagree by more than the real series disagrees with itself across
    sub-periods, because those sub-periods share one realised history. The
    single-path form therefore fails a generator that is by construction
    perfect -- and this test pins that so nobody restores it as the default.
    """
    dates, real = _gbm(365 * 19, 0.11, 0.20, seed=6)
    _, other = _gbm(365 * 19, 0.11, 0.20, seed=7)
    m = M.measure(other, dates)
    ok, findings = V.verify(real, dates, other, dates,
                            target_cagr=m["cagr"], target_vol=m["vol"])
    assert not ok
    assert any(f["name"].startswith("vr") and f["verdict"] == "FAIL"
               for f in findings)


def test_the_ensemble_form_accepts_that_same_correct_generator():
    dates, real = _gbm(365 * 19, 0.11, 0.20, seed=6)
    paths = [(_gbm(365 * 19, 0.11, 0.20, seed=100 + i)[1], dates)
             for i in range(12)]
    ms = [M.measure(n, d) for n, d in paths]
    ok, findings = V.verify_ensemble(
        real, dates, paths,
        target_cagr=sum(m["cagr"] for m in ms) / len(ms),
        target_vol=sum(m["vol"] for m in ms) / len(ms))
    est = [f for f in findings if f["kind"] == "estimated"]
    assert not any(f["verdict"] == "FAIL" for f in est), V.format_findings(est)


def test_the_ensemble_form_still_rejects_a_wrong_generator():
    dates, real = _gbm(365 * 19, 0.11, 0.20, seed=6)
    paths = [(_gbm(365 * 19, 0.02, 0.45, seed=200 + i)[1], dates)
             for i in range(12)]
    ok, findings = V.verify_ensemble(real, dates, paths)
    assert not ok


def test_an_ensemble_of_one_is_refused():
    """A single path has more latitude than any defect it could detect."""
    dates, real = _gbm(365 * 19, 0.11, 0.20, seed=6)
    with pytest.raises(ValueError):
        V.verify_ensemble(real, dates, [(real, dates)])


def test_subsample_range_uses_contiguous_windows():
    """
    Contiguous, NEVER reshuffled. A moving-block bootstrap destroys the
    dependence it is asked to put a band around, and when it was used here it
    returned intervals that did not contain their own point estimates.
    """
    dates, nav = _gbm(365 * 19, 0.11, 0.20, seed=8)
    rng = V.subsample_range(nav, dates, V._vr(12), window_years=9)
    assert rng is not None
    lo, hi, n = rng
    assert n >= 5 and lo < hi


def test_subsample_range_returns_none_when_the_window_does_not_fit():
    dates, nav = _gbm(365 * 3, 0.11, 0.20, seed=9)
    assert V.subsample_range(nav, dates, V._vr(12), window_years=9) is None


def test_format_findings_renders_every_verdict_without_raising():
    dates, real = _gbm(365 * 19, 0.11, 0.20, seed=10)
    _, wrong = _gbm(365 * 19, 0.02, 0.45, seed=11)
    _, findings = V.verify(real, dates, wrong, dates)
    text = V.format_findings(findings)
    assert "FAIL" in text and len(text.splitlines()) == len(findings)
