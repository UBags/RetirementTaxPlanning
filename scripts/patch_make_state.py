#!/usr/bin/env python3
"""
Phase 1.0 — bring test_swp_engine.py's factories up to the current models.py.

    PersonalIncome / HUFIncome      -> OtherIncome (one class, both entities)
    RequirementChunk                -> AppState.annual_requirements: Dict[int, float]
    Windfall                        -> WindfallEntry(year, amount, target)
    DEFAULT_*_CHUNKS (x5)           -> built by models.default_state()
    SplitChunk(debt, equity, other) -> SplitChunk(year_from, year_to, debt_ratio)

_make_state() now starts from models.default_state() -- which already builds
all six tax-chunk lists -- and overrides only what a test needs, so it tracks
future models.py changes instead of drifting again.

Safe to run twice. Preserves existing line endings.
"""
import shutil
import sys
from pathlib import Path

TARGET = Path("test_swp_engine.py")

NEW_MAKE_STATE = '''def _make_state(funds=None, total_alloc=100.0, req_annual=8.0,
                inv_date=None):
    """Create a minimal AppState with sensible defaults.

    Built on models.default_state() so the six tax-chunk lists stay in sync
    with the application rather than being duplicated here.

    ``total_alloc`` is accepted for signature compatibility but unused --
    each FundEntry carries its own ``allocation``.
    """
    from models import (AllocationChunk, ReturnChunk, FDRateChunk,
                        OtherIncome, default_state)

    state = default_state()
    state.investment_date = inv_date or date(2024, 4, 1)

    if funds is None:
        funds = [_make_fund()]

    state.funds = list(funds)

    chunk = AllocationChunk(year_from=1, year_to=30, funds=list(funds))
    chunk.min_return   = 0.065
    chunk.max_std_dev  = 0.04
    chunk.max_dd       = 0.03
    chunk.max_per_fund = 0.40
    chunk.min_per_fund = 0.01
    state.allocation_chunks = [chunk]

    # annual_requirements is a Dict[int, float], not a list of chunks
    state.annual_requirements = {y: req_annual for y in range(1, 31)}

    state.return_chunks = [ReturnChunk(year_from=1, year_to=30,
                                       annual_return=0.07)]

    # Left empty: the engine derives debt/equity/other split from the actual
    # fund allocations, which is what these tests vary.
    state.split_chunks = []

    state.personal_income = OtherIncome()
    state.huf_income      = OtherIncome()
    state.windfalls       = []
    state.huf_withdrawal_chunks = []
    state.huf_annual_requirements = {}

    state.fd_rate_chunks = [FDRateChunk(year_from=1, year_to=30, fd_rate=0.07)]
    state.fd_rate = 0.07
    state.allocation_mode = "singular"
    state.rebalance_spread_years = 4
    state.glide_path = None

    return state
'''


def _replace_block(lines, start_pred, end_pred, replacement, what):
    try:
        start = next(i for i, ln in enumerate(lines) if start_pred(ln))
    except StopIteration:
        print(f"  ! could not find start of {what}")
        return lines, False
    try:
        end = next(i for i in range(start + 1, len(lines)) if end_pred(lines[i]))
    except StopIteration:
        print(f"  ! could not find end of {what}")
        return lines, False
    lines[start:end] = replacement.split("\n")
    print(f"  - {what}: replaced lines {start + 1}..{end}")
    return lines, True


def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run from the project root.")
        return 1

    raw = TARGET.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")

    if any("models.default_state() so the six tax-chunk" in ln for ln in lines):
        print("Already patched — nothing to do.")
        return 0

    changed = 0

    lines, ok = _replace_block(
        lines,
        lambda ln: ln.startswith("def _make_state("),
        lambda ln: ln.startswith("# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500") or ln.startswith("def _run_engine("),
        NEW_MAKE_STATE,
        "_make_state()",
    )
    changed += ok

    for i, ln in enumerate(lines):
        if "from models import RequirementChunk" in ln and "AllocationChunk" not in ln:
            lines[i] = None
            changed += 1
            print("  - corpus_depletion: dropped RequirementChunk import")
            break
    for i, ln in enumerate(lines):
        if ln is not None and "state.annual_requirements = [RequirementChunk(" in ln:
            indent = ln[: len(ln) - len(ln.lstrip())]
            lines[i] = indent + "state.annual_requirements = {y: 20.0 for y in range(1, 31)}"
            changed += 1
            print("  - corpus_depletion: annual_requirements now a dict")
            break

    for i, ln in enumerate(lines):
        if ln is not None and "from models import AllocationChunk, RequirementChunk" in ln:
            lines[i] = ln.replace("AllocationChunk, RequirementChunk,", "AllocationChunk,")
            changed += 1
            print("  - rebalancing_tax: dropped RequirementChunk import")
            break

    for i, ln in enumerate(lines):
        if ln is not None and "from models import Windfall" in ln and "WindfallEntry" not in ln:
            lines[i] = ln.replace("import Windfall", "import WindfallEntry")
            changed += 1
            print("  - windfall: Windfall -> WindfallEntry")
            break
    for i, ln in enumerate(lines):
        if ln is not None and "state.windfalls = [Windfall(" in ln:
            lines[i] = ln.replace("[Windfall(", "[WindfallEntry(")
            changed += 1
            break

    for i, ln in enumerate(lines):
        if ln is not None and "Windfall in Final Year" in ln:
            for j in range(i, min(i + 12, len(lines))):
                if lines[j] is not None and "_, yearly, _ = _run_engine(state)" in lines[j]:
                    lines[j] = lines[j].replace("_, yearly, _ =", "p_rows, yearly, _ =")
                    changed += 1
                    print("  - windfall: bind p_rows (was checking HUF rows)")
                if lines[j] is not None and "for r in _ or []" in lines[j]:
                    lines[j] = lines[j].replace("for r in _ or []", "for r in p_rows or []")
                    changed += 1
            break

    lines = [ln for ln in lines if ln is not None]

    if not changed:
        print("Nothing matched — file may already differ from the expected shape.")
        return 1

    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak"))
    TARGET.write_bytes(newline.join(lines).encode("utf-8"))
    print(f"\nPatched {TARGET} ({changed} edits, backup: {TARGET}.bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
