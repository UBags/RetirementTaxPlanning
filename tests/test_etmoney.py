# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_etmoney.py -- the AMFI-to-ETMoney join, tested without the network.

EVERY TEST HERE IS OFFLINE. The search response is replayed from a real
captured body, so the picker is exercised against the shape ETMoney actually
returns rather than one invented to suit it. Nothing in this file makes an
HTTP request; if it ever starts to, it will hang in CI rather than fail, which
is the reason `search` takes an injectable opener.

THE THREE THAT CARRY THE DESIGN:

  test_a_direct_plan_query_resolves_to_the_regular_id  -- the plan does not
      matter, because a portfolio belongs to the scheme
  test_short_term_debt_does_not_resolve_to_short_term  -- one AMC, two funds,
      different portfolios, four characters apart
  test_a_transport_error_is_not_reported_as_unresolvable -- a bad afternoon of
      connectivity must not become a manual work queue
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ingest import etmoney as E  # noqa: E402

# Captured verbatim from the live endpoint on 2026-08-06.
REAL_RESPONSE = {
    "result": {"success": True,
               "message": "GlobalSearchRegular request fulfilled "
                          "successfully.", "errors": None},
    "stockList": [],
    "mfList": [{"id": 44228,
                "name": "Bajaj Finserv Banking and PSU Fund Regular-Growth",
                "label": "Bajaj Finserv Banking and PSU Fund",
                "seoWebUrl": "/mutual-funds/"
                             "bajaj-finserv-banking-and-psu-fund/44228",
                "securityType": 1}],
}


def _mf(fund_id, label, name=None, slug=None):
    slug = slug or label.lower().replace(" ", "-")
    return {"id": fund_id, "label": label,
            "name": name or f"{label} Regular-Growth",
            "seoWebUrl": f"/mutual-funds/{slug}/{fund_id}", "securityType": 1}


class _FakeOpener:
    """Stands in for urllib.request.urlopen. Records what was posted."""

    def __init__(self, body):
        self.body = body
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req.data.decode())
        payload = json.dumps(self.body).encode()

        class _Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                self_inner.close()

        return _Resp(payload)


# ── the captured response, end to end ───────────────────────────────────────

def test_the_real_captured_response_resolves(monkeypatch):
    op = _FakeOpener(REAL_RESPONSE)
    m = E.resolve("Bajaj Finserv Banking and PSU Fund", opener=op)
    assert m.found
    assert m.fund_id == 44228
    assert m.label == "Bajaj Finserv Banking and PSU Fund"
    assert m.portfolio_url == (
        "https://www.etmoney.com/mutual-funds/"
        "bajaj-finserv-banking-and-psu-fund/portfolio-details/44228")


def test_the_query_is_form_encoded_as_the_endpoint_expects():
    op = _FakeOpener(REAL_RESPONSE)
    E.resolve("Bajaj Finserv Banking and PSU Fund", opener=op)
    assert op.calls == ["query=Bajaj+Finserv+Banking+and+PSU+Fund"]


def test_a_direct_plan_query_resolves_to_the_regular_id():
    """THE PLAN DOES NOT MATTER. Direct and Regular differ in expense ratio
    and NAV, never in what the fund holds, so the Regular id the search
    returns reaches the same portfolio. The AMFI-side Direct/Growth filter is
    deduplication, not selection."""
    op = _FakeOpener(REAL_RESPONSE)
    m = E.resolve("Bajaj Finserv Banking and PSU Fund", opener=op)
    assert m.found and "Regular" in m.name
    assert m.fund_id == 44228


# ── the picker ──────────────────────────────────────────────────────────────

def test_short_term_debt_does_not_resolve_to_short_term():
    """ONE AMC, TWO FUNDS, DIFFERENT PORTFOLIOS. This is the failure that
    would be invisible afterwards: a plausible fund id pointing at the wrong
    holdings."""
    entries = [_mf(1, "HDFC Short Term Fund"),
               _mf(2, "HDFC Short Term Debt Fund")]
    assert E.pick("HDFC Short Term Debt Fund", entries).fund_id == 2
    assert E.pick("HDFC Short Term Fund", entries).fund_id == 1


