"""
HANDOVER §2.4 (default chunk structures) and Phase 2.2 (config validation).

§2.4 anchors every Option's default `min_return` to a documented rate instead
of the bare 6.85 that was hardcoded in four places in main.py. The anchor is
`risk_free_rate` — the yield offered by liquid funds — plus a per-chunk
spread that widens with the chunk's horizon.

Phase 2.2 makes `configuration.py` refuse to start on a missing file or a
missing key, and moves the required-key list into the module so it stops
being a copy that drifts. It had already drifted: `test_smoke.py` named 16
keys while the file held 23.

The layout tests pin the exact 30-year table, because the numbers came from a
decision rather than a formula and a "simplification" that changes them is a
silent change to every future plan.
"""
from __future__ import annotations

import pytest

import chunk_defaults as cd

# ── the canonical 30-year table ──────────────────────────────────────────────

EXPECTED_30Y = {
    1: [(1, 30)],
    2: [(1, 15), (16, 30)],
    3: [(1, 10), (11, 20), (21, 30)],
    4: [(1, 7), (8, 15), (16, 23), (24, 30)],
}


@pytest.mark.parametrize("n,expected", sorted(EXPECTED_30Y.items()))
def test_layout_reproduces_the_approved_table(n, expected):
    assert cd.default_chunk_layout(n, horizon=30) == expected


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_layout_is_contiguous_and_covers_the_whole_horizon(n):
    """No gaps, no overlaps, no year unaccounted for — at any horizon."""
    for horizon in (5, 12, 20, 25, 30, 40):
        layout = cd.default_chunk_layout(n, horizon=horizon)
        assert len(layout) == n, f"n={n} horizon={horizon}: wrong chunk count"
        assert layout[0][0] == 1
        assert layout[-1][1] == horizon
        for (_, prev_end), (next_start, _) in zip(layout, layout[1:]):
            assert next_start == prev_end + 1, (
                f"n={n} horizon={horizon}: gap or overlap at FY{prev_end}")
        for start, end in layout:
            assert start <= end, (
                f"n={n} horizon={horizon}: empty chunk FY{start}-{end}")


def test_layout_rejects_a_horizon_too_short_to_split():
    with pytest.raises(ValueError, match="shorter than"):
        cd.default_chunk_layout(4, horizon=3)


def test_layout_rejects_an_unknown_option():
    with pytest.raises(ValueError, match="must be one of"):
        cd.default_chunk_layout(5, horizon=30)


# ── min_return = risk_free_rate + spread ─────────────────────────────────────

def test_min_return_is_the_rate_plus_the_spread():
    chunks = cd.default_chunks_for_option(3, risk_free_rate=0.0665, horizon=30)
    assert [c["min_return"] for c in chunks] == [8.55, 8.15, 7.65]


def test_min_return_moves_with_the_rate():
    """The whole point of §2.4: the defaults are derived, not typed in."""
    lo = cd.default_chunks_for_option(2, risk_free_rate=0.05, horizon=30)
    hi = cd.default_chunks_for_option(2, risk_free_rate=0.08, horizon=30)
    for a, b in zip(lo, hi):
        assert b["min_return"] - a["min_return"] == pytest.approx(3.0, abs=1e-9)


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_spreads_narrow_towards_the_end_of_the_plan(n):
    """
    Earlier chunks are longer-horizon money and take the wider spread; the
    final chunk is closest to being spent. A patch that reversed this would
    still satisfy every arithmetic test above.
    """
    chunks = cd.default_chunks_for_option(n, risk_free_rate=0.0665, horizon=30)
    spreads = [c["spread"] for c in chunks]
    assert spreads == sorted(spreads, reverse=True), (
        f"Option {n}: spreads {spreads} are not monotonically narrowing")
    if n > 1:
        # Option 1's single chunk spans the whole horizon, so it carries the
        # long-duration spread rather than the near-term one.
        assert spreads[-1] == 1.00, (
            f"Option {n}: the final chunk should sit at rf + 1.00")


def test_more_chunks_means_a_wider_first_spread():
    """More chunks = a shorter, more front-loaded first chunk = more room."""
    firsts = [cd.SPREADS_PCT[n][0] for n in (1, 2, 3, 4)]
    assert firsts == sorted(firsts)


