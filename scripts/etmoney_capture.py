#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""etmoney_capture.py -- everything about N funds, in one zip, in one run.

WHY THIS EXISTS. Discovering this page took three rounds -- fetch, then look
for a hydration blob, then look for a plain var assignment -- and each round
cost a message because only a printed summary came back. Printing a summary
throws away everything the summary did not think to mention. This writes the
ARTEFACTS instead: raw HTML, every JS var that parses as JSON, the search
response, and a manifest. One zip, one upload, no more rounds.

TAKE SEVERAL FUNDS, AND MAKE THEM DIFFERENT. A parser written against one
banking-and-PSU debt fund will meet an equity fund's holdings, a liquid fund's
CP and CD, a gilt fund with nothing but sovereigns, and a hybrid with both --
and break on at least one. Capturing four or five kinds at once means the
parser is written once against the variety rather than four times against
whatever turned up next. The default list below is chosen for that spread.

WHAT IT DOES NOT DO. It does not parse holdings and it does not touch the
database. It resolves names to ids through the search API already tested in
ingest/etmoney.py, fetches each portfolio page, extracts what is extractable,
and stops.

    python3 scripts/etmoney_capture.py
    python3 scripts/etmoney_capture.py --funds "DSP Savings Fund" "SBI Liquid Fund"
    python3 scripts/etmoney_capture.py --ids 44228:bajaj-finserv-banking-and-psu-fund
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import re
import sys
import time
import urllib.request
import zipfile

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ingest import etmoney as E  # noqa: E402

# Chosen for SHAPE, not for quality. Each is a different holdings problem:
# corporate debt with ratings; a liquid fund of CP and CD, which carry no ISIN
# and are absent from the NSDL file; a gilt fund that is all sovereign; an
# equity fund whose holdings are shares and have no rating at all; and a
# hybrid that mixes the last two in one table.
DEFAULT_FUNDS = (
    "Bajaj Finserv Banking and PSU Fund",
    "ICICI Prudential Money Market Fund",
    "SBI Magnum Gilt Fund",
    "Invesco India Largecap Fund",
    "HDFC Balanced Advantage Fund",
)

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "accept-encoding": "gzip",
    "user-agent": E.HEADERS["user-agent"],
}

MIN_VAR_CHARS = 200


def fetch_page(url, timeout=45):
    req = urllib.request.Request(url, headers=PAGE_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return resp.status, raw


def js_value_at(text, start):
    """The balanced {...} or [...] at `start`. String-aware, so a brace inside
    a quoted fund name cannot close the object early."""
    if start >= len(text) or text[start] not in "{[":
        return None
    depth, in_str, esc, quote = 0, False, False, ""
    for j in range(start, len(text)):
        c = text[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif in_str:
            if c == quote:
                in_str = False
        elif c in "\"'":
            in_str, quote = True, c
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return None


def js_vars(text, minimum=MIN_VAR_CHARS):
    """Every `var NAME = {...}` big enough to be data.

    ETMoney puts the portfolio in a plain assignment inside
    `function afterAllJSFilesLoaded()` -- not a hydration global, not an XHR,
    not __NEXT_DATA__. Looking for the assignment is what finds it.
    """
    out = {}
    for m in re.finditer(r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*",
                         text):
        raw = js_value_at(text, m.end())
        if not raw or len(raw) < minimum:
            continue
        try:
            out[m.group(1)] = json.loads(raw)
        except ValueError:
            out.setdefault("_unparsed", {})[m.group(1)] = raw[:2000]
    return out


def capture(target, outdir, delay=1.0):
    """One fund: search, fetch, extract. Returns a manifest entry."""
    d = pathlib.Path(outdir)
    d.mkdir(parents=True, exist_ok=True)

    if isinstance(target, tuple):
        fid, slug = target
        url = f"{E.BASE}/mutual-funds/{slug}/portfolio-details/{fid}"
        entry = dict(query=None, fund_id=int(fid), label=slug, score=None)
    else:
        m = E.resolve(target)
        time.sleep(delay)
        (d / f"search_{re.sub(r'[^A-Za-z0-9]+', '_', target)}.json").write_text(
            json.dumps(dict(m), indent=1, default=str))
        if not m["found"]:
            return dict(query=target, found=False, reason=m["reason"])
        fid, url = m["fund_id"], m["portfolio_url"]
        entry = dict(query=target, fund_id=fid, label=m["label"],
                     score=m["score"], seo_web_url=m["seo_web_url"])

    status, raw = fetch_page(url)
    html = raw.decode("utf-8", errors="replace")
    (d / f"portfolio_{fid}.html").write_bytes(raw)

    variables = js_vars(html)
    for name, value in variables.items():
        (d / f"var_{fid}_{name}.json").write_text(
            json.dumps(value, indent=1, default=str))

    entry.update(found=True, url=url, status=status, bytes=len(raw),
                 vars={k: (len(v) if isinstance(v, (list, dict)) else 1)
                       for k, v in variables.items()})
    return entry


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--funds", nargs="*", default=list(DEFAULT_FUNDS),
                    help="fund names to resolve via the search API")
    ap.add_argument("--ids", nargs="*", default=[], metavar="ID:SLUG",
                    help="skip the search: 44228:bajaj-finserv-banking-...")
    ap.add_argument("--out", default="data/etmoney/capture")
    ap.add_argument("--zip", dest="zip_path", default="data/etmoney_capture.zip")
    ap.add_argument("--delay", type=float, default=1.0)
    a = ap.parse_args(argv)

    targets = list(a.funds)
    for spec in a.ids:
        fid, _, slug = spec.partition(":")
        targets.append((fid, slug))

    manifest = []
    for i, t in enumerate(targets, 1):
        name = t if isinstance(t, str) else f"{t[0]} ({t[1]})"
        print(f"[{i}/{len(targets)}] {name}")
        try:
            e = capture(t, a.out, delay=a.delay)
        except Exception as exc:               # noqa: BLE001
            # One fund failing must not lose the four that worked. The whole
            # point is coming back with artefacts.
            e = dict(query=name, found=False,
                     reason=f"{type(exc).__name__}: {exc}")
        manifest.append(e)
        if e.get("found"):
            print(f"        id {e['fund_id']}  {e['bytes']:,} bytes  "
                  f"vars: {', '.join(e['vars']) or 'none'}")
        else:
            print(f"        FAILED: {e['reason']}")
        if i < len(targets):
            time.sleep(a.delay)

    mpath = pathlib.Path(a.out) / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=1, default=str))

    zp = pathlib.Path(a.zip_path)
    zp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pathlib.Path(a.out).rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(pathlib.Path(a.out).parent))
    print(f"\nwrote {zp}  ({zp.stat().st_size:,} bytes, "
          f"{sum(1 for e in manifest if e.get('found'))}/{len(manifest)} funds)")
    print("Upload that one file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