def test_ampersand_expansion_buys_margin_not_correctness():
    """MEASURED, AFTER A FALSIFIER FAILED TO BITE. Dropping the "&" outright
    still clears the floor at 93.8, so the expansion is not what makes the
    match happen. It is what stops a genuine &/and disagreement from eating
    most of the gap to a similarly-named sibling -- which is the number
    AMBIGUITY_MARGIN is read against. Pinned on the SCORE, since pinning it
    on `found` pins nothing."""
    entries = [_mf(7, "Bajaj Finserv Banking and PSU Fund")]
    m = E.pick("Bajaj Finserv Banking & PSU Fund", entries)
    assert m.found
    assert m.score == 100.0, "the expansion should make these identical"
    assert E.normalise("Banking & PSU") == E.normalise("Banking and PSU")


def test_label_and_name_normalise_identically():
    """ALSO MEASURED. It looked like matching on `name` would be penalised for
    a plan suffix the query was stripped of, but `normalise` removes those
    words from both sides, so the two are byte-identical and the choice
    between them changes nothing."""
    assert E.normalise("Bajaj Finserv Banking and PSU Fund") == \
        E.normalise("Bajaj Finserv Banking and PSU Fund Regular-Growth")


def test_plan_words_are_stripped_from_both_sides():
    """The query has already had them removed by the AMFI suffix stripper, so
    a candidate still carrying them must not be penalised for it."""
    entries = [_mf(9, "DSP Savings Fund",
                   name="DSP Savings Fund Direct Plan-Growth")]
    m = E.pick("DSP Savings Fund", entries)
    assert m.found and m.fund_id == 9
    assert E.normalise("DSP Savings Fund Direct Plan-Growth") == \
        E.normalise("DSP Savings Fund")


def test_a_close_second_is_flagged_ambiguous():
    """1,500 schemes and no prospect of eyeballing them, so the doubtful ones
    have to identify themselves."""
    entries = [_mf(1, "Axis Banking and PSU Debt Fund"),
               _mf(2, "Axis Banking and PSU Debt Fund - Series 2")]
    m = E.pick("Axis Banking and PSU Debt Fund", entries)
    assert m.found and m.runner_up is not None and m.margin is not None


def test_nothing_below_the_floor_resolves():
    """A wrong fund id is worse than a missing one: the missing one is a work
    queue, the wrong one is silent."""
    entries = [_mf(1, "Nippon India Gilt Securities Fund")]
    m = E.pick("Quantum Liquid Fund", entries)
    assert m.found is False
    assert m.reason.startswith("best score")
    assert m.fund_id is None


def test_an_empty_result_list_is_reported_as_no_candidates():
    m = E.pick("Some Fund That Does Not Exist", [])
    assert m.found is False and m.reason == "no candidates"


def test_an_entry_with_no_id_is_dropped_not_returned():
    """Returning it with fund_id None would fail later and further from the
    cause."""
    m = E.pick("Alpha Fund", [{"label": "Alpha Fund", "seoWebUrl": "/x/1"}])
    assert m.found is False and m.reason == "no candidates"


def test_an_api_failure_is_not_a_missing_fund():
    op = _FakeOpener({"result": {"success": False, "message": "rate limited"},
                      "mfList": []})
    m = E.resolve("Anything", opener=op)
    assert m.found is False and "rate limited" in m.reason


def test_a_transport_error_is_not_reported_as_unresolvable():
    """A network error and a fund that does not exist must not look the same,
    or a bad afternoon of connectivity silently becomes a list someone then
    works through by hand."""
    def boom(req, timeout=None):
        raise OSError("connection reset")

    with pytest.raises(OSError):
        E.resolve("Bajaj Finserv Banking and PSU Fund", opener=boom)


# ── the portfolio URL ───────────────────────────────────────────────────────

@pytest.mark.parametrize("seo,expected", [
    ("/mutual-funds/dsp-savings-fund/32350",
     "https://www.etmoney.com/mutual-funds/dsp-savings-fund/"
     "portfolio-details/32350"),
    ("/mutual-funds/invesco-india-largecap-fund/10234",
     "https://www.etmoney.com/mutual-funds/invesco-india-largecap-fund/"
     "portfolio-details/10234"),
])
def test_the_portfolio_url_is_built_from_the_returned_slug(seo, expected):
    """Built from the URL the API returned rather than from the name, so a
    slug ETMoney spells differently cannot produce a 404."""
    assert E.portfolio_url(seo) == expected


@pytest.mark.parametrize("bad", [None, "", "/mutual-funds/no-id-here",
                                 "not-a-url"])
def test_a_malformed_seo_url_yields_no_portfolio_url(bad):
    assert E.portfolio_url(bad) is None


# ── batch reporting ─────────────────────────────────────────────────────────

