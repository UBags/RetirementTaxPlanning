# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""etmoney.py -- resolve an AMFI scheme name to an ETMoney fund id.

THE JOIN THAT MAKES HOLDINGS REACHABLE. AMFI knows schemes by name and by its
own code; ETMoney knows them by an integer that appears nowhere in any AMFI
feed. `POST /api/stocks/global-search` with the base fund name is the only
bridge, and it returns a LIST, so something has to decide which entry is the
fund that was asked for. That decision is this module; it does not fetch a
portfolio and it does not parse one.

THE PLAN DOES NOT MATTER, WHICH REMOVES HALF THE PROBLEM. Searching for a
Direct Plan name returns the REGULAR id -- "Bajaj Finserv Banking and PSU Fund
Regular-Growth", id 44228 -- and that is fine, because a portfolio is a
property of the SCHEME and not of the plan. Direct and Regular differ in
expense ratio and NAV, never in what the fund holds. So the Direct/Growth
filter on the AMFI side is doing one job only: giving exactly one row per
scheme instead of eight plan variants, which is deduplication rather than
selection.

MATCHING ON `label`, AND IT MAKES NO DIFFERENCE, WHICH IS WORTH SAYING. The
response carries both: `name` is "Bajaj Finserv Banking and PSU Fund
Regular-Growth", `label` is "Bajaj Finserv Banking and PSU Fund". It looked
like `name` would be penalised for a plan suffix the query was stripped of --
but `normalise` removes those words from BOTH sides, so the two forms come out
byte-identical and swapping them changes no test. `label` is used because it is
the form the AMFI base name already resembles; the preference is real and the
claim that it matters was not, and was withdrawn when a falsifier failed to
bite.

QRatio AGAIN, AND FOR THE SAME REASON. It was chosen for issuer names by
measurement: token_set_ratio treats a subset as a perfect match, so "SBI Debt
Fund" would score 100 against "SBI Debt Fund Series C 24"; WRatio falls back to
a partial match when lengths differ and matches long names to short ones on a
shared word. Fund names have both failure shapes in abundance -- "HDFC Short
Term Debt Fund" against "HDFC Short Term Fund" is one AMC, two funds, different
portfolios.

EVERY RESULT CARRIES ITS RUNNER-UP. There are roughly 1,500 schemes and no
prospect of eyeballing them all, so the ambiguous ones have to identify
themselves. A thin margin between the top two is the signal that a human
should look, and it is the only one available without a second source.

WHAT THIS CANNOT FIX. If the search returns nothing, the fund is unresolvable
here and that is the end of it -- there is no second endpoint to try and
guessing a URL from the name would fabricate a fund id. `resolve` returns a
Match with `found=False` and the caller collects those for manual handling.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from rapidfuzz import fuzz, process

SEARCH_URL = "https://www.etmoney.com/api/stocks/global-search"
BASE = "https://www.etmoney.com"

