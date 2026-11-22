#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""fund_rank.py -- score every fund against its own category, and store it.

    python3 scripts/fund_rank.py --metrics Fund_Metrics_Output.csv
    python3 scripts/fund_rank.py --metrics ... --category "Debt Scheme - Liquid Fund"
    python3 scripts/fund_rank.py --metrics ... --dry-run

THE JOIN IS ON AMFI CODE, NOT ON NAME. Fund_Metrics_Output.csv carries an
`AMFI Code` column and india.fund stores amfi_scheme_code, so the two meet on
an integer. Name matching is the fallback and it is REPORTED, because a fuzzy
join that silently pairs the wrong funds is the same class of error as the four
schemes that took a neighbour's ETMoney id.

ONLY 801 OF ~2,300 FUNDS HAVE METRICS, so most of the universe is unranked and
the report says so rather than presenting the ranked third as the whole. That
is a fact about the metrics file, not about this code.

SMALL CATEGORIES ROLL UP TO THEIR SCHEME TYPE. A percentile over three funds is
0, 50 and 100 whatever their merits, so a category below MIN_PEERS is scored
against its parent instead -- "Debt Scheme - Long Duration Fund" against all
Debt Scheme funds. The roll-up is recorded per row in `peer_group`, because a
fund ranked against a broader field is not comparable to one ranked against its
own, and a reader must be able to tell which happened.

WHAT IS SCORED AND WHAT IS NOT. The composite is performance and volatility,
multiplied down by credit. A fund with no stored holdings has no credit
picture, so it is scored on performance alone and flagged -- not given a credit
multiplier of 1.0, which would rank it above funds whose paper was actually
checked.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fund_filters import fund_score as FS  # noqa: E402
from ingest import fund_store as F  # noqa: E402
from lookup.api import normalise as _normalise  # noqa: E402

DEFAULT_DSN = "dbname=RetirementPlanning"
DEFAULT_METRICS = "Fund_Metrics_Output.csv"


def read_metrics(path):
    """{amfi_code: row} and {normalised name: row}, from the metrics CSV."""
    by_code, by_name, rows = {}, {}, []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            m = FS.from_metrics_row(raw)
            rows.append(m)
            if m["amfi_code"]:
                by_code.setdefault(m["amfi_code"], m)
            n = _normalise(m["metrics_name"])
            if n:
                by_name.setdefault(n, m)
    return rows, by_code, by_name


