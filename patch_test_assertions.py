#!/usr/bin/env python3
"""
Phase 0 — make test_swp_engine.py's _check() actually fail.

Today _check() only prints, so under pytest a failing condition still reports
green. This rewrites it to raise AssertionError, while keeping the standalone
runner's "collect everything and summarise" behaviour via _COLLECT_MODE.

Safe to run twice. Preserves existing line endings (this file is CRLF on disk).
"""
import shutil
import sys
from pathlib import Path

TARGET = Path("test_swp_engine.py")


def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run this from the project root.")
        return 1

    raw = TARGET.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")

    if any("_COLLECT_MODE" in ln for ln in lines):
        print("Already patched — nothing to do.")
        return 0

    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("def _check("))
        end = next(i for i in range(start, len(lines))
                   if "_results.append((name, condition))" in lines[i])
    except StopIteration:
        print("ERROR: could not locate _check(). Has the file been edited?")
        return 1

    lines[start:end + 1] = [
        "_COLLECT_MODE = False   # True only for the standalone runner (see run_all)",
        "",
        'def _check(name: str, condition: bool, detail: str = ""):',
        "    condition = bool(condition)",
        "    sym = PASS if condition else FAIL",
        '    msg = f"  {sym}  {name}"',
        "    if detail:",
        '        msg += f"  [{detail}]"',
        "    print(msg)",
        "    _results.append((name, condition))",
        "    if not condition and not _COLLECT_MODE:",
        '        raise AssertionError(f"{name}: {detail}" if detail else name)',
    ]

    try:
        r = next(i for i, ln in enumerate(lines) if ln.startswith("def run_all("))
    except StopIteration:
        print("ERROR: could not locate run_all().")
        return 1

    lines[r + 1:r + 1] = [
        "    global _COLLECT_MODE",
        "    _COLLECT_MODE = True      # collect all results instead of aborting",
    ]

    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak"))
    TARGET.write_bytes(newline.join(lines).encode("utf-8"))
    print(f"Patched {TARGET} (backup: {TARGET}.bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
