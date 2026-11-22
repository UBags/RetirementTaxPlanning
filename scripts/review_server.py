#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""review_server.py -- the rating queue, as a local web page.

    python3 scripts/review_server.py
    then open http://127.0.0.1:8765

STDLIB ONLY, DELIBERATELY. Flask or FastAPI would be three lines shorter and
would put a web framework in a project whose only other dependencies are a
database driver, a spreadsheet reader and a fuzzy matcher. `http.server` is
enough for a single-user form on localhost, and one file with no new
requirements is one fewer thing to keep current.

BINDS TO 127.0.0.1 AND NOTHING ELSE, by default. There is no login, no CSRF
token and no audit beyond `entered_by`, because the threat model is one person
on one machine. `--host` exists for the case where that is genuinely wanted and
it prints a warning, because a queue that writes credit ratings into a planning
model should not be reachable from a hotel network by accident.

EVERY NAME ON THIS PAGE CAME FROM ETMONEY, so every name is escaped on the way
out. A fund holding called `<script>` is unlikely and the cost of assuming so
is a page that runs someone else's code in the operator's browser.

THE SCALE PROMPT IS THE POINT OF THE PAGE, not the form. A certificate of
deposit takes a SHORT-TERM grade -- A1+ -- and a debenture takes a long-term
one, and putting AAA on a CD is the exact error that produced 86% against
ETMoney's 77.31% earlier in this work. So each row states which scale it wants
and offers only those grades. A dropdown of the right options prevents the
mistake that a free-text box invites.
"""
from __future__ import annotations

import argparse
import html
import pathlib
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fund_filters import borrower as _borrower  # noqa: E402
from ingest import fund_store as F  # noqa: E402

DEFAULT_DSN = "dbname=RetirementPlanning"
DEFAULT_PORT = 8765

# Which grades to offer, by why the holding could not be resolved. A
# certificate of deposit is short-term paper and a debenture is not; offering
# both lists everywhere is how the wrong one gets picked.
SHORT_TERM = ["A1+", "A1", "A2+", "A2", "A3+", "A3", "A4+", "A4", "D"]
LONG_TERM = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB",
             "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "C", "D"]

SCALE_FOR_GAP = {
    "not_in_nsdl": ("short", SHORT_TERM,
                    "Money market paper. Enter the SHORT-TERM grade (A1+ and "
                    "below) -- the issuer's long-term grade is a different "
                    "scale and does not apply to this instrument."),
    "securitised": ("long", LONG_TERM,
                    "A securitisation trust. The rating is on the TRUST, not "
                    "on the originator; if none exists, tick unratable."),
    "unresolved": ("long", LONG_TERM,
                   "The name did not match anything in the NSDL file. Check "
                   "the spelling first -- a rating here overrides nothing, it "
                   "fills a hole."),
}

STYLE = """
body{font:14px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#111}
header{background:#1f2937;color:#fff;padding:14px 22px}
header h1{margin:0;font-size:17px;font-weight:600}
header .sub{opacity:.75;font-size:12px;margin-top:2px}
nav a{color:#93c5fd;margin-right:16px;text-decoration:none;font-size:13px}
main{padding:22px;max-width:1100px}
.row{background:#fff;border:1px solid #e3e6ea;border-radius:8px;
     padding:14px 16px;margin-bottom:12px}
.name{font-weight:600;font-size:15px}
.meta{color:#555;font-size:12px;margin:4px 0 10px}
.hint{background:#fff8e6;border-left:3px solid #e0a800;padding:7px 10px;
      font-size:12px;margin-bottom:10px;border-radius:0 4px 4px 0}
.pct{float:right;font-variant-numeric:tabular-nums;font-weight:600}
label{font-size:12px;color:#444;margin-right:4px}
select,input[type=text]{font:13px system-ui;padding:5px 6px;
      border:1px solid #ccd0d5;border-radius:4px}
input[type=text]{min-width:240px}
button{background:#1f6feb;color:#fff;border:0;border-radius:4px;
       padding:6px 14px;font:13px system-ui;cursor:pointer}
button.sec{background:#6b7280}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #e3e6ea;padding:6px 9px;text-align:left;font-size:13px}
th{background:#eef1f4}
.stale{background:#fdecec}
.ok{color:#137333}.warn{color:#9a3412}
.empty{background:#fff;border:1px solid #e3e6ea;border-radius:8px;
       padding:28px;text-align:center;color:#555}
"""


def e(v):
    """Escape. Every name here came from ETMoney."""
    return html.escape("" if v is None else str(v), quote=True)


def _page(title, body, note=""):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{e(title)}</title><style>{STYLE}</style></head><body>
<header><h1>Credit rating review</h1>
<div class="sub">{e(note)}</div>
<nav><a href="/">Queue</a><a href="/overrides">Answered</a></nav></header>
<main>{body}</main></body></html>"""


def queue_page(conn, message=""):
    rows = F.pending_reviews(conn, limit=300)
    banner = (f'<div class="hint">{e(message)}</div>' if message else "")
    if not rows:
        return _page("Queue", banner + '<div class="empty">Nothing waiting. '
                     'Every holding above the floor has a rating.</div>',
                     "0 names waiting")

    cats = [""] + list(_borrower.CATEGORIES)
    parts = [banner]
    for r in rows:
        gap = r["gap"] or "unresolved"
        _scale, grades, hint = SCALE_FOR_GAP.get(gap,
                                                 SCALE_FOR_GAP["unresolved"])
        opts = "".join(f'<option value="{e(g)}">{e(g)}</option>'
                       for g in [""] + grades)
        catopts = "".join(f'<option value="{e(c)}">{e(c or "-")}</option>'
                          for c in cats)
        parts.append(f"""<div class="row">
<span class="pct">{e(r['largest_percent'])}%</span>
<div class="name">{e(r['company'])}</div>
<div class="meta">{e(gap)} &middot; {e(r['instrument_type'] or 'unknown')}
 &middot; {e(r['funds_affected'])} fund(s) &middot; as on
 {e(r['latest_as_on'])}</div>
<div class="hint">{e(hint)}</div>
<form method="post" action="/override">
<input type="hidden" name="company" value="{e(r['company'])}">
<label>Rating</label><select name="rating">{opts}</select>
<label>Borrower</label><select name="category">{catopts}</select>
<label>Source</label><input type="text" name="note"
  placeholder="e.g. CRISIL website, 2026-08-07" required>
<label><input type="checkbox" name="unratable" value="1"> no rating exists
</label>
<button type="submit">Save</button>
</form></div>""")
    return _page("Queue", "".join(parts), f"{len(rows)} names waiting")


def overrides_page(conn, message=""):
    data = F.overrides(conn)
    if not data:
        return _page("Answered", '<div class="empty">Nothing answered yet.'
                     '</div>', "0 answered")
    import datetime as dt
    today = dt.date.today()
    rows = "".join(
        f'<tr class="{"stale" if o["review_by"] < today else ""}">'
        f'<td>{e(o["display_name"])}</td><td>{e(o["rating"] or "-")}</td>'
        f'<td>{e(o["rating_band"] or "-")}</td>'
        f'<td>{e(o["borrower_category"] or "-")}</td>'
        f'<td>{"yes" if o["is_unratable"] else ""}</td>'
        f'<td>{e(o["review_by"])}</td></tr>'
        for o in sorted(data.values(), key=lambda d: d["review_by"]))
    body = (f'<div class="hint">{e(message)}</div>' if message else "") + f"""
<p class="meta">Rows shaded red are past their review date. A rating looked up
by hand a year ago is not evidence about today.</p>
<table><tr><th>Issuer</th><th>Rating</th><th>Band</th><th>Borrower</th>
<th>Unratable</th><th>Review by</th></tr>{rows}</table>"""
    return _page("Answered", body, f"{len(data)} answered")


def make_handler(dsn):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RTPReview/1.0"

        def _send(self, body, status=200, headers=None):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _conn(self):
            import psycopg2
            return psycopg2.connect(dsn)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            msg = (q.get("m") or [""])[0]
            conn = self._conn()
            try:
                if path in ("/", "/queue"):
                    self._send(queue_page(conn, msg))
                elif path == "/overrides":
                    self._send(overrides_page(conn, msg))
                else:
                    self._send(_page("Not found",
                                     '<div class="empty">No such page.</div>'),
                               404)
            finally:
                conn.close()

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != "/override":
                self._send(_page("Not found", "<p>No.</p>"), 404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(
                self.rfile.read(length).decode("utf-8"))

            def field(name):
                return (form.get(name) or [""])[0].strip()

            company = field("company")
            unratable = bool(form.get("unratable"))
            rating = field("rating") or None
            conn = self._conn()
            try:
                F.set_override(
                    conn, company, rating=None if unratable else rating,
                    borrower_category=field("category") or None,
                    source_note=field("note"),
                    entered_by=field("by") or "web",
                    unratable=unratable)
                msg = (f"{company}: recorded as unratable" if unratable
                       else f"{company}: {rating or 'no rating'} saved")
            except ValueError as exc:
                # Refused rather than stored. A bad grade banded as Other would
                # look like a deliberate answer forever after.
                msg = f"{company}: NOT saved -- {exc}"
            finally:
                conn.close()
            self._send("", 303,
                       {"Location": "/?m=" + urllib.parse.quote(msg)})

        def log_message(self, fmt, *args):
            sys.stderr.write("  %s\n" % (fmt % args))

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1",
                    help="localhost only by default; there is no login here")
    a = ap.parse_args(argv)

    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding {a.host}, which is reachable from other "
              f"machines.\n         There is no login on this page and it "
              f"writes credit ratings\n         into a planning model.")

    httpd = ThreadingHTTPServer((a.host, a.port), make_handler(a.dsn))
    print(f"review queue on http://{a.host}:{a.port}   (ctrl-c to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
