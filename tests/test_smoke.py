"""
Phase 0 smoke tests — the two failure modes a fresh clone hits.

  1. RetirementTaxPlanning.configuration untracked => `import engine` raises
     AttributeError at module scope (CESS = config.cess_rate).
  2. test_swp_engine.py imports names models.py no longer exports.
"""
import importlib

import pytest

REQUIRED_CONFIG_KEYS = [
    "allocator_default_input", "allocator_default_output",
    "amfi_sleep_between_calls", "cess_rate", "drift_cap_huf",
    "drift_cap_personal", "exit_load_fraction", "fallback_debt_top_rate",
    "fallback_equity_ltcg_rate", "fallback_other_ltcg_rate",
    "fallback_stcg_rate", "max_simulation_years",
    "smart_withdrawal_start_month", "stcg_holding_months",
    "swp_start_month", "weight_drift_threshold",
]

HEADLESS_MODULES = ["configuration", "models", "engine", "glide_path"]

GUI_MODULES = ["chunk_editor", "tax_dialog", "fund_dialog", "chart_dialog",
               "dialogs", "optimization_report", "main"]


def test_configuration_file_is_tracked(config_path):
    assert config_path.exists(), (
        f"{config_path.name} is missing. engine.py does "
        "`CESS = config.cess_rate` at module scope, so without this file the "
        "package cannot be imported at all. It contains no secrets — commit it."
    )


@pytest.mark.parametrize("key", REQUIRED_CONFIG_KEYS)
def test_configuration_key_present(key):
    from configuration import config
    assert config.get(key, None) is not None, f"Missing configuration key: {key}"


@pytest.mark.parametrize("module", HEADLESS_MODULES)
def test_headless_module_imports(module):
    importlib.import_module(module)


@pytest.mark.parametrize("module", GUI_MODULES)
def test_gui_module_imports(module):
    pytest.importorskip("PySide6")
    importlib.import_module(module)


def test_models_exports_names_used_by_test_suite():
    """
    _make_state() in test_swp_engine.py references PersonalIncome, HUFIncome,
    RequirementChunk and four DEFAULT_*_CHUNKS factories that no longer exist.
    This guard pins the names that DO exist so they stay in sync.
    """
    import models
    required = ["AppState", "AllocationChunk", "TaxChunk", "TaxSlab",
                "EquityTaxChunk", "OtherTaxChunk", "ReturnChunk", "SplitChunk",
                "FDRateChunk", "FundEntry", "GlidePath", "OtherIncome",
                "WindfallEntry", "HUFWithdrawalChunk", "RebalanceCost"]
    missing = [n for n in required if not hasattr(models, n)]
    assert not missing, f"models.py is missing: {missing}"


def test_duplicate_optimiser_is_not_tracked(project_root):
    """allocate_funds_v0.9.py may sit on disk (gitignored) but must never be committed."""
    import subprocess
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "allocate_funds_v0.9.py", "older_versions/"],
            cwd=project_root, capture_output=True, text=True, timeout=15,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    assert not tracked, f"Superseded copies are tracked: {tracked}"
