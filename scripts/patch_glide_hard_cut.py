#!/usr/bin/env python3
"""
Issue #1 — build_glide_path: spread_years=0 does not produce a hard cut.

glide_path.build_glide_path() did `spread = max(2, spread_years)`, forcing a
minimum 2-year interpolation window. spread_years of 0, 1 and 2 all produced
identical schedules, so a hard cut at the chunk boundary was unreachable:
with chunks [1-15] and [16-30], year 15 came out 50/50 instead of 100% equity.

Fix: short-circuit when spread_years <= 1 and give every year its owning
chunk's weights. spread_years >= 2 is untouched.

Safe to run twice. Preserves existing line endings.
"""
import shutil, sys
from pathlib import Path

TARGET = Path("glide_path.py")
OLD = "    spread = max(2, spread_years)\n"
NEW = '''    # spread_years <= 1 means a hard cut at each boundary: no transition
    # window, every year simply carries its owning chunk's weights.
    # (Previously max(2, spread_years) forced a 2-year ramp, so 0, 1 and 2 all
    # produced identical schedules and a hard cut was unreachable.)
    if spread_years <= 1:
        schedule = {}
        for y in range(1, 31):
            for c in chunks:
                if c.year_from <= y <= c.year_to:
                    schedule[y] = _effective_weights(c)
                    break
            else:
                schedule[y] = _effective_weights(chunks[-1])
        return GlidePath(schedule=schedule)

    spread = spread_years
'''

def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run from the project root.")
        return 1
    raw = TARGET.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if "spread_years <= 1" in text:
        print("Already patched — nothing to do.")
        return 0
    if OLD not in text:
        print("ERROR: could not find `spread = max(2, spread_years)`.")
        return 1
    text = text.replace(OLD, NEW, 1)
    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak"))
    TARGET.write_bytes((text.replace("\n", nl) if nl != "\n" else text).encode("utf-8"))
    print(f"Patched {TARGET} — spread_years <= 1 now cuts hard")
    return 0

if __name__ == "__main__":
    sys.exit(main())
