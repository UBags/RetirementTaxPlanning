"""
`configuration.set_value()` and the risk_free_rate write-back.

The decision being implemented: **write on import, and inform.** The user does
not type the rate and does not edit it. It remains STORED rather than computed
at read time — a value that moved every time a plan was opened would make
saved plans irreproducible — but the code puts it there, not a human reading
yields off a spreadsheet.

`get_funds_data.py` fetches NAV history from AMFI, so the measurement itself is
driven here with synthetic series and the rewriter is driven directly against a
temp file. What is NOT covered is a real run; that is stated in the handover.
"""
from __future__ import annotations

import pytest

from swp_planner.simulation import percentile_metrics as pm

CFG = "config/RetirementTaxPlanning.configuration"


def _full_config(tmp_path, project_root):
    """A COMPLETE config in tmp_path.

    Phase 2.2 validation means set_value()'s reload() rejects any file missing
    a required key, so the write-back tests cannot use a two-key stub — that
    is the validation doing its job, not a test problem.
    """
    src = project_root / CFG
    dst = tmp_path / CFG
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _mini_config(tmp_path, body=None):
    p = tmp_path / CFG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body or (
        "# ─── Tax Constants ───\n"
        "# Health & education cess\n"
        "cess_rate = 0.04\n"
        "\n"
        "# Risk-free: liquid-fund yield\n"
        "risk_free_rate = 0.0665\n"
        "\n"
        "# trailing comment\n"
    ), encoding="utf-8")
    return p


# ── the rewriter ─────────────────────────────────────────────────────────────

def test_set_value_changes_only_the_target_line(tmp_path):
    from swp_planner.config import configuration as cfg
    p = _mini_config(tmp_path)
    before = p.read_text(encoding="utf-8").splitlines()

    cfg.set_value("risk_free_rate", 0.055, path=p)

    after = p.read_text(encoding="utf-8").splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after), "line count changed"
    assert len(changed) == 1, f"expected one changed line, got {changed}"
    assert changed[0][1].strip() == "risk_free_rate = 0.055"


def test_set_value_preserves_comments_and_blank_lines(tmp_path):
    """
    A rewrite that dumped the parsed dict back out would lose all of it — and
    those comments are the only documentation several of these keys have.
    """
    from swp_planner.config import configuration as cfg
    p = _mini_config(tmp_path)
    cfg.set_value("risk_free_rate", 0.055, path=p)
    text = p.read_text(encoding="utf-8")

    assert "# ─── Tax Constants ───" in text
    assert "# Health & education cess" in text
    assert "# Risk-free: liquid-fund yield" in text
    assert "# trailing comment" in text
    assert "cess_rate = 0.04" in text
    assert text.count("risk_free_rate") == 1, "the key was duplicated"


def test_set_value_refuses_to_create_a_key(tmp_path):
    """A typo should fail loudly, not quietly add `risk_fre_rate` beside the
    real one and leave the real one stale forever."""
    from swp_planner.config import configuration as cfg
    p = _mini_config(tmp_path)
    with pytest.raises(KeyError, match="not present"):
        cfg.set_value("risk_fre_rate", 0.055, path=p)
    assert "risk_fre_rate" not in p.read_text(encoding="utf-8")