# Sent verbatim from a working browser request. The endpoint rejects a bare
# urllib User-Agent, and `origin`/`referer` are checked -- this is a public
# page's own XHR, not a documented API, so it is only as stable as the page.
HEADERS = {
    "accept": "*/*",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "content-type": "application/x-www-form-urlencoded",
    "origin": BASE,
    "referer": f"{BASE}/mutual-funds/all-funds-listing",
    "user-agent": ("Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
}

# A NAMED CHOICE. Below this the name has not been resolved. Set from the same
# reasoning as the issuer matcher: high enough that "HDFC Short Term Debt Fund"
# does not resolve to "HDFC Short Term Fund", low enough to survive "and" vs
# "&" and the punctuation AMFI and ETMoney disagree about.
MIN_SCORE = 88.0

# Two candidates closer than this are not distinguishable by name alone.
AMBIGUITY_MARGIN = 4.0

# Politeness. ETMoney publishes no rate limit, so this is a guess made in the
# conservative direction: one request per second over ~1,500 schemes is 25
# minutes, which is a one-off and does not need to be faster.
DELAY_SECONDS = 1.0

# Plan and option words. Stripped from BOTH sides, because the query has
# already had them removed by the AMFI-side suffix stripper and a candidate
# that still carries them would score lower for no reason that matters.
_PLAN = re.compile(
    r"\b(?:DIRECT|REGULAR|GROWTH|IDCW|DIVIDEND|PAYOUT|REINVESTMENT|PLAN|"
    r"OPTION|BONUS|RETAIL|INSTITUTIONAL)\b", re.I)
_PUNCT = re.compile(r"[^A-Z0-9 ]+")


class Match(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as exc:
            raise AttributeError(k) from exc


def _match(**kw):
    base = dict(found=False, query=None, fund_id=None, label=None, name=None,
                seo_web_url=None, portfolio_url=None, security_type=None,
                score=0.0, runner_up=None, margin=None, ambiguous=False,
                candidates=0, reason=None, searched_as=None, ladder_steps=0)
    base.update(kw)
    return Match(base)


def normalise(name):
    """A fund name reduced to the part that identifies the SCHEME.

    Applied identically to query and candidate.

    "&" BECOMES "AND", WHICH BUYS MARGIN RATHER THAN CORRECTNESS. AMFI and
    ETMoney disagree about which they write. Measured on "Bajaj Finserv
    Banking & PSU Fund" against the "and" spelling: expanding scores 100,
    dropping the character scores 93.8. Both clear the 88 floor, so this is
    not what makes the match happen -- it is what keeps a genuine &/and
    disagreement from eating most of the gap to a similarly-named sibling
    fund, which is the number AMBIGUITY_MARGIN is read against.
    """
    s = " ".join(str(name or "").upper().split())
    s = s.replace("&", " AND ")
    s = _PUNCT.sub(" ", s)
    s = _PLAN.sub(" ", s)
    return " ".join(s.split())


def search(query, opener=None, timeout=30):
    """POST the search and return the decoded body.

    Raises on transport failure rather than returning an empty list: a network
    error and a fund that genuinely does not exist must not look the same, or
    a bad afternoon of connectivity silently becomes a list of "unresolvable"
    funds that someone then works through by hand.
    """
    data = urllib.parse.urlencode({"query": str(query)}).encode()
    req = urllib.request.Request(SEARCH_URL, data=data, headers=HEADERS,
                                 method="POST")
    op = opener or urllib.request.urlopen
    with op(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# The plan pair, as the fund's own page publishes it:
#
#   schemeData={counterpartSchemeUrl:"/mutual-funds/uti-...-direct-growth/15530",
#               counterpartSchemeId:"15530",
#               currentSchemeUrl:"/mutual-funds/uti-...-fund/9467",
#               currentSchemeId:"9467"}
#
# Unquoted keys, so it is a JS literal and not JSON. Read with a regex rather
# than a parser, because that is all it is.
_SCHEME_FIELD = r'{key}\s*:\s*"([^"]*)"'
_DIRECT_URL = re.compile(r"-direct(?:-|\b)", re.I)


def _scheme_field(html, key):
    m = re.search(_SCHEME_FIELD.format(key=key), html)
    return m.group(1) if m else None


def direct_plan(html):
    """The Direct-Growth (id, url) for a fund, from its main page.

    WHY THIS MATTERS EVEN THOUGH BOTH PLANS HOLD THE SAME PORTFOLIO. I argued
    earlier that the plan was irrelevant because Direct and Regular differ only
    in expense ratio and NAV, never in what the fund holds -- and the holdings
    part is true. But the expense ratio and NAV are exactly what a retirement
    model runs on, and `currentSchemeDetailDto` on the Regular page carries the
    REGULAR expense ratio. Storing the Regular id means every downstream cost
    number is the wrong one, by 50 to 100 basis points a year, compounded over
    thirty years.

    Returns (fund_id, seo_web_url) for the Direct plan, or None when the page
    does not name a counterpart -- some schemes have only one plan, and that
    is not an error.
    """
    current_url = _scheme_field(html, "currentSchemeUrl")
    current_id = _scheme_field(html, "currentSchemeId")
    other_url = _scheme_field(html, "counterpartSchemeUrl")
    other_id = _scheme_field(html, "counterpartSchemeId")

    for url, fid in ((current_url, current_id), (other_url, other_id)):
        if url and fid and _DIRECT_URL.search(url):
            try:
                return int(fid), url
            except ValueError:
                return None
    # Fall back to the quoted sdWebUrl the page also carries, which is JSON and
    # survives a change to the schemeData literal.
    m = re.search(r'"sdWebUrl"\s*:\s*"(/mutual-funds/[^"]*?-direct[^"]*?/'
                  r'(\d+))"', html, re.I)
    if m:
        return int(m.group(2)), m.group(1)
    return None


def fund_page_url(seo_web_url):
    """The fund's main page, from the search result's URL."""
    if not seo_web_url:
        return None
    return BASE + str(seo_web_url)


def portfolio_url(seo_web_url):
    """/mutual-funds/<slug>/<id>  ->  /mutual-funds/<slug>/portfolio-details/<id>

    Built from the URL the API returned rather than from the slug and id
    separately, so a slug ETMoney spells differently from the fund name cannot
    produce a 404.
    """
    if not seo_web_url:
        return None
    parts = str(seo_web_url).rstrip("/").split("/")
    if len(parts) < 2 or not parts[-1].isdigit():
        return None
    return BASE + "/".join(parts[:-1] + ["portfolio-details", parts[-1]])


def pick(query, mf_list, min_score=MIN_SCORE,
         ambiguity_margin=AMBIGUITY_MARGIN, exclude_ids=()):
    """Choose one entry from the search response. Never guesses past the floor.

    `mf_list` is the response's `mfList`. An entry with no id is dropped
    rather than returned with fund_id None, which would fail later and
    further from the cause.
    """
    # `exclude_ids` is how a caller says "not that one" after the id turned
    # out to belong to another scheme. Twelve funds in a full run resolved to
    # a neighbour's id ABOVE the score floor -- TRUSTMF Flexi Cap to Taurus's,
    # UTI Liquid to ITI's -- and were then dropped entirely. The right answer
    # is usually the second candidate, and without this it is never asked for.
    excluded = set(exclude_ids or ())
    entries = [e for e in (mf_list or [])
               if e.get("id") and e["id"] not in excluded]
    if not entries:
        return _match(query=query, reason="no candidates")

    q = normalise(query)
    if not q:
        return _match(query=query, reason="query normalised to nothing")

    choices = [normalise(e.get("label") or e.get("name")) for e in entries]
    scored = process.extract(q, choices, scorer=fuzz.QRatio,
                             limit=len(choices))
    (_, best_score, best_i) = scored[0]

    runner, margin = None, None
    for _, score, i in scored[1:]:
        if entries[i].get("id") != entries[best_i].get("id"):
            runner = entries[i].get("label") or entries[i].get("name")
            margin = best_score - score
            break

    if best_score < min_score:
        return _match(query=query, candidates=len(entries), score=best_score,
                      runner_up=entries[best_i].get("label"),
                      reason=f"best score {best_score:.1f} below {min_score}")

    e = entries[best_i]
    return _match(found=True, query=query, fund_id=e["id"],
                  label=e.get("label"), name=e.get("name"),
                  seo_web_url=e.get("seoWebUrl"),
                  portfolio_url=portfolio_url(e.get("seoWebUrl")),
                  security_type=e.get("securityType"),
                  score=round(best_score, 2), runner_up=runner, margin=margin,
                  candidates=len(entries),
                  ambiguous=bool(margin is not None
                                 and margin < ambiguity_margin))


# The search endpoint returns NOTHING for some exact fund names and finds the
# same fund when a trailing word is dropped. Measured:
#
#     "ICICI Prudential Money Market Fund"  ->  0 results
#     "ICICI Prudential Money Market"       ->  1, id 3068, the right fund
#     "ICICI Money Market"                  ->  1, id 3068, the right fund
#
# while "Bajaj Finserv Banking and PSU Fund" -- longer, and also ending in
# "Fund" -- resolves on the first try. So it is neither length nor the word
# "Fund", and the cause is inside an endpoint nobody documents. Rather than
# theorise, RETRY SHORTER: drop trailing words one at a time until candidates
# come back.
MIN_QUERY_WORDS = 2

# Words that carry no identity. Dropped to form a keyword rung, because the
# endpoint answers a short distinctive query when it will not answer a long
# exact one: "ICICI Prudential Banking and PSU Debt Fund" returns NOTHING,
# while "ICICI Prudential Banking PSU" returns two funds, one of which is the
# right one and scores 100 against the full name.
#
# Trailing truncation alone does not reach that: it removes PSU before it
# removes "and", so the rung that would have worked is never asked.
_PUNCT_IN_QUERY = re.compile(r"[^A-Za-z0-9& ]+")

GENERIC_WORDS = frozenset({
    "and", "of", "the", "for", "fund", "funds", "scheme", "schemes", "plan",
    "debt", "growth", "direct", "regular", "option", "series",
})


def query_ladder(name, minimum=MIN_QUERY_WORDS):
    """Queries to try, in order, until one returns a usable candidate.

    THREE KINDS OF RUNG, and the middle one is the addition:

      1. the full name, which is right when the endpoint cooperates
      2. the DISTINCTIVE words -- connectives and generic fund vocabulary
         removed. This is the rung that finds ICICI Prudential Banking & PSU
         Debt Fund, and no amount of trailing truncation reaches it, because
         truncation removes PSU before it removes "and"
      3. progressively fewer trailing words

    The full name is always first and always present, even when shorter than
    `minimum`: an earlier version built the rungs from a range that came out
    EMPTY for a one-word name, so such a fund was never searched at all.
    """
    words = str(name or "").split()
    if not words:
        return []
    rungs = [" ".join(words)]

    # Punctuation stripped as well as generic words: "UTI Dividend Yield
    # Fund." and "JM Liquid Fund I.E.F." carry stops the endpoint does not
    # forgive, and comparing "fund." against the generic list misses it.
    def _bare(w):
        return _PUNCT_IN_QUERY.sub("", w).lower()

    distinctive = [w for w in words if _bare(w) not in GENERIC_WORDS
                   and _bare(w)]
    if len(distinctive) >= minimum and distinctive != words:
        rungs.append(" ".join(distinctive))

    depunctuated = " ".join(_PUNCT_IN_QUERY.sub(" ", " ".join(words)).split())
    if depunctuated and depunctuated != " ".join(words):
        rungs.append(depunctuated)
        bare = [w for w in depunctuated.split() if w.lower()
                not in GENERIC_WORDS]
        if len(bare) >= minimum:
            rungs.append(" ".join(bare))

    for i in range(len(words) - 1, max(minimum, 1) - 1, -1):
        rungs.append(" ".join(words[:i]))

    seen, out = set(), []
    for r in rungs:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def resolve(name, opener=None, min_score=MIN_SCORE, timeout=30,
            ladder=True, on_retry=None, exclude_ids=()):
    """search + pick, over a ladder of progressively vaguer queries.

    THE LADDER NOW CONTINUES PAST A LOW-SCORING CANDIDATE SET, which reverses
    what this function did before and what its docstring argued for. The old
    rule was that a query returning candidates had been ANSWERED -- the fund is
    not there under that name -- so asking again more vaguely would invite a
    wrong match rather than a missing one.

    The evidence says otherwise. Different rungs return DIFFERENT candidate
    sets, so a set with nothing above the floor does not mean the fund is
    absent; it means that query did not surface it. ICICI Prudential Banking
    and PSU Debt Fund scored 76.6 and was rejected, while the right fund scores
    100 and sits in the results of a shorter query the ladder never asked.

    PRECISION DOES NOT MOVE, WHICH IS WHY THIS IS SAFE. Every candidate from
    every rung is scored against the FULL original name at the same floor, and
    the best across all rungs wins. A vaguer question cannot lower the bar; it
    can only put more candidates in front of it.
    """
    best, tried, errors = None, [], []
    for i, q in enumerate(query_ladder(name) if ladder else [name]):
        try:
            body = search(q, opener=opener, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # THE ENDPOINT 400s ON SOME PUNCTUATION and the full name is the
            # rung most likely to carry it: "CRISIL IBX 50:50 Gilt Plus SDL",
            # "Axis Children's Fund", "Equity Hybrid'95". Sixty-one funds in
            # one run died here -- not because the fund was missing, but
            # because the FIRST query was rejected and the exception took the
            # whole ladder with it. A later rung has the punctuation stripped
            # and usually works.
            errors.append(f"{q!r}: HTTP {exc.code}")
            continue
        result = body.get("result") or {}
        if result.get("success") is False:
            return _match(query=name, reason=f"api: "
                          f"{result.get('message') or 'failed'}")
        tried.append(q)
        candidates = body.get("mfList") or []
        if not candidates:
            if on_retry and i == 0:
                on_retry(name)
            continue

        # Scored against the FULL name, never against the shortened query.
        m = pick(name, candidates, min_score=min_score,
                 exclude_ids=exclude_ids)
        m["query"] = name
        m["searched_as"] = q
        m["ladder_steps"] = i
        if m["found"]:
            return m
        if best is None or (m["score"] or 0) > (best["score"] or 0):
            best = m

    if best is not None:
        best["ladder_steps"] = len(tried) - 1
        return best
    if errors and not tried:
        # Every rung was rejected. That is a different answer from "the fund
        # is not listed", and conflating them would send a punctuation problem
        # to the same place as a genuinely absent scheme.
        return _match(query=name,
                      reason=f"all queries rejected: {errors[0]}",
                      ladder_steps=len(errors) - 1)
    return _match(query=name, reason="no candidates",
                  searched_as=tried[-1] if tried else name,
                  ladder_steps=max(0, len(tried) - 1))


def resolve_all(names, opener=None, delay=DELAY_SECONDS, on_each=None):
    """Resolve a list, politely. Returns [Match] in input order.

    A transport error is allowed to propagate. Swallowing it here would turn a
    connectivity problem into a list of funds that look unresolvable, and the
    difference matters because one is retried and the other is worked by hand.
    """
    out = []
    for i, n in enumerate(names):
        m = resolve(n, opener=opener)
        out.append(m)
        if on_each:
            on_each(i, n, m)
        if delay and i < len(names) - 1:
            time.sleep(delay)
    return out


def summarise(matches):
    resolved = [m for m in matches if m["found"]]
    return dict(
        total=len(matches), resolved=len(resolved),
        unresolved=len(matches) - len(resolved),
        ambiguous=sum(1 for m in resolved if m["ambiguous"]),
        no_candidates=sum(1 for m in matches
                          if m["reason"] == "no candidates"),
        needed_ladder=sum(1 for m in matches
                          if m["found"] and m["ladder_steps"]),
        below_floor=sum(1 for m in matches
                        if (m["reason"] or "").startswith("best score")))
