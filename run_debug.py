"""
Debug launcher for RetirementTaxPlanning.
Sets SWP_DEBUG_LOG env var so engine, main, and models write trace logs.

Usage:
    cd C:\PythonProjects\RetirementTaxPlanning
    python run_debug.py

Log file: swp_debug.log (same directory)
"""
import os, sys, logging

# ── Set log path BEFORE importing anything else ──
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "swp_debug.log")
os.environ["SWP_DEBUG_LOG"] = LOG_FILE

# Clear previous log
with open(LOG_FILE, "w", encoding="utf-8") as f:
    f.write(f"=== SWP Debug Log ===\n")
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
cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
if os.path.isdir(cache_dir):
    shutil.rmtree(cache_dir)
    print(f"Deleted {cache_dir}")

print(f"Debug logging to: {LOG_FILE}")
print("Starting app...\n")

# Now import and run the app
from main import main
main()