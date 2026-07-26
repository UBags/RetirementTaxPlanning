# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Configuration.py
================
Singleton configuration reader for the SWP Financial Planner.

Reads key-value pairs from ``RetirementTaxPlanning.configuration`` located
alongside this file (project root).  Values are auto-parsed into int, float,
or str.

Usage
-----
    from configuration import config, get_project_root

    cess = config.cess_rate                   # float 0.04
    path = get_project_root() / "some_file"   # Path
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

# ─── Project root ─────────────────────────────────────────────────────────────

def get_project_root() -> Path:
    """Return the project root directory (the directory containing this file)."""
    return Path(os.path.dirname(os.path.abspath(__file__)))


# ─── Configuration singleton ──────────────────────────────────────────────────

_CONFIG_FILENAME = "RetirementTaxPlanning.configuration"

# Every key the application reads at runtime.  Validated as a set when the
# file loads, so a missing key is one error at startup naming all of them,
# not an AttributeError from whatever line happened to touch it first.
#
# This list lives here, beside the loader, because tests/test_smoke.py used to
# keep its own copy and the copy drifted: it named 16 keys while the file held
# 23.  A copy of a list is not a check on the list.  test_smoke.py now imports
# this one.
#
# ADDING A KEY: add it here and to RetirementTaxPlanning.configuration in the
# same commit.  The suite will tell you if you forget the second half.
REQUIRED_KEYS = frozenset({
    # Tax
    "cess_rate",
    "fallback_equity_ltcg_rate",
    "fallback_other_ltcg_rate",
    "fallback_debt_top_rate",
    "fallback_stcg_rate",
    # Fund / portfolio
    "stcg_holding_months",
    "exit_load_fraction",
    "swp_start_month",
    "smart_withdrawal_start_month",
    "max_simulation_years",
    # Bounded smart withdrawal
    "drift_cap_personal",
    "drift_cap_huf",
    "weight_drift_threshold",
    # AMFI
    "amfi_sleep_between_calls",
    # File paths
    "allocator_default_input",
    "allocator_default_output",
    # Allocation-chunk defaults (HANDOVER 2.4).  The through-the-cycle liquid
    # fund yield, written by the fund importer -- see percentile_metrics.py.
    "risk_free_rate",
    # Tunables for those metrics.  Two separate percentiles on purpose: they
    # are equal today and are not the same decision.  Lowering the first
    # loosens the MILP return floors; lowering the second lowers the
    # projection itself.
    "risk_free_rate_percentile",
    "fund_return_percentile",
    "risk_free_min_funds",
    # The FD tax benchmark ladder.  The benchmark is a notional "what if it
    # had all sat in fixed deposits" comparator, and a single flat rate for
    # thirty years asserts more certainty about year 30 than about year 1.
    # Three measured percentiles of the same liquid-fund series, plus a
    # spread, give the later chunks a lower and less confident rate.
    #
    # _mid_ and _low_ are the percentiles; the measured RATES they produce
    # are risk_free_rate_mid / risk_free_rate_low, written back by
    # get_funds_data.py exactly as risk_free_rate is.
    "risk_free_rate_mid_percentile",
    "risk_free_rate_low_percentile",
    "risk_free_rate_mid",
    "risk_free_rate_low",
    "fd_benchmark_spread",
})

# Keys present in RetirementTaxPlanning.configuration that NO module reads.
#
# Six of them configure the Tax-Optimal Split Optimizer, which exists and runs
# but does not take its parameters from here -- editing them changes nothing,
# silently.  They are not validated (requiring a key nothing reads is
# pointless) and not deleted from the file, because deciding whether the
# optimiser ought to read them belongs to whoever owns that feature.
#
# Verified by test_no_required_key_is_silently_unused, which greps every
# module.  If one of these acquires a reader, move it into REQUIRED_KEYS.
UNUSED_KEYS = frozenset({
    "default_cagr_fallback",
    "optimizer_default_user_cap",
    "optimizer_min_personal_fraction",
    "optimizer_max_huf_corpus_drain",
    "optimizer_grid_step",
    "optimizer_default_iterations",
    "rebalance_no_trade_band",
})


