#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""nsdl_vocab.py -- what the file actually says, before deciding what to read.

WHY THIS EXISTS. The first seniority pass found 134 subordinated instruments in
28,745 rows using a marker table written for PROSE descriptions -- SUBORDINATED,
BASEL III, ADDITIONAL TIER 1. The file does not write prose. It writes
compressed codes: "STATE BANK OF INDIA SR I 7.72 BD PERPETUAL FVRS1CR",
"ARG25PDI03 9.50 NCD PERPE", "SR PDI OPT II 12 NCD". BASEL never appears at
all. PDI -- Perpetual Debt Instrument -- appears repeatedly and is not in the
marker table; the rows carrying it were caught only because PERPETUAL happened
to appear as well, and the ones where it did not were missed silently.

So the marker table has to be derived from this file's vocabulary rather than
from what a bond description ought to look like. That is what this does, and
it decides nothing: it reports.

THREE PASSES, IN THE ORDER THAT NARROWS FASTEST.

  1. EVERY COLUMN'S CARDINALITY. A twenty-column file where one column has
     three distinct values and another has 28,000 is telling you which columns
     are keys and which are free text. `Type of Instrument` turned out to hold
     exactly three values -- Debentures, Bonds, blank -- which settles that it
     cannot carry seniority, and that is worth knowing definitively rather than
     by inference from a subordinated count.

  2. TOKEN AND BIGRAM FREQUENCY over the free-text columns. Markers have to be
     chosen from tokens that exist.

  3. TARGETED PROBES with sample rows. For each candidate marker: how many rows
     contain it, how many of those the CURRENT rule already catches, and what
     the strings look like. The second number is the one that matters -- a
     token appearing 900 times of which 890 are already caught buys ten rows,
     and a token appearing 300 times of which none are caught is the find.

IT ALSO ANSWERS WHETHER THE MATURITY CROSS-CHECK IS ALIVE. The audit reported
zero undated corporates and 134 flagged-but-dated, including bonds whose
descriptions plainly say PERPETUAL. That means the redemption column is
populated for every row including the perpetuals -- probably with the call
date, which is a real date on a real instrument that never matures. If so the
cross-check has no discriminating power and should stop being described as
validation. `--maturity` prints the distribution so that is settled by looking.
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fund_filters import instruments as I  # noqa: E402

# Candidate seniority tokens, drawn from Indian debt naming conventions and
# from the strings the first audit printed. NONE of these is adopted here.
SENIORITY_PROBES = [
    "PDI", "PERPETUAL", "PERP", "SUBORDINATED", "SUBORDINATE", "SUBDEBT",
    "SUB DEBT", "SUBD", "TIER", "TIER 1", "TIER I", "TIER 2", "TIER II",
    "AT1", "AT 1", "UT2", "LT2", "UT 2", "LT 2", "UPPER TIER", "LOWER TIER",
    "BASEL", "HYBRID", "IPDI", "T1", "T2",
]

# Not seniority. Printed because the naming GRAMMAR is what tells you whether
# a marker can be trusted: if every row ends FVRS1CR and carries SR and a
# coupon, the descriptions are generated from fields and the vocabulary is
# closed, which is a much stronger position than free text.
STRUCTURAL_PROBES = ["SR", "NCD", "BD", "OCD", "LOA", "PP", "FVRS", "SERIES"]

# Columns worth tokenising. The rest are dates, numbers or codes.
TEXT_COLUMNS = ("Security Description", "Name of Issuer", "Series",
                "Type of Instrument", "Category of Issue")

_TOKEN_RE = re.compile(r"[A-Z0-9&]+")
LOW_CARDINALITY = 40      # show every value below this many distinct


def _norm(v):
    return " ".join(str(v).split()) if v is not None else ""


