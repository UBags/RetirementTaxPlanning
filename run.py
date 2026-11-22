#!/usr/bin/env python3
"""Launch the SWP Financial Planner GUI from a checkout without installing.

Equivalent to ``python -m swp_planner`` after ``pip install -e .``. Kept at the
root so existing IDE run configurations that point at run.py keep working.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from swp_planner.gui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
