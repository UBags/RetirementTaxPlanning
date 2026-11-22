#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""etmoney_shape.py -- read the SAVED page. No network, no refetch.

WHY A SECOND PASS. The first discovery asked whether the page was Next.js by
looking for `<script id="__NEXT_DATA__">` and concluded no. That test was too
narrow. Next.js 13+ with the App Router does not emit __NEXT_DATA__ at all --
it streams the same payload through repeated `self.__next_f.push([1,"..."])`
calls, and several other frameworks use their own global. So "no __NEXT_DATA__"
does not mean "no JSON on the page".

THE EVIDENCE THAT SOMETHING IS STILL THERE. `assetAllocation` appeared 22
times in the HTML, in camelCase. Rendered prose does not contain camelCase.
That is a JavaScript object literal, and finding it is worth one more pass
because typed JSON beats scraped table cells on every axis that matters: the
numbers arrive as numbers, the field names survive a visual redesign, and a
column reordering cannot silently transpose two values.

WHAT IT DOES, IN ORDER OF HOW GOOD THE ANSWER WOULD BE.

  1. Every <script> on the page -- id, type, length -- because the payload has
     to be in one of them and the count alone says whether it is worth
     hunting.
  2. The known hydration globals, __next_f included, with the App Router
     stream reassembled and unescaped.
  3. A direct scan for the camelCase keys, reporting the JSON object around
     each hit. This works even when the framework is one nobody guessed,
     because it looks for the DATA rather than for the mechanism.
  4. Failing all that, the four tables, dumped as headings plus real rows --
     which is the parser to write if there is genuinely no JSON.

IT DECIDES NOTHING. Same as the first pass.

    python3 scripts/etmoney_shape.py data/etmoney/portfolio_44228.html
"""
from __future__ import annotations

import argparse
import html as _html
import json
import pathlib
import re
import sys
from html.parser import HTMLParser

# Globals that carry a hydration payload, across the frameworks in common use.
HYDRATION_GLOBALS = (
    "self.__next_f", "__NEXT_DATA__", "window.__NUXT__", "__NUXT__",
    "window.__INITIAL_STATE__", "__INITIAL_STATE__", "window.__APOLLO_STATE__",
    "window.__PRELOADED_STATE__", "__remixContext", "window.__data",
    "window.__STATE__", "dataLayer",
)

# camelCase keys that would only appear in a data payload.
DATA_KEYS = ("assetAllocation", "sectorAllocation", "holdings", "holdingList",
             "portfolioDetails", "creditRating", "marketValue", "netAssets",
             "instrumentName", "topHoldings", "debtHoldings", "equityHoldings",
             "percentageHolding", "portfolioData", "aumValue")


class _Tables(HTMLParser):
    """Tables as lists of rows of cell text. stdlib only, so nothing to
    install on a machine that just needs to answer one question."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._t, self._row, self._cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = []
        elif tag == "tr" and self._t is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._t is not None:
            self.tables.append(self._t)
            self._t = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._t.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def scripts(html):
    """(id, type, length, head) for every script element."""
    out = []
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        sid = re.search(r'id=["\']([^"\']+)', attrs)
        typ = re.search(r'type=["\']([^"\']+)', attrs)
        out.append((sid.group(1) if sid else "",
                    typ.group(1) if typ else "",
                    len(body), body[:90].replace("\n", " ")))
    return out


def next_flight(html):
    """Reassemble a Next.js App Router stream into one string.

    Each push carries a JSON-encoded string fragment; concatenating the
    DECODED fragments in order rebuilds the payload. Fragments that will not
    decode are skipped rather than aborting the whole reassembly -- one bad
    chunk should not hide the rest.
    """
    parts = []
    for m in re.finditer(r'self\.__next_f\.push\(\[\d+\s*,\s*(".*?")\]\)',
                         html, re.S):
        try:
            parts.append(json.loads(m.group(1)))
        except ValueError:
            continue
    return "".join(parts)


def object_around(text, index, span=4000):
    """The smallest balanced {...} containing `index`, if one is nearby.

    Scans outward for a brace whose match encloses the hit. Bounded, because
    on a 427 KB page an unbounded scan on malformed input is a hang.
    """
    start = max(0, index - span)
    for i in range(index, start - 1, -1):
        if text[i] != "{":
            continue
        depth, in_str, esc = 0, False, False
        for j in range(i, min(len(text), i + span * 4)):
            c = text[j]
            if esc:
                esc = False
                continue
            if c == "\\":
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        if j >= index:
                            return text[i:j + 1]
                        break
    return None


def report_hits(text, label, limit=6, preview=700):
    found = False
    for key in DATA_KEYS:
        # CASE-INSENSITIVE, BECAUSE THE CASE-SENSITIVE VERSION MISSED IT.
        # The payload is assigned to `getAssetAllocationData`, so a scan for
        # "assetAllocation" with a lower-case leading a never matched, and the
        # page reported no data keys while carrying 119 KB of them.
        hits = [m.start() for m in re.finditer(re.escape(key), text, re.I)]
        if not hits:
            continue
        found = True
        print(f"\n  {key}  x{len(hits)}  (in {label})")
        blob = object_around(text, hits[0])
        if blob:
            try:
                parsed = json.loads(blob)
                print(f"    parses as JSON, {len(parsed)} keys: "
                      f"{list(parsed)[:12]}")
                for k, v in list(parsed.items())[:12]:
                    kind = type(v).__name__
                    size = len(v) if isinstance(v, (list, dict, str)) else ""
                    print(f"      {k:<28}{kind}{f' [{size}]' if size else ''}")
            except ValueError:
                print("    not valid JSON on its own; raw head:")
                print(f"      {blob[:preview]}")
        else:
            print(f"    context: ...{text[max(0, hits[0]-120):hits[0]+240]}...")
        if limit and DATA_KEYS.index(key) > limit:
            break
    return found