def load(path, sheet=None):
    """Raw rows as dicts, every column kept.

    Deliberately NOT parse_workbook: that drops five columns, and the question
    here is which column carries the signal.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    header = [_norm(h) for h in next(it)]
    return header, [dict(zip(header, r)) for r in it]


def cardinality(header, rows):
    """Distinct values per column, and the full value list where it is small."""
    out = {}
    for h in header:
        if not h:
            continue
        c = collections.Counter(_norm(r.get(h)) for r in rows)
        out[h] = dict(distinct=len(c), blank=c.get("", 0),
                      values=dict(c.most_common())
                      if len(c) <= LOW_CARDINALITY else None,
                      top=c.most_common(5))
    return out


def tokens(rows, column, n=2):
    """Token and n-gram counts for one free-text column."""
    uni, grams = collections.Counter(), collections.Counter()
    for r in rows:
        t = _TOKEN_RE.findall(_norm(r.get(column)).upper())
        uni.update(t)
        for k in range(2, n + 1):
            grams.update(" ".join(t[i:i + k]) for i in range(len(t) - k + 1))
    return uni, grams


def probe(rows, needles, sample=3):
    """For each candidate: how many rows contain it, and how many of those the
    CURRENT rule already catches.

    THREE MATCH WIDTHS, BECAUSE THE FIRST ATTEMPT AT THIS GOT IT WRONG. A
    strict word-boundary probe for PDI returns ZERO on this file, because the
    token is glued to a series number -- SR PDI22, SR PDI2, ARG25PDI03. A
    marker table built from a strict probe would have concluded PDI does not
    appear and dropped it, which is exactly backwards.

        strict   \bPDI\b     the form a safe marker would use
        prefix   \bPDI       PDI22, PDI2 -- a token starting the word
        loose    PDI         ARG25PDI03 -- anywhere, including inside a code

    The gap between the three IS the finding. Where strict is small and prefix
    is large the marker needs the looser form; where loose is far above prefix,
    read the examples before trusting it, because that is where false
    positives live.

    `new` is computed on the PREFIX width: what adopting that form would add
    beyond what the current rule already catches. That is the decision number.
    """
    out = []
    for needle in needles:
        esc = re.escape(needle).replace(r"\ ", r"\s+")
        strict = re.compile(r"\b" + esc + r"\b", re.I)
        prefix = re.compile(r"\b" + esc, re.I)
        loose = re.compile(esc, re.I)
        n_strict = n_prefix = n_loose = already = 0
        examples, loose_only = [], []
        for r in rows:
            text = (f"{_norm(r.get('Type of Instrument'))} "
                    f"{_norm(r.get('Security Description'))} "
                    f"{_norm(r.get('Series'))}")
            hit_loose = bool(loose.search(text))
            if not hit_loose:
                continue
            n_loose += 1
            hit_prefix = bool(prefix.search(text))
            n_prefix += 1 if hit_prefix else 0
            n_strict += 1 if strict.search(text) else 0
            caught = I.subordinated_text(
                f"{_norm(r.get('Type of Instrument'))} "
                f"{_norm(r.get('Security Description'))}") is not None
            if hit_prefix:
                already += 1 if caught else 0
                if not caught and len(examples) < sample:
                    examples.append(_norm(r.get("Security Description"))[:78])
            elif len(loose_only) < sample:
                loose_only.append(_norm(r.get("Security Description"))[:78])
        out.append(dict(needle=needle, strict=n_strict, prefix=n_prefix,
                        loose=n_loose, already=already,
                        new=n_prefix - already, examples=examples,
                        loose_only=loose_only))
    return sorted(out, key=lambda d: -d["new"])


def maturity_of_perpetuals(rows, sample=10):
    """Does the redemption column ever say 'no maturity'?

    If it does not, the audit's undated/flagged cross-check has no
    discriminating power and must stop being called validation.
    """
    dated, undated, examples = 0, 0, []
    for r in rows:
        desc = _norm(r.get("Security Description")).upper()
        if "PERPETUAL" not in desc and "PDI" not in desc:
            continue
        red = _norm(r.get("Date of Redemption/Conversion"))
        if red:
            dated += 1
            if len(examples) < sample:
                examples.append((red, desc[:64]))
        else:
            undated += 1
    total_undated = sum(1 for r in rows
                        if not _norm(r.get("Date of Redemption/Conversion")))
    return dict(perpetual_dated=dated, perpetual_undated=undated,
                undated_anywhere=total_undated, examples=examples)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workbook")
    ap.add_argument("--top", type=int, default=60,
                    help="tokens and bigrams to print (default 60)")
    ap.add_argument("--columns", action="store_true",
                    help="cardinality of every column")
    ap.add_argument("--maturity", action="store_true",
                    help="is the redemption column ever blank?")
    ap.add_argument("--probes", action="store_true",
                    help="candidate markers, and what each would ADD")
    a = ap.parse_args(argv)
    if not (a.columns or a.maturity or a.probes):
        a.columns = a.maturity = a.probes = True

    header, rows = load(a.workbook)
    print(f"{len(rows):,} rows, {len(header)} columns\n")

    if a.columns:
        print("=" * 74)
        print("COLUMN CARDINALITY -- which columns are keys, which are text")
        print("=" * 74)
        for h, d in cardinality(header, rows).items():
            print(f"\n{h}   distinct {d['distinct']:,}   blank {d['blank']:,}")
            if d["values"] is not None:
                for v, n in d["values"].items():
                    print(f"    {(v or '(blank)')[:56]:<58}{n:>8,}")
            else:
                for v, n in d["top"]:
                    print(f"    {(v or '(blank)')[:56]:<58}{n:>8,}")

    if a.probes:
        for title, needles in (("CANDIDATE SENIORITY MARKERS",
                                SENIORITY_PROBES),
                               ("NAMING GRAMMAR (not seniority)",
                                STRUCTURAL_PROBES)):
            print("\n" + "=" * 74)
            print(f"{title} -- 'new' is what the prefix form would ADD")
            print("=" * 74)
            print(f"{'token':<14}{'strict':>8}{'prefix':>8}{'loose':>8}"
                  f"{'already':>9}{'new':>7}")
            for d in probe(rows, needles):
                if d["loose"] == 0:
                    continue
                print(f"{d['needle']:<14}{d['strict']:>8,}{d['prefix']:>8,}"
                      f"{d['loose']:>8,}{d['already']:>9,}{d['new']:>7,}")
                for e in d["examples"]:
                    print(f"    + {e}")
                for e in d["loose_only"]:
                    print(f"    ? {e}   (loose only -- read before trusting)")
            absent = [d["needle"] for d in probe(rows, needles)
                      if d["loose"] == 0]
            if absent:
                print(f"  absent from the file: {', '.join(sorted(absent))}")

    if a.maturity:
        print("\n" + "=" * 74)
        print("IS THE MATURITY CROSS-CHECK ALIVE?")
        print("=" * 74)
        m = maturity_of_perpetuals(rows)
        print(f"  rows saying PERPETUAL or PDI, WITH a redemption date  "
              f"{m['perpetual_dated']:>8,}")
        print(f"  rows saying PERPETUAL or PDI, with NO date            "
              f"{m['perpetual_undated']:>8,}")
        print(f"  rows anywhere in the file with NO date                "
              f"{m['undated_anywhere']:>8,}")
        if m["undated_anywhere"] == 0:
            print("\n  The column is populated for every row, including the "
                  "perpetuals.\n  The cross-check has NO discriminating power "
                  "and is not validation.")
        for red, desc in m["examples"]:
            print(f"    {red:<14}{desc}")

    print("\n" + "=" * 74)
    print("DESCRIPTION VOCABULARY")
    print("=" * 74)
    for col in TEXT_COLUMNS:
        if col not in header:
            continue
        uni, grams = tokens(rows, col)
        if not uni:
            continue
        print(f"\n-- {col}: top tokens --")
        for t, n in uni.most_common(a.top):
            print(f"    {t[:36]:<38}{n:>8,}")
        print(f"\n-- {col}: top bigrams --")
        for t, n in grams.most_common(a.top // 2):
            print(f"    {t[:36]:<38}{n:>8,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