@pytest.mark.parametrize("bad", [6.65, 0.0, 1.0, -0.05, 100.0])
def test_a_percentage_passed_as_a_fraction_is_rejected(bad):
    """
    The codebase mixes the two units — config holds fractions, the GUI dicts
    hold percent — so passing 6.65 where 0.0665 was meant is the obvious
    mistake. It would silently produce a min_return of 666%.
    """
    with pytest.raises(ValueError, match="fraction"):
        cd.default_chunks_for_option(2, risk_free_rate=bad, horizon=30)


def test_chunk_dicts_carry_what_the_dialog_needs():
    for c in cd.default_chunks_for_option(4, risk_free_rate=0.0665, horizon=30):
        assert set(c) >= {"year_from", "year_to", "min_return"}
        assert isinstance(c["year_from"], int)
        assert isinstance(c["year_to"], int)
        assert isinstance(c["min_return"], float)


def test_defaults_come_from_config_when_not_passed():
    from configuration import config
    chunks = cd.default_chunks_for_option(1)
    assert chunks[0]["year_to"] == int(config.max_simulation_years)
    assert chunks[0]["min_return"] == pytest.approx(
        config.risk_free_rate * 100.0 + 1.25, abs=1e-9)


def test_configured_rate_is_in_a_plausible_range():
    """
    Cannot assert the configured value is *correct* — only the fund data knows
    that. This catches a unit error or an untouched placeholder drifting far
    from anything a liquid fund has ever yielded.
    """
    from configuration import config
    rf = float(config.risk_free_rate)
    assert 0.02 < rf < 0.12, (
        f"risk_free_rate = {rf} is outside 2%-12%. It is a FRACTION (0.0665 "
        "for 6.65%), and should be the liquid-fund yield — run "
        "percentile_metrics.risk_free_from_liquid_funds() to measure it.")


# ── Phase 2.2: config validation ─────────────────────────────────────────────

def test_required_keys_lives_in_configuration():
    from configuration import REQUIRED_KEYS
    assert isinstance(REQUIRED_KEYS, frozenset)
    assert "risk_free_rate" in REQUIRED_KEYS
    assert "cess_rate" in REQUIRED_KEYS


def test_every_required_key_is_actually_present(config_path):
    """
    The check that had drifted. test_smoke.py's private copy named 16 keys
    while the file held 23, so the five optimizer_* keys,
    rebalance_no_trade_band and default_cagr_fallback were unguarded despite
    all being read at runtime.
    """
    from configuration import REQUIRED_KEYS
    present = {
        ln.split("=", 1)[0].strip()
        for ln in config_path.read_text(encoding="utf-8").splitlines()
        if "=" in ln and not ln.strip().startswith("#")
    }
    missing = sorted(REQUIRED_KEYS - present)
    assert not missing, (
        f"{config_path.name} is missing: {missing}. Add them to the file, or "
        "drop them from configuration.REQUIRED_KEYS if nothing reads them.")


def test_no_required_key_is_silently_unused(project_root):
    """
    The other direction: a key in REQUIRED_KEYS that no module reads is dead
    weight that will outlive whatever used to need it.
    """
    from configuration import REQUIRED_KEYS
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in project_root.glob("*.py") if p.name != "configuration.py"
    )
    unused = sorted(k for k in REQUIRED_KEYS if k not in sources)
    assert not unused, (
        f"REQUIRED_KEYS lists {unused}, which no module references. Either "
        "something stopped reading them, or they belong in UNUSED_KEYS.")


def test_missing_file_raises_instead_of_promising_defaults(tmp_path, monkeypatch):
    """
    The old code warned "Using built-in defaults" and continued. There are no
    built-in defaults: _data stayed empty and the next attribute access raised
    AttributeError with no mention of the config file — and engine.py does
    `CESS = config.cess_rate` at module scope, so it surfaced as an
    unexplained import failure.
    """
    import configuration as cfg

    monkeypatch.setattr(cfg, "get_project_root", lambda: tmp_path)
    fresh = cfg._Configuration.__new__(cfg._Configuration)
    fresh._loaded = False
    fresh._data = {}

    with pytest.raises(FileNotFoundError, match="no built-in defaults"):
        fresh._ensure_loaded()


