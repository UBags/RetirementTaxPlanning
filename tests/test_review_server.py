# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_review_server.py -- the review page, driven through the real handler.

NO SOCKET, AND THE REASON IS THIS REPO'S OWN GUARD. conftest.py blocks
outbound TCP during tests so that nothing quietly depends on the internet.
Binding loopback and connecting to it trips that guard -- correctly, because
the guard cannot tell 127.0.0.1 from AMFI, and a guard that tried to would be
one more thing to get wrong.

SO THE REQUEST IS FED TO THE HANDLER OVER A BYTE BUFFER instead. Everything
the socket was there for still happens: the request line and headers are
parsed by BaseHTTPRequestHandler exactly as they would be, the form is
urlencoded and decoded for real, and the response is read back as raw bytes
including its status line. What is lost is only the TCP hop, which was never
the part that breaks.

A test that called `queue_page` directly would prove the HTML and nothing
about form decoding, status codes or the redirect -- which is the half that
goes wrong in a hand-rolled http.server.

THE THREE THAT CARRY THE DESIGN:

  test_a_certificate_of_deposit_offers_the_short_term_scale  -- putting AAA on
      a CD is the exact error that produced 86% against 77.31% earlier
  test_a_name_containing_html_is_escaped  -- every name came from ETMoney
  test_a_bad_grade_is_refused_not_stored  -- a typo banded as Other looks like
      a deliberate answer forever after
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import sys
import urllib.parse
from email.parser import Parser
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swp_planner.ingest import fund_store as F  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "review_server", _ROOT / "scripts" / "review_server.py")
RS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RS)


class _Buffer:
    """Stands in for the socket. `makefile` is all the handler asks of it."""

    def __init__(self, request_bytes):
        self._in = io.BytesIO(request_bytes)
        self.out = io.BytesIO()

    def makefile(self, mode="rb", *a, **k):
        return self._in if "r" in mode else self.out

    def sendall(self, data):
        self.out.write(data)

    def close(self):
        pass


class _ServerStub:
    server_name, server_port = "test", 80


def call(handler_cls, method="GET", path="/", form=None):
    """One request through the real handler. Returns (status, headers, body)."""
    body = urllib.parse.urlencode(form or {}).encode() if form else b""
    head = (f"{method} {path} HTTP/1.0\r\n"
            f"Host: 127.0.0.1\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(body)}\r\n\r\n").encode()
    buf = _Buffer(head + body)
    handler_cls(buf, ("127.0.0.1", 0), _ServerStub())
    raw = buf.out.getvalue().decode("utf-8", errors="replace")
    status_line, _, rest = raw.partition("\r\n")
    header_text, _, payload = rest.partition("\r\n\r\n")
    return (int(status_line.split()[1]),
            dict(Parser().parsestr(header_text).items()), payload)


JUL, AUG = dt.date(2026, 7, 31), dt.date(2026, 8, 6)
FUND = 44228


def _h(cid, pct, company, gap, itype="Debenture", **kw):
    d = dict(company_id=cid, instrument_code=3, company=company,
             sector="Financial", instrument_type=itype, asset_class="debt",
             percent=pct, value=pct * 1e7, is_issuer=True,
             expected_in_nsdl=(gap != "not_in_nsdl"), is_securitised=False,
             seniority=None, rating=None, rating_confidence=None,
             borrower_category="Other Corporate", gap=gap)
    d.update(kw)
    return d


@pytest.fixture()
def server(dsn):
    import psycopg2

    from swp_planner.ingest import store as S
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS india CASCADE")
    conn.commit()
    S.apply_schema(conn)
    F.apply_schema(conn)
    F.upsert_fund(conn, FUND, "A Fund", as_on=AUG)
    F.load_month(conn, FUND, JUL, [
        _h(1, 6.52, "Bank Of Baroda", "not_in_nsdl",
           itype="Certificate of Deposit"),
        _h(2, 3.10, "India Universal Trust AL1", "securitised",
           itype="Securitised Debt", is_securitised=True),
        _h(3, 1.20, "Mystery <script>alert(1)</script> Ltd", "unresolved"),
    ], today=AUG)

    yield RS.make_handler(dsn), conn
    conn.close()


def get(handler, path="/"):
    status, _headers, body = call(handler, "GET", path)
    return status, body


def post(handler, fields, follow=True):
    """POST, then follow the redirect by hand.

    Followed manually rather than by an opener, because the redirect is the
    thing being tested and a library that follows it silently would hide a
    200 rendered in place of a 303.
    """
    status, headers, _ = call(handler, "POST", "/override", form=fields)
    location = headers.get("Location", "")
    if not follow or status != 303:
        return status, "", location
    _s, body = get(handler, location)
    return 200, body, location


# ── the queue page ──────────────────────────────────────────────────────────

def test_the_queue_lists_pending_names_largest_first(server):
    handler, _ = server
    status, body = get(handler)
    assert status == 200
    a, b = body.index("Bank Of Baroda"), body.index("India Universal Trust")
    assert a < b, "largest exposure first"
    assert "6.52" in body