class _Configuration:
    """
    Singleton that lazily loads the configuration properties file.

    Access any property as an attribute::

        config.cess_rate          # 0.04
        config.stcg_holding_months  # 12
        config.allocator_default_input  # "Fund_Metrics_Output.csv"

    If a key is not found, ``AttributeError`` is raised.
    """

    _instance: Optional["_Configuration"] = None
    _loaded: bool = False
    _data: Dict[str, Any] = {}

    def __new__(cls) -> "_Configuration":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── Loading ───────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        config_path = get_project_root() / _CONFIG_FILENAME
        if not config_path.exists():
            # There are no built-in defaults.  The previous code warned and
            # continued, leaving _data empty, so the next attribute access
            # raised AttributeError with no mention of this file -- and since
            # engine.py does `CESS = config.cess_rate` at module scope, that
            # surfaced as an unexplained import failure.
            self._loaded = False
            raise FileNotFoundError(
                f"Configuration file not found: {config_path}\n"
                "There are no built-in defaults. The application cannot start "
                "without it. Restore it from git:\n"
                f"    git checkout -- {_CONFIG_FILENAME}"
            )
        self._data = _parse_properties(config_path)

        missing = sorted(REQUIRED_KEYS - self._data.keys())
        if missing:
            # All of them, once.  Reporting the first only would mean one
            # edit-run cycle per missing key.
            self._loaded = False
            raise KeyError(
                f"{config_path} is missing {len(missing)} required "
                f"key(s):\n    " + "\n    ".join(missing) +
                "\n\nNo defaults are supplied: a plausible-looking wrong tax "
                "rate produces a plausible-looking wrong plan. Add the keys "
                "listed above, or remove them from configuration.REQUIRED_KEYS "
                "if they are genuinely no longer read."
            )

    # ── Attribute access ──────────────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        self._ensure_loaded()
        if name in self._data:
            return self._data[name]
        raise AttributeError(
            f"Configuration key '{name}' not found in {_CONFIG_FILENAME}"
        )

    def get(self, name: str, default: Any = None) -> Any:
        """Return a configuration value, or *default* if not found."""
        self._ensure_loaded()
        return self._data.get(name, default)

    def reload(self) -> None:
        """Force re-read of the configuration file (useful for testing)."""
        self._loaded = False
        self._data = {}
        self._ensure_loaded()


def set_value(key: str, value: object, path: "Path | None" = None) -> None:
    """Rewrite one `key = value` line in the properties file, in place.

    Everything else survives byte-for-byte: comments, blank lines, the
    box-drawing section rules, and the ordering.  A naive rewrite that dumped
    the parsed dict back out would lose all of it, and those comments are the
    only documentation several of these keys have.

    Refuses to CREATE a key.  A caller with a typo should fail loudly rather
    than quietly add `risk_fre_rate` beside the real one and leave the real
    one stale forever.

    The in-memory singleton is refreshed afterwards, so a process that writes
    a value sees it immediately rather than the value it started with.
    """
    cfg_path = Path(path) if path is not None else get_project_root() / _CONFIG_FILENAME
    lines = cfg_path.read_text(encoding="utf-8").splitlines(keepends=True)

    hit = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.split("=", 1)[0].strip() == key:
            hit = i
            break

    if hit is None:
        raise KeyError(
            f"{key!r} is not present in {cfg_path.name}. set_value() rewrites "
            "existing keys only -- add it to the file and to REQUIRED_KEYS "
            "first, in the same commit."
        )

    # Preserve the original alignment: `foo      = 1` keeps its padding.
    lhs = lines[hit].split("=", 1)[0]
    eol = "\n" if lines[hit].endswith("\n") else ""
    lines[hit] = f"{lhs}= {value}{eol}"
    cfg_path.write_text("".join(lines), encoding="utf-8")

    config.reload()


def _parse_properties(path: Path) -> Dict[str, Any]:
    """
    Parse a simple ``key = value`` properties file.

    * Lines starting with ``#`` are comments.
    * Blank lines are ignored.
    * Values are auto-parsed: int → float → bool → str.
    """
    data: Dict[str, Any] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, raw_val = line.partition("=")
            key = key.strip()
            raw_val = raw_val.strip()
            data[key] = _auto_cast(raw_val)
    return data


def _auto_cast(val: str) -> Any:
    """Attempt int, then float, then bool, then return as str."""
    # Boolean
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    # Integer (only if no decimal point)
    if "." not in val:
        try:
            return int(val)
        except ValueError:
            pass
    # Float
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ── Module-level singleton ────────────────────────────────────────────────────
config = _Configuration()