def test_resolve_all_returns_one_match_per_input_in_order():
    op = _FakeOpener(REAL_RESPONSE)
    out = E.resolve_all(["Bajaj Finserv Banking and PSU Fund",
                         "Bajaj Finserv Banking and PSU Fund"],
                        opener=op, delay=0)
    assert len(out) == 2 and all(m.found for m in out)


def test_summarise_separates_the_two_kinds_of_failure():
    """"No candidates" is a fund ETMoney does not list; "below floor" is one
    it lists under a name too different to trust. They need different
    handling, so they are counted apart."""
    ms = [E.pick("Alpha Fund", [_mf(1, "Alpha Fund")]),
          E.pick("Beta Fund", []),
          E.pick("Gamma Fund", [_mf(2, "Totally Unrelated Scheme")])]
    s = E.summarise(ms)
    assert s == dict(total=3, resolved=1, unresolved=2, ambiguous=0,
                     no_candidates=1, below_floor=1, needed_ladder=0)


# ── the query ladder ────────────────────────────────────────────────────────

class _LadderOpener:
    """Answers only when the query is short enough, as the real endpoint does.

    Measured on the live API: "ICICI Prudential Money Market Fund" returns
    nothing, "ICICI Prudential Money Market" returns the right fund. The cause
    is inside an endpoint nobody documents; this reproduces the behaviour
    rather than explaining it.
    """

    def __init__(self, answers_at, body):
        self.answers_at, self.body, self.queries = answers_at, body, []

    def __call__(self, req, timeout=None):
        import urllib.parse
        q = urllib.parse.parse_qs(req.data.decode())["query"][0]
        self.queries.append(q)
        hit = q == self.answers_at
        payload = json.dumps(dict(self.body,
                                  mfList=self.body["mfList"] if hit else []))

        class _Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                self_inner.close()

        return _Resp(payload.encode())


ICICI = {"result": {"success": True},
         "mfList": [_mf(3068, "ICICI Prudential Money Market Fund",
                        slug="icici-prudential-money-market-fund")]}


def test_a_name_the_endpoint_refuses_is_retried_shorter():
    """THE MEASURED FAILURE. The full name returns zero; dropping the trailing
    word finds the right fund."""
    op = _LadderOpener("ICICI Prudential Money Market", ICICI)
    m = E.resolve("ICICI Prudential Money Market Fund", opener=op)
    assert m.found and m.fund_id == 3068
    assert m.searched_as == "ICICI Prudential Money Market"
    assert m.ladder_steps == 1
    assert op.queries[0] == "ICICI Prudential Money Market Fund"


def test_the_ladder_scores_against_the_full_name_not_the_short_query():
    """RECALL WIDENS, PRECISION DOES NOT MOVE. A shorter query asks a vaguer
    question, but the candidates are still scored against the full original
    name at the same floor -- so dropping words can only find funds that were
    being missed, never admit one that does not match.

    THE CASE HAS TO DISTINGUISH THE TWO READINGS. A first version used a
    candidate that failed under both scorings, so the falsifier did not bite
    and the test pinned nothing. Here "Quantum Liquid Fund" scores 100 against
    the shortened query and 80.9 against the full name, either side of the 88
    floor -- so a fund asking for Series B must NOT be handed the plain
    fund."""
    op = _LadderOpener("Quantum Liquid Fund",
                       {"result": {"success": True},
                        "mfList": [_mf(1, "Quantum Liquid Fund")]})
    m = E.resolve("Quantum Liquid Fund Series B", opener=op)
    assert m.found is False, "a vaguer query must not lower the bar"


def test_the_ladder_stops_at_two_words():
    op = _LadderOpener("nothing matches this", ICICI)
    E.resolve("Alpha Beta Gamma Delta", opener=op)
    assert op.queries[-1] == "Alpha Beta"
    assert all(len(q.split()) >= 2 for q in op.queries)


def test_a_weak_candidate_set_is_no_longer_treated_as_an_answer():
    """THE RULE THIS TEST USED TO PIN HAS BEEN REVERSED, ON EVIDENCE. It
    asserted that a query returning candidates had been ANSWERED and that the
    ladder must stop -- reasonable, and wrong: different rungs return
    different sets, so nothing above the floor means that query did not
    surface the fund, not that the fund is absent.

    The cost is extra requests on a fund that genuinely is not there. The
    benefit is ICICI Prudential Banking & PSU Debt Fund, which scores 100 and
    was being missed."""
    op = _LadderOpener("Alpha Beta Gamma",
                       {"result": {"success": True},
                        "mfList": [_mf(9, "Totally Unrelated Scheme")]})
    m = E.resolve("Alpha Beta Gamma", opener=op)
    assert m.found is False, "still refuses a bad match"
    assert len(op.queries) > 1, "and keeps looking"


