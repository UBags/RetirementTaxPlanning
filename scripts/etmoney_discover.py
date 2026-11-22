#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""etmoney_discover.py -- what shape is the portfolio page, before parsing it.

WHY A DISCOVERY RUN AND NOT A PARSER. There are three quite different ways
this page could deliver holdings, and they need three different pieces of
code:

  1. A Next.js hydration blob -- <script id="__NEXT_DATA__"> carrying the
     whole portfolio as JSON. Best case by a distance: no HTML parsing, the
     numbers arrive typed, and the structure survives a redesign.
  2. An XHR to a JSON endpoint the page calls after load, like the
     global-search one already in use. Nearly as good, and it means the HTML
     can be skipped entirely.
  3. Server-rendered tables. Workable, but every column heading becomes a
     thing that can change silently.

Guessing wrong means writing the wrong parser and finding out on the first
real fund. This fetches ONE page, writes the raw bytes to disk, and reports
which of the three it is -- and it decides nothing.

IT SAVES THE HTML. A parser written next week has to be testable against what
was actually served, not against a description of it, and the page will have
changed by then.

    python3 scripts/etmoney_discover.py 44228 \\
        --slug bajaj-finserv-banking-and-psu-fund

Or give it a full URL. Fetch only; nothing is stored in the database.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
import sys
import urllib.request

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from swp_planner.ingest import etmoney as E  # noqa: E402

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "accept-encoding": "gzip",
    "user-agent": E.HEADERS["user-agent"],
}

# Words that would appear in a holdings payload whatever shape it takes.
PROBES = ("holding", "portfolio", "instrument", "sector", "assetAllocation",
          "asset_allocation", "isin", "marketValue", "market_value",
          "percentage", "quantity", "rating", "creditRating", "maturity",
          "purchase", "redemption", "aum", "netAssets")


def fetch(url, timeout=45):
    req = urllib.request.Request(url, headers=PAGE_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("content-encoding") == "gzip":
            raw = gzip.decompress(raw)
        # resp.headers is case-insensitive; dict() is not, and
        # the server sends "Content-Type", so the copy must be
        # lower-cased or every lookup silently misses.
        return resp.status, {k.lower(): v for k, v
                             in resp.headers.items()}, raw


def next_data(html):
    """The __NEXT_DATA__ blob, if this is a Next.js page."""
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def walk(obj, path="", depth=0, max_depth=6):
    """Every path in a nested structure, with its type and size.

    Printed rather than searched, because the field NAMES are the thing being
    discovered and a search would only find the ones already guessed at.
    """
    out = []
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, (dict, list)):
                out.append((p, type(v).__name__, len(v)))
                out.extend(walk(v, p, depth + 1, max_depth))
            else:
                out.append((p, type(v).__name__, None))
    elif isinstance(obj, list) and obj:
        out.append((f"{path}[0]", type(obj[0]).__name__,
                    len(obj[0]) if isinstance(obj[0], (dict, list)) else None))
        out.extend(walk(obj[0], f"{path}[0]", depth + 1, max_depth))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", help="fund id, or a full portfolio-details URL")
    ap.add_argument("--slug", default=None,
                    help="URL slug, when giving a bare fund id")
    ap.add_argument("--out", default="data/etmoney",
                    help="where the raw page is written")
    ap.add_argument("--paths", type=int, default=120,
                    help="__NEXT_DATA__ paths to print (default 120)")
    a = ap.parse_args(argv)

    if a.target.startswith("http"):
        url, fid = a.target, a.target.rstrip("/").split("/")[-1]
    else:
        fid = a.target
        if not a.slug:
            raise SystemExit("give --slug with a bare fund id, or a full URL")
        url = (f"{E.BASE}/mutual-funds/{a.slug}/portfolio-details/{fid}")

    print(f"GET {url}")
    status, headers, raw = fetch(url)
    print(f"    {status}   {len(raw):,} bytes   "
          f"{headers.get('content-type', '?')}")

    d = pathlib.Path(a.out)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"portfolio_{fid}.html"
    path.write_bytes(raw)
    print(f"    saved {path}")

    html = raw.decode("utf-8", errors="replace")

    print("\n" + "=" * 70)
    print("1. IS IT NEXT.JS?  (best case: the whole portfolio as typed JSON)")
    print("=" * 70)
    nd = next_data(html)
    if nd is None:
        print("  no __NEXT_DATA__ block found")
    else:
        blob = json.dumps(nd)
        print(f"  __NEXT_DATA__ present, {len(blob):,} chars")
        (d / f"next_data_{fid}.json").write_text(json.dumps(nd, indent=1))
        print(f"  saved {d / f'next_data_{fid}.json'}")
        rows = walk(nd)
        big = [r for r in rows if r[1] == "list" and (r[2] or 0) >= 5]
        print("\n  lists of 5+ items -- holdings live in one of these:")
        for p, _, n in sorted(big, key=lambda r: -(r[2] or 0))[:25]:
            print(f"    {n:>6}  {p}")
        print(f"\n  first {a.paths} paths:")
        for p, kind, n in rows[:a.paths]:
            print(f"    {p:<62}{kind}{'' if n is None else f' [{n}]'}")

    print("\n" + "=" * 70)
    print("2. IS THERE AN XHR ENDPOINT?  (nearly as good; skips the HTML)")
    print("=" * 70)
    apis = sorted(set(re.findall(r'["\'](/api/[A-Za-z0-9/_\-]+)', html)))
    for u in apis[:40]:
        print(f"    {u}")
    if not apis:
        print("    none referenced in the HTML -- if the page fetches data "
              "after load,\n    it will only show in the browser Network tab")

    print("\n" + "=" * 70)
    print("3. SERVER-RENDERED TABLES?  (workable, but headings can shift)")
    print("=" * 70)
    tables = re.findall(r"<table\b", html, re.I)
    print(f"    <table> elements: {len(tables)}")
    heads = re.findall(r"<th[^>]*>(.*?)</th>", html, re.S | re.I)
    for h in [re.sub(r"<[^>]+>", "", x).strip() for x in heads][:40]:
        if h:
            print(f"      th: {h[:70]}")

    print("\n" + "=" * 70)
    print("WHICH WORDS APPEAR AT ALL")
    print("=" * 70)
    for w in PROBES:
        n = len(re.findall(re.escape(w), html, re.I))
        if n:
            print(f"    {w:<20}{n:>6}")

    print("\nIf section 1 found a big list, that is the parser. Send the "
          "'lists of 5+ items'\nblock and the paths under whichever one looks "
          "like holdings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
