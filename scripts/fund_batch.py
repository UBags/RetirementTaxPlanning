#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""fund_batch.py -- every fund AMFI lists, resolved, fetched and stored.

    python3 scripts/fund_batch.py --limit 25        # try it small first
    python3 scripts/fund_batch.py                   # the whole universe
    python3 scripts/fund_batch.py --retry-errors    # just the ones that broke
    python3 scripts/fund_batch.py --reapply         # after answering the queue

RESUMABLE, BECAUSE HALF AN HOUR IS LONG ENOUGH FOR SOMETHING TO GO WRONG.
Roughly fifteen hundred funds at one request a second is thirty minutes, over
which a laptop lid, a dropped connection or an ETMoney hiccup is likely rather
than possible. Every outcome is logged, funds already done today are skipped,
and restarting costs nothing.

FAILURES ARE ROWS, NOT EXCEPTIONS. One fund that will not resolve must not lose
the fourteen hundred that did. `no_match` is a fund ETMoney does not list --
closed-end schemes, segregated portfolios, funds too new for a retail platform
-- and retrying it this afternoon will not help. `error` is a timeout or a
parse failure and may well succeed next pass. They are counted apart because
the response to each differs.

--reapply IS THE ONE TO RUN AFTER THE REVIEW QUEUE. Overrides are consulted at
load time, so a rating answered in the web page does nothing to holdings
already stored -- and re-fetching will not fix that either, because the
snapshot hash correctly declines to rewrite a month whose content has not
changed. This fills them in place.

ONE REQUEST A SECOND, BY DEFAULT. ETMoney publishes no rate limit, so the guess
is made in the conservative direction. This is a job to start and leave.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import pathlib
import sys
import time
import urllib.request

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ingest import amfi_schemes as A  # noqa: E402
from ingest import etmoney as E  # noqa: E402
from ingest import etmoney_portfolio as P  # noqa: E402
from ingest import fund_store as F  # noqa: E402
from ingest import store as S  # noqa: E402
from lookup.api import CreditLookup  # noqa: E402

DEFAULT_DSN = "dbname=RetirementPlanning"
DELAY = 1.0

# How many times to try the next-best candidate after a colliding id. Bounded
# so a chain of collisions cannot loop; three is enough for the observed cases
# and each costs one more search.
MAX_CONFLICT_RETRIES = 3