def test_a_one_word_name_is_still_searched_once():
    """FOUND BY A FAILING TEST. The rungs were built from a range that came
    out EMPTY for a one-word name, so such a fund returned "no candidates"
    without a single HTTP request -- indistinguishable from the endpoint
    having been asked and said no."""
    assert E.query_ladder("Quantum") == ["Quantum"]
    assert E.query_ladder("") == []
    op = _LadderOpener("Quantum", {"result": {"success": True},
                                   "mfList": [_mf(1, "Quantum")]})
    assert E.resolve("Quantum", opener=op).found
    assert op.queries == ["Quantum"]


def test_the_ladder_can_be_switched_off():
    op = _LadderOpener("ICICI Prudential Money Market", ICICI)
    m = E.resolve("ICICI Prudential Money Market Fund", opener=op, ladder=False)
    assert m.found is False and len(op.queries) == 1


def test_summarise_counts_how_many_needed_the_ladder():
    """If most of 1,500 funds need it, the AMFI-side name extraction is what
    should change, not the retry."""
    op = _LadderOpener("ICICI Prudential Money Market", ICICI)
    ms = [E.resolve("ICICI Prudential Money Market Fund", opener=op),
          E.resolve("ICICI Prudential Money Market", opener=op)]
    assert E.summarise(ms)["needed_ladder"] == 1


# ── the ladder, after the first full-universe run ───────────────────────────

def test_the_distinctive_rung_drops_connectives_and_generic_words():
    """FOUND ON A 2,266-FUND RUN. "ICICI Prudential Banking and PSU Debt Fund"
    returns NOTHING from the endpoint, while "ICICI Prudential Banking PSU"
    returns two funds -- one of which scores 100 against the full name.

    Trailing truncation alone never reaches that rung: it removes PSU before
    it removes "and"."""
    rungs = E.query_ladder("ICICI Prudential Banking and PSU Debt Fund")
    assert rungs[0] == "ICICI Prudential Banking and PSU Debt Fund"
    assert "ICICI Prudential Banking PSU" in rungs
    assert rungs.index("ICICI Prudential Banking PSU") == 1, "tried early"


def test_the_ladder_continues_past_a_weak_candidate_set():
    """THIS REVERSES THE EARLIER RULE, ON EVIDENCE. The old logic stopped at
    the first rung returning candidates, arguing that a query which returned
    something had been answered. But different rungs return DIFFERENT sets: a
    set with nothing above the floor means that query did not surface the
    fund, not that the fund is absent. ICICI Prudential Banking and PSU Debt
    Fund was rejected at 76.6 while the right fund scores 100 in the results of
    a query the ladder never asked."""
    right = _mf(3120, "ICICI Prudential Banking & PSU Debt Fund")
    wrong = _mf(9, "ICICI Prudential Banking and Financial Services Fund")

    class _Rungs:
        def __init__(self):
            self.queries = []

        def __call__(self, req, timeout=None):
            import urllib.parse
            q = urllib.parse.parse_qs(req.data.decode())["query"][0]
            self.queries.append(q)
            # The full name returns a poor set; the distinctive rung returns
            # the right fund alongside it, exactly as the live endpoint does.
            mf = ([wrong] if q == "ICICI Prudential Banking and PSU Debt Fund"
                  else [wrong, right] if q == "ICICI Prudential Banking PSU"
                  else [])

            class _Resp(io.BytesIO):
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    self_inner.close()

            return _Resp(json.dumps(
                {"result": {"success": True}, "mfList": mf}).encode())

    op = _Rungs()
    m = E.resolve("ICICI Prudential Banking and PSU Debt Fund", opener=op)
    assert m.found and m.fund_id == 3120
    assert op.queries[0] == "ICICI Prudential Banking and PSU Debt Fund"
    assert m.searched_as == "ICICI Prudential Banking PSU"


def test_a_vaguer_rung_still_cannot_lower_the_bar():
    """PRECISION DOES NOT MOVE. Every candidate from every rung is scored
    against the FULL original name at the same floor."""
    op = _LadderOpener("Quantum Liquid Fund",
                       {"result": {"success": True},
                        "mfList": [_mf(1, "Quantum Liquid Fund")]})
    assert E.resolve("Quantum Liquid Fund Series B", opener=op).found is False


