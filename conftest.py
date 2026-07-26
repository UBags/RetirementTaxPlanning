"""
Root pytest configuration.

  1. Put the project root on sys.path so `import engine` works from tests/.
  2. Force headless Qt before PySide6 is imported anywhere, so CI (and any
     SSH session) can import the dialog modules without an X server.
  3. Block outbound TCP for the whole session (Phase 1.2), so a test cannot
     silently depend on api.mfapi.in or portal.amfiindia.com being reachable.
     Opt out with @pytest.mark.network.
  4. Redirect monte_carlo's NAV caches into a session tmpdir, so a test run
     neither reads nor overwrites the owner's mc_nifty50_nav.csv.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def config_path(project_root: Path) -> Path:
    return project_root / "RetirementTaxPlanning.configuration"


# ── Phase 1.2: network isolation ──────────────────────────────────────
# monte_carlo._fetch_nifty50_annual_returns() hits api.mfapi.in and then the
# AMFI portal, and swallows every exception on the way, so an unreachable
# network degrades to a log-normal fallback rather than a red test. That makes
# "did this run use real history?" invisible, and makes MC output depend on
# whether the machine happened to be online. Block it and be explicit.
#
# AF_UNIX is left alone on purpose: headless Qt and DBus need it.

_NETWORK_DENIED = (
    "Outbound TCP blocked during tests (Phase 1.2). Something tried to reach "
    "the network — most likely monte_carlo's Nifty 50 NAV fetch. A test that "
    "depends on the internet is not reproducible between this machine and CI. "
    "Use a frozen fixture, or mark the test @pytest.mark.network."
)


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Deny AF_INET / AF_INET6 connects unless the test is marked `network`."""
    if request.node.get_closest_marker("network"):
        return

    import socket

    _real_connect = socket.socket.connect

    def _guarded_connect(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError(_NETWORK_DENIED)
        return _real_connect(self, address, *args, **kwargs)

    def _denied(*args, **kwargs):
        raise RuntimeError(_NETWORK_DENIED)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket, "create_connection", _denied)


# ── Phase 1.2: cache isolation ───────────────────────────────────────
# _EQUITY_CACHE_CSV / _DEBT_CACHE_CSV are module-level Paths beside the source.
# Left alone, a test run reads whatever the owner last downloaded (so results
# depend on the age of a file that is not in git) and _save_equity_cache()
# overwrites it. Point both somewhere disposable for the session.


@pytest.fixture(scope="session", autouse=True)
def mc_cache_dir(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    try:
        import monte_carlo as _mc
    except Exception:               # numpy/pandas absent — nothing to isolate
        yield None
        return

    d = tmp_path_factory.mktemp("mc_cache")
    mp.setattr(_mc, "_EQUITY_CACHE_CSV", d / "mc_nifty50_nav.csv")
    mp.setattr(_mc, "_DEBT_CACHE_CSV", d / "mc_debt_index.csv")
    try:
        yield d
    finally:
        mp.undo()


# ── Known-bug registry ───────────────────────────────────────────────────────
# Tests failing because of a filed, unfixed bug get xfail(strict=True) here, so
# CI stays green for regressions while the bug stays loudly pinned: fixing it
# makes the test pass unexpectedly and the build fails until the entry is
# removed. Applied here rather than as decorators so test_swp_engine.py keeps
# working as a standalone script without a pytest dependency.
#
# Issues #1-#4 are all fixed. Empty is the correct state: an entry here means
# a filed bug is reproduced and pinned, and strict=True breaks the build when
# someone fixes it without clearing the entry.
KNOWN_BUGS: dict[str, str] = {}


def pytest_collection_modifyitems(config, items):
    import pytest as _pytest

    for item in items:
        reason = KNOWN_BUGS.get(item.originalname or item.name)
        if reason:
            item.add_marker(_pytest.mark.xfail(strict=True, reason=reason))