def test_set_value_ignores_the_key_name_inside_comments(tmp_path):
    from swp_planner.config import configuration as cfg
    p = _mini_config(tmp_path, body=(
        "# risk_free_rate = 0.09  <- an old value, commented out\n"
        "risk_free_rate = 0.0665\n"
    ))
    cfg.set_value("risk_free_rate", 0.055, path=p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# risk_free_rate = 0.09"), "a comment was rewritten"
    assert lines[1].strip() == "risk_free_rate = 0.055"


def test_set_value_round_trips(tmp_path):
    from swp_planner.config import configuration as cfg
    p = _mini_config(tmp_path)
    original = p.read_text(encoding="utf-8")
    cfg.set_value("risk_free_rate", 0.055, path=p)
    cfg.set_value("risk_free_rate", 0.0665, path=p)
    assert p.read_text(encoding="utf-8") == original


def test_set_value_is_readable_by_the_parser(tmp_path):
    from swp_planner.config import configuration as cfg
    p = _mini_config(tmp_path)
    cfg.set_value("risk_free_rate", 0.055, path=p)
    assert cfg._parse_properties(p)["risk_free_rate"] == pytest.approx(0.055)


# ── the measurement, end to end ──────────────────────────────────────────────

def _rec(name, cagrs, dates):
    return {"Fund Name": name,
            "_rolling_series": {"3Y": {"cagrs": cagrs, "cagr_dates": dates}}}


def _liquid_corpus(n_funds=12, n_months=60, base=5.5):
    """n liquid funds sharing one rate cycle, dates aligned."""
    import math
    dates = [f"20{15 + m // 12:02d}-{m % 12 + 1:02d}-28" for m in range(n_months)]
    recs, types = [], {}
    for f in range(n_funds):
        cagrs = [base + 1.5 * math.cos(2 * math.pi * m / n_months) + f * 0.02
                 for m in range(n_months)]
        name = f"ACME {f} Liquid Fund"
        recs.append(_rec(name, cagrs, dates))
        types[name] = "Debt Scheme - Liquid Fund"
    return recs, types


class _DF:
    """Minimal stand-in for the df_out columns _write_risk_free_rate reads."""
    def __init__(self, types):
        self._t = types
        self.columns = ["Fund Name", "Fund Type"]

    def get(self, key, default=None):
        return list(self._t.values()) if key == "Fund Type" else default

    def __getitem__(self, key):
        return list(self._t) if key == "Fund Name" else list(self._t.values())


def _tunables():
    """Stand-in for percentile_metrics.configured().

    Every key that function returns must appear here. This was five inline
    copies; adding the ladder percentiles to configured() broke all five at
    once, which is the drift REQUIRED_KEYS exists to prevent and which a copy
    of a dict reintroduces. Asserted against the real thing below.
    """
    return {
        "risk_free_rate_percentile": 33.0,
        "risk_free_rate_mid_percentile": 20.0,
        "risk_free_rate_low_percentile": 5.0,
        "fund_return_percentile": 33.0,
        "risk_free_min_funds": 10,
    }


def test_the_stub_matches_the_real_configured_signature():
    """A stub missing a key would make every test below pass vacuously."""
    assert set(_tunables()) == set(pm.configured()), (
        "_tunables() has drifted from percentile_metrics.configured(); "
        "add the missing key here as well")


def test_writeback_computes_and_writes(tmp_path, monkeypatch, project_root):
    from swp_planner.config import configuration as cfg
    from swp_planner.funddata.get_funds_data import _write_risk_free_rate

    p = _full_config(tmp_path, project_root)
    monkeypatch.setattr(cfg, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(pm, "configured", _tunables)

    recs, types = _liquid_corpus()
    _write_risk_free_rate(recs, _DF(types))

    written = cfg._parse_properties(p)["risk_free_rate"]
    assert 0.01 < written < 0.15, f"implausible rate written: {written}"
    # P33 of a cycle centred on 5.5% sits below the mean
    assert written < 0.055, f"P33 landed at or above the centre: {written}"


def test_writeback_refuses_when_too_few_liquid_funds(tmp_path, monkeypatch, project_root):
    """Overwriting a working config with a number built from four funds is
    worse than leaving it alone."""
    from swp_planner.config import configuration as cfg
    from swp_planner.funddata.get_funds_data import _write_risk_free_rate

    p = _full_config(tmp_path, project_root)
    before = cfg._parse_properties(p)['risk_free_rate']
    monkeypatch.setattr(cfg, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(pm, "configured", _tunables)

    recs, types = _liquid_corpus(n_funds=4)
    _write_risk_free_rate(recs, _DF(types))
    assert cfg._parse_properties(p)["risk_free_rate"] == pytest.approx(before)


def test_writeback_ignores_funds_that_are_not_liquid(tmp_path, monkeypatch, project_root):
    from swp_planner.config import configuration as cfg
    from swp_planner.funddata.get_funds_data import _write_risk_free_rate

    p = _full_config(tmp_path, project_root)
    before = cfg._parse_properties(p)['risk_free_rate']
    monkeypatch.setattr(cfg, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(pm, "configured", _tunables)

    recs, types = _liquid_corpus(n_funds=12)
    for name in types:
        types[name] = "Debt Scheme - Corporate Bond Fund"
    _write_risk_free_rate(recs, _DF(types))
    assert cfg._parse_properties(p)["risk_free_rate"] == pytest.approx(before), (
        "corporate bond funds were treated as liquid")


def test_writeback_refuses_an_implausible_rate(tmp_path, monkeypatch, project_root):
    from swp_planner.config import configuration as cfg
    from swp_planner.funddata.get_funds_data import _write_risk_free_rate

    p = _full_config(tmp_path, project_root)
    before = cfg._parse_properties(p)['risk_free_rate']
    monkeypatch.setattr(cfg, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(pm, "configured", _tunables)

    recs, types = _liquid_corpus(base=45.0)      # 45% "liquid" yield
    _write_risk_free_rate(recs, _DF(types))
    assert cfg._parse_properties(p)["risk_free_rate"] == pytest.approx(before), (
        "a 45% rate was accepted; the plausibility guard is not firing")


def test_writeback_skips_series_whose_dates_do_not_match(tmp_path, monkeypatch, project_root):
    """
    Mismatched lengths mean the CAGR/date pairing is wrong, and a wrong pairing
    silently averages the wrong calendar months. Skip, do not zip-and-hope.
    """
    from swp_planner.config import configuration as cfg
    from swp_planner.funddata.get_funds_data import _write_risk_free_rate

    p = _full_config(tmp_path, project_root)
    before = cfg._parse_properties(p)['risk_free_rate']
    monkeypatch.setattr(cfg, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(pm, "configured", _tunables)

    recs, types = _liquid_corpus(n_funds=12)
    for r in recs:
        r["_rolling_series"]["3Y"]["cagr_dates"] = \
            r["_rolling_series"]["3Y"]["cagr_dates"][:-3]
    _write_risk_free_rate(recs, _DF(types))
    assert cfg._parse_properties(p)["risk_free_rate"] == pytest.approx(before)


def test_writeback_never_aborts_the_analyser_run(tmp_path, monkeypatch):
    """
    The measurement is a side benefit of the run, not its purpose. A failure
    here must not lose an hour of NAV fetching.
    """
    from swp_planner.funddata.get_funds_data import _write_risk_free_rate
    monkeypatch.setattr(pm, "configured",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        _write_risk_free_rate([], None)      # caller wraps this in try/except