def fetch_html(url, timeout=45):
    req = urllib.request.Request(url, headers={
        "accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "accept-encoding": "gzip", "user-agent": E.HEADERS["user-agent"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def do_one(conn, name, lookup, load_fund, latest_only=False, today=None,
           fetch=fetch_html, resolve=None, scheme=None, exclude_ids=()):
    """One fund, from AMFI name to stored months. Never raises.

    Returns the log row it wrote, so a caller can print it without asking the
    database again.
    """
    today = today or _dt.date.today()
    resolve = resolve or E.resolve
    def _log(row):
        # ROLL BACK BEFORE LOGGING. A psycopg2 error inside load_fund leaves
        # the connection in an aborted transaction, and every later statement
        # on it -- including the one recording the failure -- raises
        # InFailedSqlTransaction. The run then dies on the FIRST bad fund with
        # an error that says nothing about the actual cause, which is exactly
        # what "failures are rows, not exceptions" was meant to prevent.
        try:
            conn.rollback()
        except Exception:                          # noqa: BLE001
            pass
        F.log_attempt(conn, name, on=today, **row)
        return dict(row, amfi_name=name)

    clock = {}
    t0 = time.perf_counter()
    try:
        # exclude_ids only on a retry, so a caller-supplied `resolve` stub
        # that does not accept it still works on the common path.
        m = (resolve(name, exclude_ids=exclude_ids) if exclude_ids
             else resolve(name))
    except Exception as exc:                       # noqa: BLE001
        return _log(dict(status="error",
                         reason=f"search: {type(exc).__name__}: "
                                f"{str(exc)[:180]}"))

    if not m["found"]:
        return _log(dict(status="no_match", reason=m["reason"]))

    clock["search"] = time.perf_counter() - t0

    # ONE ETMONEY FUND IS ONE AMFI FUND, and the floor alone does not enforce
    # it. Measured on a real run, four schemes took a neighbour's id at scores
    # ABOVE the 88 floor: TRUSTMF Flexi Cap took Taurus's 44630 at 88.4, UTI
    # Short Duration took Union's at 91.7, ICICI Prudential Short Term took the
    # Gilt fund's at 92.8. Raising the floor would lose legitimate matches;
    # refusing a second claim on one id costs nothing and catches all four.
    #
    # The symptom was visible and would have been missed: the second fund
    # reported "0mo" because every month it wrote was already there.
    owner = F.claimed_by(conn, m["fund_id"], on=today)
    if owner and owner != name:
        # RETRY WITH THE NEXT CANDIDATE RATHER THAN GIVING UP. Twelve funds in
        # a full run hit this and were dropped entirely, but the id belonging
        # to another scheme does not mean this scheme is absent -- it means
        # the top candidate was the wrong one. The second is usually right.
        # Bounded: one extra attempt per collision, and the excluded set grows
        # so a chain of collisions terminates.
        if len(exclude_ids) < MAX_CONFLICT_RETRIES:
            return do_one(conn, name, lookup, load_fund,
                          latest_only=latest_only, today=today, fetch=fetch,
                          resolve=resolve, scheme=scheme,
                          exclude_ids=tuple(exclude_ids) + (m["fund_id"],))
        return _log(dict(status="conflict", fund_id=m["fund_id"],
                         match_score=m["score"],
                         reason=f"id {m['fund_id']} already resolved from "
                                f"{owner!r} (scored {m['score']})"))

    fund_id, portfolio = m["fund_id"], m["portfolio_url"]
    seo = m["seo_web_url"]
    try:
        # THE SEARCH RETURNS WHICHEVER PLAN IT FEELS LIKE, usually Regular.
        # Both plans hold the same portfolio, but they do NOT share an expense
        # ratio or a NAV -- and those are what a retirement model runs on. A
        # Regular id means every cost number downstream is wrong by 50 to 100
        # basis points a year, compounded over thirty years.
        #
        # One extra request, and only when the search did not already land on
        # a Direct URL.
        if not E._DIRECT_URL.search(seo or ""):
            t1 = time.perf_counter()
            try:
                direct = E.direct_plan(fetch(E.fund_page_url(seo)))
            except Exception:                      # noqa: BLE001
                # A 404 HERE IS NOT A REASON TO LOSE THE FUND. The main page
                # can be gone while the portfolio page still answers, and the
                # Regular id is a worse answer than none at all only for the
                # expense ratio. Fall back rather than fail.
                direct = None
            clock["plan"] = time.perf_counter() - t1
            if direct:
                fund_id, seo = direct[0], direct[1]
                portfolio = E.portfolio_url(seo)
                # A Direct id may already be claimed by the scheme whose
                # Regular id we started from, so the collision check has to
                # run again on the id actually used.
                owner2 = F.claimed_by(conn, fund_id, on=today)
                if owner2 and owner2 != name:
                    if len(exclude_ids) < MAX_CONFLICT_RETRIES:
                        return do_one(conn, name, lookup, load_fund,
                                      latest_only=latest_only, today=today,
                                      fetch=fetch, resolve=resolve,
                                      scheme=scheme,
                                      exclude_ids=tuple(exclude_ids)
                                      + (m["fund_id"],))
                    return _log(dict(status="conflict", fund_id=fund_id,
                                     match_score=m["score"],
                                     reason=f"direct id {fund_id} already "
                                            f"resolved from {owner2!r}"))
        t1 = time.perf_counter()
        html = fetch(portfolio)
        clock["fetch"] = time.perf_counter() - t1
        clock["bytes"] = len(html)
        t1 = time.perf_counter()
        payload = P.extract_payload(html)
        clock["parse"] = time.perf_counter() - t1
        t1 = time.perf_counter()
        months = [max(payload["mfHoldingMap"])] if latest_only else None
        s = scheme or {}
        codes = s.get("scheme_codes") or []
        verdicts = load_fund(conn, payload, fund_id, m["label"], lookup,
                             months=months, amfi_name=name,
                             seo_web_url=seo, score=m["score"],
                             today=today, category=s.get("category"),
                             scheme_type=s.get("scheme_type"),
                             amc=s.get("amc"),
                             amfi_scheme_code=codes[0] if codes else None)
        clock["store"] = time.perf_counter() - t1
        row = dict(status="ok", fund_id=fund_id, match_score=m["score"],
                   months_written=sum(1 for v in verdicts.values()
                                      if v != "unchanged"),
                   reason=None)
    except Exception as exc:                       # noqa: BLE001
        row = dict(status="error", fund_id=fund_id,
                   match_score=m["score"],
                   reason=f"{type(exc).__name__}: {str(exc)[:180]}")
    out = _log(row)
    out["clock"] = clock
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N funds; try 25 before the whole file")
    ap.add_argument("--delay", type=float, default=DELAY)
    ap.add_argument("--latest-only", action="store_true",
                    help="store only the newest month per fund")
    ap.add_argument("--names-file", default=None,
                    help="one fund name per line, instead of downloading AMFI")
    ap.add_argument("--retry-errors", action="store_true",
                    help="only names that errored today")
    ap.add_argument("--reapply", action="store_true",
                    help="fill stored holdings from overrides; no fetching")
    ap.add_argument("--timings", action="store_true",
                    help="print search/fetch/parse/store seconds per fund")
    # RESTORED AFTER A REGRESSION. A later rewrite of this file dropped the
    # review-queue arguments entirely, so refresh.sh's final step failed with
    # "unrecognized arguments: --queue" and the manual queue became
    # unreachable from the pipeline. The web page still worked, which is why
    # it went unnoticed for two runs.
    ap.add_argument("--queue", action="store_true",
                    help="print what needs a person, and exit")
    ap.add_argument("--override", metavar="NAME",
                    help="record a hand-found rating for this issuer")
    ap.add_argument("--rating", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--note", default="", help="where the rating was found")
    ap.add_argument("--by", default="manual")
    ap.add_argument("--unratable", action="store_true",
                    help="a person looked and there is no rating to find")
    ap.add_argument("--force", action="store_true",
                    help="ignore today's log and attempt everything")
    a = ap.parse_args(argv)

    import psycopg2
    conn = psycopg2.connect(a.dsn)
    S.apply_schema(conn)
    F.apply_schema(conn)
    today = _dt.date.today()

    if a.override:
        F.set_override(conn, a.override, rating=a.rating,
                       borrower_category=a.category, source_note=a.note,
                       entered_by=a.by, unratable=a.unratable)
        print(f"recorded {a.override!r} -> {a.rating or 'unratable'}")
        return 0

    if a.queue:
        q = F.pending_reviews(conn)
        print(f"{len(q)} names need a person, largest exposure first\n")
        print(f"{'largest%':>9} {'funds':>6}  {'why':<13}name")
        for r in q:
            print(f"{r['largest_percent']:>9} {r['funds_affected']:>6}  "
                  f"{r['gap']:<13}{r['company']}")
        return 0

    if a.reapply:
        # ORDER MATTERS AND IT IS THE OPPOSITE OF WHAT IT WAS. The rules run
        # first and hand answers land last, because an override exists exactly
        # where the rules have nothing to say. Run the other way round, the
        # re-enrichment wrote its answer back over seventeen overrides and
        # returned Karur Vysya, Titan, DBS and Julius Baer to the queue the
        # same afternoon they were cleared.
        #
        # The ordering is belt; rating_source = 'override' is braces. Either
        # alone would do today, and the column is what survives someone
        # reordering this list.
        m = F.reclassify_non_issuers(conn)
        print(f"reclassified {m:,} holdings that are not borrowers")
        s = F.reclassify_sovereign(conn)
        print(f"answered {s:,} government holdings by rule")
        bf = F.backfill_company_names(conn)
        print(f"named {bf['rows_filled']:,} holdings from {bf['known_ids']:,} "
              f"known company ids"
              + (f" ({bf['ambiguous_ids']:,} ids left alone: two names)"
                 if bf["ambiguous_ids"] else ""))
        from lookup import portfolio_credit as _pc
        from lookup.api import CreditLookup as _CL
        rep = F.reenrich(conn, _pc.enrich, _CL(conn),
                         progress=lambda msg: print(f"  {msg}", flush=True))
        print(f"re-enriched {rep['keys']:,} distinct holdings; "
              f"{rep['rows_changed']:,} rows changed")
        n = F.reapply_overrides(conn)
        print(f"filled {n:,} holdings from the override table")
        return 0

    if a.names_file:
        names = [ln.strip() for ln in
                 pathlib.Path(a.names_file).read_text().splitlines()
                 if ln.strip()]
        schemes = {}
        print(f"{len(names):,} names from {a.names_file}")
    else:
        print("downloading AMFI NAVAll.txt ...")
        funds = A.load()
        names = [f["name"] for f in funds]
        schemes = {f["name"]: f for f in funds}
        print(f"{len(names):,} funds after Direct-Plan-Growth deduplication")
        cats = {f.get("category") for f in funds if f.get("category")}
        print(f"{len(cats)} categories, which is what fund_score ranks within")

    if a.retry_errors:
        done = F.already_done(conn, on=today, statuses=("ok", "no_match"))
        names = [n for n in names if n not in done]
        errored = F.already_done(conn, on=today, statuses=("error",))
        names = [n for n in names if n in errored]
        print(f"{len(names):,} errored today")
    elif not a.force:
        done = F.already_done(conn, on=today)
        if done:
            names = [n for n in names if n not in done]
            print(f"{len(done):,} already done today; {len(names):,} to go")

    if a.limit:
        names = names[:a.limit]

    lookup = CreditLookup(conn)
    # Imported here so the module is loaded once rather than per fund.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fund_load", _ROOT / "scripts" / "fund_load.py")
    fund_load = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fund_load)

    totals = {}
    started = time.time()
    for i, name in enumerate(names, 1):
        # PRINTED BEFORE THE WORK, NOT AFTER. Each fund is two HTTP round
        # trips against pages of 0.4 to 4.7 MB, so a line that appears only on
        # completion leaves the run looking hung for however long the slowest
        # fetch takes. flush because stdout is block-buffered the moment this
        # is piped to tee, which is how anyone runs a two-hour job.
        print(f"  [{i}/{len(names)}] {name[:52]:<54}", end="", flush=True)
        row = do_one(conn, name, lookup, fund_load.load_fund,
                     latest_only=a.latest_only, today=today,
                     scheme=schemes.get(name))
        mark = {"ok": "ok", "no_match": "--", "error": "!!",
                "conflict": "><"}.get(row["status"], "??")
        detail = (f"id {row.get('fund_id')} "
                  f"{row.get('months_written', 0)}mo"
                  if row["status"] == "ok" else (row.get("reason") or "")[:52])
        c = row.get("clock") or {}
        timing = ""
        if a.timings and c:
            timing = ("  " + " ".join(
                f"{k}={c[k]:.1f}s" for k in ("search", "plan", "fetch",
                                             "parse", "store") if k in c)
                + (f" {c['bytes'] / 1e6:.1f}MB" if "bytes" in c else ""))
        print(f"{mark} {detail}{timing}", flush=True)
        for k, v in c.items():
            if k != "bytes":
                totals[k] = totals.get(k, 0.0) + v
        if a.delay and i < len(names):
            time.sleep(a.delay)

    mins = (time.time() - started) / 60
    print(f"\n{len(names):,} attempted in {mins:.1f} min")
    if totals:
        # WHERE THE TIME WENT, measured rather than assumed. Local work
        # profiles at a few seconds a fund; if `fetch` dominates, no amount of
        # tuning this code helps and the answer is concurrency or a smaller
        # page, not a faster loop.
        print("  time by step: " + "  ".join(
            f"{k} {v / 60:.1f} min" for k, v in sorted(
                totals.items(), key=lambda kv: -kv[1])))
    for s in F.run_summary(conn, on=today):
        print(f"  {s['status']:<10}{s['funds']:>6} funds  "
              f"{s['months']:>7} months")
    bad = F.failures(conn, on=today)
    if bad:
        print(f"\nnot ok ({len(bad)} shown):")
        for b in bad:
            print(f"  {b['status']:<10}{b['amfi_name'][:44]:<46}"
                  f"{(b['reason'] or '')[:56]}")
    q = F.pending_reviews(conn, limit=10)
    if q:
        print(f"\ntop of the review queue ({len(q)} shown):")
        for r in q:
            print(f"  {r['largest_percent']:>7}%  {r['funds_affected']:>4} "
                  f"funds  {r['gap']:<13}{r['company']}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