def test_the_best_rung_is_reported_not_the_first():
    """When no rung clears the floor, the score in the log is the best seen
    anywhere -- so it says how close the search got, rather than what the
    first query happened to return."""
    class _Two:
        """Rung 1 returns something unrelated; a later rung returns a near
        miss. Neither clears 88."""

        def __call__(self, req, timeout=None):
            import urllib.parse
            q = urllib.parse.parse_qs(req.data.decode())["query"][0]
            mf = ([_mf(1, "Totally Unrelated Scheme")]
                  if q == "Alpha Beta Gamma Fund"
                  else [_mf(2, "Alpha Beta Gamma Trust Fund")])

            class _Resp(io.BytesIO):
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    self_inner.close()

            return _Resp(json.dumps(
                {"result": {"success": True}, "mfList": mf}).encode())

    m = E.resolve("Alpha Beta Gamma Fund", opener=_Two())
    assert m.found is False
    assert m.score > 70, "the near miss, not the unrelated first result"


# ── the Direct Plan id ──────────────────────────────────────────────────────

REGULAR_PAGE = (
    '<html><script>var schemeData={'
    'counterpartSchemeUrl:"/mutual-funds/uti-multi-asset-allocation-fund-'
    'direct-growth/15530",counterpartSchemeId:"15530",'
    'currentSchemeUrl:"/mutual-funds/uti-multi-asset-allocation-fund/9467",'
    'currentSchemeId:"9467"}</script></html>')

DIRECT_PAGE = REGULAR_PAGE.replace(
    'currentSchemeUrl:"/mutual-funds/uti-multi-asset-allocation-fund/9467",'
    'currentSchemeId:"9467"',
    'currentSchemeUrl:"/mutual-funds/uti-multi-asset-allocation-fund-'
    'direct-growth/15530",currentSchemeId:"15530"')


def test_the_direct_plan_id_is_read_from_the_regular_page():
    """I ARGUED THE PLAN WAS IRRELEVANT AND THAT WAS ONLY HALF RIGHT. Both
    plans hold the same portfolio -- that part is true -- but they do not share
    an expense ratio or a NAV, and those are what a retirement model runs on.
    Storing the Regular id means every cost number downstream is wrong by 50 to
    100 basis points a year, compounded over thirty years."""
    assert E.direct_plan(REGULAR_PAGE) == (
        15530,
        "/mutual-funds/uti-multi-asset-allocation-fund-direct-growth/15530")


def test_a_page_that_is_already_direct_returns_itself():
    """The search sometimes lands on Direct, and a second hop would be a
    wasted request."""
    assert E.direct_plan(DIRECT_PAGE)[0] == 15530


def test_a_scheme_with_no_counterpart_is_not_an_error():
    """Some schemes have only one plan."""
    assert E.direct_plan("<html>no scheme data</html>") is None


def test_the_quoted_sdweburl_is_a_fallback():
    """schemeData is a JS literal with unquoted keys; sdWebUrl is real JSON
    and survives a change to the literal."""
    page = ('<html>{"sdWebUrl":"/mutual-funds/abc-fund-direct-growth/15530",'
            '"parName":"ABC Fund"}</html>')
    assert E.direct_plan(page) == (
        15530, "/mutual-funds/abc-fund-direct-growth/15530")


def test_the_portfolio_url_follows_the_direct_plan():
    fid, url = E.direct_plan(REGULAR_PAGE)
    assert E.portfolio_url(url).endswith(f"/portfolio-details/{fid}")
    assert "direct-growth" in E.portfolio_url(url)


def test_a_malformed_id_is_refused_rather_than_guessed():
    page = REGULAR_PAGE.replace('counterpartSchemeId:"15530"',
                                'counterpartSchemeId:"abc"')
    assert E.direct_plan(page) is None


# ── punctuation in the query ────────────────────────────────────────────────

def test_punctuation_is_stripped_in_a_later_rung():
    """"UTI Dividend Yield Fund." and "JM Liquid Fund I.E.F." carry stops the
    endpoint does not forgive."""
    rungs = E.query_ladder("UTI Dividend Yield Fund.")
    assert "UTI Dividend Yield Fund" in rungs
    assert "UTI Dividend Yield" in rungs


def test_the_trustmf_case_is_reached_by_the_distinctive_rung():
    """Searching "TRUSTMF Flexi Cap" surfaces it; the full name took Taurus's
    id at 88.4 instead."""
    assert E.query_ladder("TRUSTMF Flexi Cap Fund")[1] == "TRUSTMF Flexi Cap"