def test_missing_keys_are_reported_all_at_once(tmp_path, monkeypatch):
    """
    One run, one message, the whole list. Reporting only the first would mean
    an edit-run cycle per missing key.
    """
    import configuration as cfg

    (tmp_path / "RetirementTaxPlanning.configuration").write_text(
        "cess_rate = 0.04\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "get_project_root", lambda: tmp_path)
    fresh = cfg._Configuration.__new__(cfg._Configuration)
    fresh._loaded = False
    fresh._data = {}

    with pytest.raises(KeyError) as exc:
        fresh._ensure_loaded()

    msg = str(exc.value)
    assert "risk_free_rate" in msg and "max_simulation_years" in msg, (
        "the error should name every missing key, not just the first")
    assert "cess_rate" not in msg, "the one key that IS present was reported"


def test_get_still_tolerates_absent_optional_keys():
    """Validation must not break genuinely optional lookups."""
    from configuration import config
    assert config.get("definitely_not_a_real_key") is None
    assert config.get("definitely_not_a_real_key", 42) == 42


def test_unused_keys_really_are_unused(project_root):
    """
    The seven keys nothing reads. Six configure the Tax-Optimal Split
    Optimizer, which runs but takes its parameters from elsewhere — so editing
    `optimizer_default_user_cap` in the config file changes nothing, silently.

    Recorded rather than deleted: whether the optimiser ought to read them is
    a decision for whoever owns that feature. This test fails the day one of
    them acquires a reader, which is the moment to move it to REQUIRED_KEYS.
    """
    from configuration import REQUIRED_KEYS, UNUSED_KEYS

    assert not (REQUIRED_KEYS & UNUSED_KEYS), "a key cannot be both"

    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in project_root.glob("*.py") if p.name != "configuration.py"
    )
    now_used = sorted(k for k in UNUSED_KEYS if k in sources)
    assert not now_used, (
        f"{now_used} are listed as unused but something now reads them. Move "
        "them into REQUIRED_KEYS so a missing value fails at startup.")


# ── the FD tax benchmark ladder ──────────────────────────────────────────────

def test_fd_ladder_is_three_descending_parts_of_the_horizon():
    """Three equal parts, earliest first, rates falling across the plan.

    A later chunk carrying a higher rate would claim more confidence about
    year 30 than about year 1, which is backwards.
    """
    out = cd.default_fd_benchmark_chunks(30, rates=[0.053, 0.045, 0.036],
                                         spread=0.01)
    assert [(a, b) for a, b, _ in out] == [(1, 10), (11, 20), (21, 30)]
    rates = [r for _a, _b, r in out]
    assert rates == sorted(rates, reverse=True)
    assert rates[0] == pytest.approx(0.063)


def test_fd_ladder_shares_the_layout_with_the_allocation_options():
    """'Three equal parts' must have one definition, not two."""
    for horizon in (30, 25, 20, 15, 10):
        fd = [(a, b) for a, b, _ in
              cd.default_fd_benchmark_chunks(horizon, rates=[0.05, 0.04, 0.03],
                                             spread=0.0)]
        assert fd == cd.default_chunk_layout(3, horizon)


def test_fd_ladder_covers_every_year_with_no_gap_or_overlap():
    for horizon in (30, 23, 17, 11, 10):
        out = cd.default_fd_benchmark_chunks(horizon, rates=[0.05, 0.04, 0.03],
                                             spread=0.0)
        covered = []
        for a, b, _ in out:
            covered.extend(range(a, b + 1))
        assert covered == list(range(1, horizon + 1)), (
            f"horizon {horizon}: {out} leaves a gap or overlaps")


def test_fd_ladder_refuses_rates_that_do_not_descend():
    with pytest.raises(ValueError, match="descend"):
        cd.default_fd_benchmark_chunks(30, rates=[0.03, 0.04, 0.05],
                                       spread=0.01)


def test_fd_ladder_reads_the_configured_rates_and_spread():
    from configuration import config
    out = cd.default_fd_benchmark_chunks(30)
    expected = [config.risk_free_rate, config.risk_free_rate_mid,
                config.risk_free_rate_low]
    for (_a, _b, got), base in zip(out, expected):
        assert got == pytest.approx(base + config.fd_benchmark_spread)
