"""
Debug launcher for RetirementTaxPlanning.
Sets SWP_DEBUG_LOG env var so engine, main, and models write trace logs.

Usage:
    python scripts/run_debug.py

Log file: data/logs/swp_debug.log
"""
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

# ── Set log path BEFORE importing anything else ──
LOG_FILE = os.path.join(_ROOT, "data", "logs", "swp_debug.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.environ["SWP_DEBUG_LOG"] = LOG_FILE

# Clear previous log
with open(LOG_FILE, "w", encoding="utf-8") as f:
    f.write("=== SWP Debug Log ===\n")
    f.write(f"Started: {__import__('datetime').datetime.now()}\n")
    f.write(f"Python: {sys.executable}\n")
    f.write(f"CWD: {os.getcwd()}\n\n")

# Set up root loggers for models.debug and engine.debug too
for name in ("main.debug", "engine.debug", "models.debug"):
    lg = logging.getLogger(name)
    fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(fh)
    lg.setLevel(logging.DEBUG)

# Delete __pycache__ to ensure fresh bytecode
import shutil

for _dirpath, _dirnames, _ in os.walk(os.path.join(_ROOT, "src", "swp_planner")):
    if "__pycache__" in _dirnames:
        cache_dir = os.path.join(_dirpath, "__pycache__")
        shutil.rmtree(cache_dir)
        print(f"Deleted {cache_dir}")

print(f"Debug logging to: {LOG_FILE}")
print("Starting app...\n")

# Now import and run the app
from swp_planner.gui.main import main

main()