#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""fund_credit.py -- a fund name in, a credit profile out.

    python3 scripts/fund_credit.py "Bajaj Finserv Banking and PSU Fund"
    python3 scripts/fund_credit.py --file data/etmoney/portfolio_44228.html

The whole chain in one command: resolve the name to an ETMoney id, fetch the
portfolio, parse the holdings, look every issuer up against NSDL, and print
what resolved, what did not, and whether our ratings reproduce ETMoney's own
published buckets.

READ THE OUTPUT IN THIS ORDER. `UNRESOLVED` first, because it is the only
number that indicts the lookup -- everything else is an explained miss. Then
the rating check, because it is the only validation that scales past eyeballing
one fund. Then the manual queue, which is what a human actually has to do.
"""
from __future__ import annotations

import argparse
import gzip
import pathlib
import sys
import urllib.request

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ingest import etmoney as E  # noqa: E402
from ingest import etmoney_portfolio as P  # noqa: E402
from lookup import portfolio_credit as PC  # noqa: E402
from lookup.api import CreditLookup  # noqa: E402

DEFAULT_DSN = "dbname=RetirementPlanning"


def fetch_html(url, timeout=45):
    req = urllib.request.Request(url, headers={
        "accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "accept-encoding": "gzip", "user-agent": E.HEADERS["user-agent"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", nargs="?", help="fund name, as AMFI writes it")
    ap.add_argument("--file", help="a saved portfolio page; skips the fetch")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--as-on", default=None, help="YYYY-MM-DD, default latest")
    ap.add_argument("--floor", type=float, default=0.10,
                    help="smallest holding worth queueing (default 0.10%%)")
    ap.add_argument("--sample", type=int, default=20)
    a = ap.parse_args(argv)
    if not a.name and not a.file:
        raise SystemExit("give a fund name or --file")

    if a.file:
        html = pathlib.Path(a.file).read_text(errors="replace")
        print(f"reading   {a.file}")
    else:
        m = E.resolve(a.name)
        if not m["found"]:
            raise SystemExit(f"could not resolve {a.name!r}: {m['reason']}")
        note = "" if not m["ladder_steps"] else \
            f"  (searched as {m['searched_as']!r})"
        print(f"resolved  {m['label']}  id {m['fund_id']}  "
              f"score {m['score']}{note}")
        if m["ambiguous"]:
            print(f"  AMBIGUOUS: runner-up {m['runner_up']}, "
                  f"margin {m['margin']}")
        html = fetch_html(m["portfolio_url"])

    payload = P.extract_payload(html)
    s = P.summarise(payload)
    print(f"parsed    {s['holdings']} holdings, {s['issuers']} issuers, "
          f"{s['months']} months to {s['last_month']}")
    print()

    import psycopg2
    conn = psycopg2.connect(a.dsn)
    lookup = CreditLookup(conn)
    print(PC.report(payload, lookup, as_on=a.as_on, floor=a.floor,
                    sample=a.sample))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