def stored_funds(conn, as_on=None):
    """One row per fund: category, bands and issuer exposures for a month.

    The month is the LATEST that fund has, not a single date across the
    universe -- funds report at different times and forcing one date would
    silently drop the ones that had not published yet.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT f.fund_id, f.label, f.amfi_name, f.category, f.scheme_type,
                   f.amc, f.amfi_scheme_code, s.as_on
              FROM india.fund f
              JOIN LATERAL (SELECT MAX(as_on) AS as_on
                              FROM india.fund_holding h
                             WHERE h.fund_id = f.fund_id) s ON TRUE
             WHERE s.as_on IS NOT NULL
        """)
        cols = [d[0] for d in cur.description]
        funds = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            SELECT fund_id, as_on, rating_band, percent
              FROM india.fund_rating_allocation WHERE source = 'derived'
        """)
        bands = {}
        for fid, on, band, pct in cur.fetchall():
            bands.setdefault((fid, on), {})[band] = float(pct)

        cur.execute("""
            SELECT fund_id, as_on, debt_percent
              FROM india.fund_asset_allocation
        """)
        debt = {(fid, on): float(pct or 0.0) / 100.0
                for fid, on, pct in cur.fetchall()}

        cur.execute("""
            SELECT fund_id, as_on, percent, cap_exempt
              FROM india.fund_issuer_exposure
        """)
        exposures = {}
        for fid, on, pct, exempt in cur.fetchall():
            exposures.setdefault((fid, on), []).append(
                dict(percent=float(pct), cap_exempt=exempt))

    for f in funds:
        key = (f["fund_id"], f["as_on"])
        f["bands"] = bands.get(key, {})
        f["issuer_exposures"] = exposures.get(key, [])
        # HOW MUCH OF THE FUND IS DEBT AT ALL. An equity fund has no bands
        # because it has no bonds, and penalising it for that is wrong in any
        # category holding both. None means unmeasured, which is treated as
        # fully exposed rather than as a free pass.
        f["debt_share"] = debt.get(key)
        f["has_credit"] = bool(f["bands"])
    return funds


def join(funds, by_code, by_name):
    """Attach metrics to funds. Returns (joined, unjoined)."""
    joined, unjoined = [], []
    for f in funds:
        m = by_code.get(f["amfi_scheme_code"]) if f["amfi_scheme_code"] else None
        how = "amfi_code" if m else None
        if m is None:
            m = by_name.get(_normalise(f["amfi_name"] or f["label"]))
            how = "name" if m else None
        if m is None:
            unjoined.append(f)
            continue
        joined.append(dict(f, **{k: v for k, v in m.items()
                                 if k not in ("bands", "issuer_exposures")},
                           joined_by=how))
    return joined, unjoined


def peer_groups(funds, min_peers=FS.MIN_PEERS):
    """{peer_group: [funds]}, rolling small categories up to scheme type.

    A percentile over three funds is 0, 50 and 100 whatever their merits, so a
    category below `min_peers` is scored against its parent instead. Recorded
    per row, because a fund ranked against a broader field is not comparable to
    one ranked against its own.
    """
    by_cat = {}
    for f in funds:
        cat = f.get("category") or f.get("metrics_category") or "(uncategorised)"
        by_cat.setdefault(cat, []).append(f)

    groups, rolled = {}, {}
    for cat, members in by_cat.items():
        if len(members) >= min_peers:
            groups.setdefault(cat, []).extend(members)
        else:
            parent = cat.split(" - ")[0].strip() or "(uncategorised)"
            rolled.setdefault(parent, []).extend(members)
    for parent, members in rolled.items():
        groups.setdefault(parent, []).extend(members)
    return groups


def score_all(funds, min_peers=FS.MIN_PEERS):
    """Every peer group, scored. Groups still too small come back unranked."""
    ranked, unranked = [], []
    for group, members in sorted(peer_groups(funds, min_peers).items()):
        try:
            out = FS.score_category(members, min_peers=min_peers)
        except FS.ScoreError:
            for f in members:
                unranked.append(dict(f, peer_group=group,
                                     reason=f"{len(members)} peers"))
            continue
        for d in out:
            d["peer_group"] = group
        ranked.extend(out)
    return ranked, unranked


def store(conn, ranked, today=None):
    from psycopg2.extras import execute_values

    today = today or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM india.fund_score WHERE last_updated_date=%s",
                    (today,))
        if ranked:
            execute_values(cur, """
                INSERT INTO india.fund_score
                (fund_id, as_on, peer_group, peers, rank, percentile,
                 base_score, credit_multiplier, composite, credit_quality,
                 breach_score, worst_issuer, over_cap, metrics_window,
                 history_years, has_credit, joined_by, last_updated_date)
                VALUES %s
                ON CONFLICT (fund_id, as_on) DO UPDATE SET
                  peer_group=EXCLUDED.peer_group, peers=EXCLUDED.peers,
                  rank=EXCLUDED.rank, percentile=EXCLUDED.percentile,
                  base_score=EXCLUDED.base_score,
                  credit_multiplier=EXCLUDED.credit_multiplier,
                  composite=EXCLUDED.composite,
                  credit_quality=EXCLUDED.credit_quality,
                  breach_score=EXCLUDED.breach_score,
                  worst_issuer=EXCLUDED.worst_issuer,
                  over_cap=EXCLUDED.over_cap,
                  metrics_window=EXCLUDED.metrics_window,
                  history_years=EXCLUDED.history_years,
                  has_credit=EXCLUDED.has_credit,
                  joined_by=EXCLUDED.joined_by,
                  last_updated_date=EXCLUDED.last_updated_date
            """, [(d["fund_id"], d["as_on"], d["peer_group"], d["peers"],
                   d["rank"], d["percentile"], d["base_score"],
                   d["credit_multiplier"], d["composite"],
                   d["credit_quality"], d["breach_score"], d["worst_issuer"],
                   d["over_cap"], d.get("window"), d.get("history_years"),
                   bool(d.get("has_credit")), d.get("joined_by"), today)
                  for d in ranked])
    conn.commit()
    return len(ranked)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--metrics", default=DEFAULT_METRICS)
    ap.add_argument("--category", default=None,
                    help="print this peer group in full and exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args(argv)

    import psycopg2
    conn = psycopg2.connect(a.dsn)
    F.apply_schema(conn)

    rows, by_code, by_name = read_metrics(a.metrics)
    print(f"metrics   {len(rows):,} funds from {a.metrics}")
    funds = stored_funds(conn)
    print(f"stored    {len(funds):,} funds with holdings")

    joined, unjoined = join(funds, by_code, by_name)
    how = {}
    for f in joined:
        how[f["joined_by"]] = how.get(f["joined_by"], 0) + 1
    print(f"joined    {len(joined):,}  ("
          + ", ".join(f"{v:,} by {k}" for k, v in sorted(how.items()))
          + f")   {len(unjoined):,} with no metrics")
    no_credit = [f for f in joined if not f["has_credit"]]
    no_debt = [f for f in no_credit
               if (f["debt_share"] or 0) < FS.CREDIT_MATERIAL_SHARE]
    if no_credit:
        print(f"          {len(no_credit):,} joined funds have no stored "
              f"credit picture: {len(no_debt):,} hold no debt (correct, the "
              f"credit multiplier does not apply) and "
              f"{len(no_credit) - len(no_debt):,} hold debt that did not "
              f"resolve (a real gap, and penalised)")

    ranked, unranked = score_all(joined)
    print(f"ranked    {len(ranked):,} in "
          f"{len({d['peer_group'] for d in ranked})} peer groups; "
          f"{len(unranked):,} in groups too small to rank")

    # STORED BEFORE ANYTHING IS PRINTED. An earlier version returned from the
    # --category branch before writing, so inspecting one group silently
    # skipped the write for all of them -- the scores appeared on screen and
    # the table stayed empty.
    if not a.dry_run:
        print(f"stored    {store(conn, ranked):,} scores\n")

    if a.category:
        members = [d for d in ranked if d["peer_group"] == a.category]
        if not members:
            groups = sorted({d["peer_group"] for d in ranked})
            raise SystemExit(f"no peer group {a.category!r}. Have: "
                             + ", ".join(groups[:40]))
        print(f"\n{a.category}  ({len(members)} funds)")
        for d in members:
            print(f"  {FS.explain(d)}  {d['label'][:44]}")
        return 0

    print()
    for group in sorted({d["peer_group"] for d in ranked}):
        members = [d for d in ranked if d["peer_group"] == group]
        best = members[0]
        print(f"  {group[:44]:<46}{len(members):>4} funds   "
              f"best: {best['label'][:34]:<36}{best['composite']:>6.1f}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