def js_value_at(text, start):
    """The balanced {...} or [...] beginning at `start`, or None.

    String-aware, so a brace inside a quoted value cannot close the object
    early -- which on a 119 KB payload of fund names full of punctuation is
    not a hypothetical.
    """
    if start >= len(text) or text[start] not in "{[":
        return None
    opens, closes = "{[", "}]"
    depth, in_str, esc, quote = 0, False, False, ""
    for j in range(start, len(text)):
        c = text[j]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
        elif in_str:
            if c == quote:
                in_str = False
        elif c in "\"'":
            in_str, quote = True, c
        elif c in opens:
            depth += 1
        elif c in closes:
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return None


def js_vars(text):
    """Every `var NAME = {...}` / `= [...]` in the page.

    THIS IS WHERE ETMONEY PUTS THE PORTFOLIO. Not a hydration global, not an
    XHR, not __NEXT_DATA__ -- a plain assignment inside
    `function afterAllJSFilesLoaded()`, which no framework probe would ever
    find. Looking for the ASSIGNMENT rather than for a known global is what
    makes this work on a page nobody has seen.

    Returns (name, raw_length, parsed_or_None). Parsing is attempted with
    json.loads and allowed to fail: JS object literals with unquoted keys --
    `{counterpartSchemeUrl:"..."}` -- are not JSON, and reporting them as
    unparsed is more useful than dropping them.
    """
    out = []
    for m in re.finditer(r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*",
                         text):
        raw = js_value_at(text, m.end())
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        out.append((m.group(1), len(raw), parsed))
    return out


def walk(obj, path="", depth=0, max_depth=6):
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
    ap.add_argument("path", help="the saved portfolio HTML")
    ap.add_argument("--paths", type=int, default=40,
                    help="paths to print per JS var (default 40)")
    ap.add_argument("--rows", type=int, default=6,
                    help="table rows to print per table (default 6)")
    a = ap.parse_args(argv)

    raw = pathlib.Path(a.path).read_bytes()
    html = raw.decode("utf-8", errors="replace")
    print(f"{a.path}  {len(raw):,} bytes\n")

    print("=" * 70)
    print("1. SCRIPTS ON THE PAGE")
    print("=" * 70)
    ss = scripts(html)
    print(f"  {len(ss)} script elements; the 12 largest:")
    for sid, typ, n, head in sorted(ss, key=lambda s: -s[2])[:12]:
        print(f"    {n:>8,}  id={sid or '-':<18} type={typ or '-':<26}")
        if n > 200:
            print(f"              {head[:78]}")

    print("\n" + "=" * 70)
    print("2. HYDRATION GLOBALS")
    print("=" * 70)
    for g in HYDRATION_GLOBALS:
        n = html.count(g)
        if n:
            print(f"    {g:<32}x{n}")
    flight = next_flight(html)
    if flight:
        print(f"\n  __next_f stream reassembled: {len(flight):,} chars")
        out = pathlib.Path(a.path).with_suffix(".flight.txt")
        out.write_text(flight)
        print(f"  saved {out}")

    print("\n" + "=" * 70)
    print("3. JS VAR ASSIGNMENTS  (where ETMoney actually puts it)")
    print("=" * 70)
    jvars = [v for v in js_vars(html) if v[1] >= 200]
    for name, n, parsed in sorted(jvars, key=lambda v: -v[1])[:12]:
        print(f"\n  var {name}  {n:,} chars  "
              f"{'JSON' if parsed is not None else 'not JSON (JS literal)'}")
        if parsed is None:
            continue
        out = pathlib.Path(a.path).with_suffix(f".{name}.json")
        out.write_text(json.dumps(parsed, indent=1))
        print(f"    saved {out}")
        rows = walk(parsed)
        big = [r for r in rows if r[1] == "list" and (r[2] or 0) >= 3]
        if big:
            print("    lists of 3+ items:")
            for p_, _, cnt in sorted(big, key=lambda r: -(r[2] or 0))[:10]:
                print(f"      {cnt:>5}  {p_}")
        print("    paths:")
        for p_, kind, cnt in rows[:a.paths]:
            print(f"      {p_:<58}{kind}{'' if cnt is None else f' [{cnt}]'}")
    if not jvars:
        print("  none over 200 chars")

    print("\n" + "=" * 70)
    print("4. THE DATA KEYS THEMSELVES  (framework-agnostic)")
    print("=" * 70)
    hit = False
    if flight:
        hit |= report_hits(flight, "__next_f stream")
    if not hit:
        hit |= report_hits(html, "raw html")
    if not hit:
        hit |= report_hits(_html.unescape(html), "unescaped html")
    if not hit:
        print("  none of the camelCase keys found anywhere -- the tables in "
              "section 5\n  are the parser")

    print("\n" + "=" * 70)
    print("5. THE TABLES")
    print("=" * 70)
    p = _Tables()
    p.feed(html)
    for i, tbl in enumerate(p.tables, 1):
        width = max((len(r) for r in tbl), default=0)
        print(f"\n  table {i}: {len(tbl)} rows, widest {width} cells")
        for row in tbl[:a.rows]:
            print("    | " + " | ".join(c[:26] for c in row[:8]))
        if len(tbl) > a.rows:
            print(f"    ... {len(tbl) - a.rows} more rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