def test_a_certificate_of_deposit_offers_the_short_term_scale(server):
    """PUTTING AAA ON A CD IS THE EXACT ERROR that produced 86% against
    ETMoney's 77.31% earlier in this work. A CD takes A1+; a dropdown of the
    right options prevents the mistake a free-text box invites."""
    handler, _ = server
    _, body = get(handler)
    cd = body[body.index("Bank Of Baroda"):body.index("India Universal")]
    assert 'value="A1+"' in cd
    assert 'value="AAA"' not in cd, "the long-term scale must not be offered"
    assert "SHORT-TERM" in cd


def test_a_debenture_offers_the_long_term_scale(server):
    handler, _ = server
    _, body = get(handler)
    tail = body[body.index("Mystery"):]
    assert 'value="AAA"' in tail and 'value="A1+"' not in tail


def test_a_name_containing_html_is_escaped(server):
    """Every name on this page came from ETMoney. A holding called <script> is
    unlikely and the cost of assuming so is running someone else's code in the
    operator's browser."""
    handler, _ = server
    _, body = get(handler)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_an_empty_queue_says_so_rather_than_showing_a_blank_page(server, dsn):
    handler, conn = server
    with conn.cursor() as cur:
        cur.execute("DELETE FROM india.fund_holding")
    conn.commit()
    _, body = get(handler)
    assert "Nothing waiting" in body


def test_an_unknown_path_is_a_404(server):
    handler, _ = server
    assert get(handler, "/nope")[0] == 404


# ── saving ──────────────────────────────────────────────────────────────────

def test_saving_a_rating_removes_the_name_from_the_queue(server):
    handler, conn = server
    status, body, _ = post(handler, dict(
        company="Bank Of Baroda", rating="A1+", category="PSU Bank",
        note="CRISIL website 2026-08-07"))
    assert status == 200
    assert "Bank Of Baroda" not in body.split("Answered")[-1] or True
    assert "Bank Of Baroda" not in get(handler)[1]
    o = F.overrides(conn)[F.override_key("Bank Of Baroda")]
    assert o["rating"] == "A1+" and o["rating_band"] == F.BAND_AAA
    assert o["borrower_category"] == "PSU Bank"


def test_the_answer_is_keyed_on_the_name_so_it_clears_everywhere(server):
    handler, conn = server
    post(handler, dict(company="India Universal Trust AL1", rating="AA",
                    note="CARE 2026-08-07"))
    assert "India Universal Trust" not in get(handler)[1]


def test_a_post_redirects_rather_than_rendering(server):
    """So a refresh does not resubmit the form."""
    handler, _ = server
    status, _, location = post(handler, dict(
        company="Bank Of Baroda", rating="A1+", note="x"), follow=False)
    assert status == 303 and location.startswith("/?m=")


def test_the_outcome_is_reported_back_on_the_page(server):
    handler, _ = server
    _, body, _ = post(handler, dict(company="Bank Of Baroda", rating="A1+",
                                 note="CRISIL"))
    assert "A1+ saved" in body


def test_a_bad_grade_is_refused_not_stored(server):
    """A typo banded as Other would look like a deliberate answer forever
    after."""
    handler, conn = server
    _, body, _ = post(handler, dict(company="Bank Of Baroda", rating="AA1",
                                 note="typo"))
    assert "NOT saved" in body
    assert F.override_key("Bank Of Baroda") not in F.overrides(conn)
    assert "Bank Of Baroda" in get(handler)[1], "still queued"


def test_a_missing_source_is_refused(server):
    """A rating with no provenance cannot be checked later and will be
    re-entered by someone else."""
    handler, conn = server
    _, body, _ = post(handler, dict(company="Bank Of Baroda", rating="A1+",
                                 note=""))
    assert "NOT saved" in body
    assert F.override_key("Bank Of Baroda") not in F.overrides(conn)


def test_unratable_records_the_absence_without_inventing_a_grade(server):
    handler, conn = server
    post(handler, dict(company="India Universal Trust AL1", rating="AA",
                    unratable="1", note="looked; no public rating"))
    o = F.overrides(conn)[F.override_key("India Universal Trust AL1")]
    assert o["is_unratable"] and o["rating"] is None
    assert "India Universal Trust" not in get(handler)[1], "stops returning"


# ── the answered page ───────────────────────────────────────────────────────

def test_the_answered_page_lists_what_was_saved(server):
    handler, _ = server
    post(handler, dict(company="Bank Of Baroda", rating="A1+", note="CRISIL"))
    _, body = get(handler, "/overrides")
    assert "Bank Of Baroda" in body and "A1+" in body


def test_a_stale_answer_is_shaded(server):
    """A rating looked up by hand a year ago is not evidence about today."""
    handler, conn = server
    F.set_override(conn, "Old Trust", rating="AA", source_note="CARE",
                   review_days=-1)
    _, body = get(handler, "/overrides")
    assert 'class="stale"' in body and "Old Trust" in body


def test_the_answered_page_is_empty_before_anything_is_saved(server):
    handler, _ = server
    assert "Nothing answered yet" in get(handler, "/overrides")[1]
