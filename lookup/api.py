# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""api.py -- rating and borrower category, from an ISIN or from a vague name.

TWO ENTRY POINTS.

    by_isin("INE062A08462")            exact, then triangulated
    by_name("7.90% HDFC Bank Ltd 28")  fuzzy, then triangulated

SENIORITY IS "senior", "subordinated" OR "unknown", AND UNKNOWN IS THE
DEFAULT. It is a property of the INSTRUMENT, not the borrower: an issuer can
have AAA senior paper and a perpetual outstanding at the same time and they
are not the same risk. So it is stated only when an ISIN identified the actual
bond, and mutual fund holdings frequently carry no ISIN at all -- a factsheet
line naming a borrower does not say WHICH of their instruments is held.

UNKNOWN RATHER THAN A DEFAULT, EVERYWHERE. There is no fallback to "senior".
Reporting a perpetual as senior paper is worse than reporting nothing, because
nothing prompts a question and "senior" does not. Three separate paths return
unknown: no ISIN was supplied, the ISIN did not resolve to a stored
instrument, or the stored data cannot distinguish seniority at all (see
`seniority_available`).

What a NAME can still answer is `issuer_has_subordinated` -- the warning that
this borrower has such paper in issue, even though which instrument is held is
not known.

THIS IS THE RISK THAT DOES NOT APPEAR IN A RETURN SERIES. A perpetual that
never triggers prices and moves like the senior paper of the same issuer, so
its historical sigma says nothing about the loss it carries. The loss arrives
as a write-down to zero on a regulatory trigger, with the senior debt
money-good the same day.

BOTH RETURN THE SAME SHAPE and both always return it. There is no None and no
exception for "not found": a caller sizing a portfolio needs an answer for
every line, and an absent answer that looks like an error gets skipped, which
is the same as treating the holding as riskless. `found` and `confidence` say
how much to trust it; `source` says how it was arrived at.

THE FOUR WAYS AN ISIN RESOLVES, IN ORDER.

  isin              the ISIN is stored and carries a rating. Done.
  issuer            the ISIN is stored with no rating -- unrated instruments
                    are common in private placements -- so the issuer's WORST
                    current rating across everything else they have out is
                    used instead.
  isin_prefix       the ISIN is not stored at all, but characters 1-7 identify
                    the ISSUER and those are. INE062A is State Bank of India
                    whatever the last five characters say, so a bond issued
                    yesterday still resolves to a borrower today.
  not_found         nothing matched. Category defaults to Other Corporate,
                    rating stays None, confidence 0.

WORST RATING, NOT LATEST, WHEN TRIANGULATING. An issuer with AAA senior paper
and an A+ subordinated line is not an AAA borrower. IL&FS was rated AAA on its
senior programme weeks before it defaulted. The conservative reading is the one
a concentration cap should see.

THE FUZZY PATH, AND WHY IT REPORTS ITS RUNNER-UP. Fund factsheets write
"HDFC Bank Ltd.", "HDFC Ltd", "HDB Financial Services Ltd" and "Housing
Development Finance Corp" -- four different borrowers whose names overlap
heavily. A single best match with a high score hides that the second-best was
one point behind. So every fuzzy result carries `runner_up` and `margin`, and
`ambiguous` is set when the margin is thin. A thin margin on a large holding is
worth a human look; a thin margin on a 0.2% line is not.

WHY QRatio, MEASURED RATHER THAN ASSUMED. Three scorers were run over the same
factsheet lines against the same index, and they do not agree:

  token_set_ratio  treats one string being a SUBSET of the other as perfect,
                   so "HDFC" scores 100 against "HDFC BANK LIMITED". HDFC Ltd
                   and HDFC Bank were different borrowers with different
                   ratings for most of the period this model covers.
  token_sort_ratio strict, but it resolved "IL&FS Financial Services" to HDB
                   Financial Services -- 81.8 against 78.3 -- because the two
                   share every token but one. Wrong company, confidently.
  WRatio           falls back to a PARTIAL match when the two strings differ
                   much in length, so "Zzyzx Trading Company of Nowhere"
                   scored 85.5 against "Bank of India" on the word "of" alone
                   and would have resolved.
  QRatio           rejects Zzyzx at 37.8, gets IL&FS right at 95.7 over HDB at
                   86.4, and separates "HDFC Ltd" from HDFC Bank at 61.5.

So QRatio, at MIN_SCORE 80. All four numbers above are reproducible from the
test fixture; none of this was reasoned from first principles, because the
first principles gave the wrong answer twice.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz, process

from fund_filters import borrower as _borrower
from fund_filters import instruments as _instruments

# Below this the name has not been resolved and the answer is not_found.
# A NAMED CHOICE, tuned so "HDFC" alone does not resolve to HDFC Bank.
MIN_SCORE = 80.0

# When the best and second-best are closer than this, the answer is flagged
# ambiguous. Names in this file overlap heavily and a two-point win is not one.
AMBIGUITY_MARGIN = 5.0

# Instrument grammar, stripped before matching. These tokens appear in every
# description and carry no information about WHO borrowed -- leaving them in
# lets two unrelated bonds score high on their shared boilerplate.
_NOISE = re.compile(
    r"\b(?:"
    r"NCD|BD|OCD|CCD|LOA|PP|PPD|SR|SERIES|TRANCHE|TR|OPT|OPTION|"
    r"PDI|PERPETUAL|PERP|PERPT|PERPATUAL|TIER|SUBDEBT|SUB\s*DEBT|AT1|"
    r"SECURED|UNSECURED|REDEEMABLE|NON[- ]?CONVERTIBLE|CONVERTIBLE|"
    r"DEBENTURE[S]?|BOND[S]?|PAPER|CERTIFICATE|DEPOSIT|"
    r"FVRS[0-9A-Z]*|FV|RS|LAC|LACS|CR|CRORE|INR|"
    r"CUM|NON[- ]?CUM|CUMULATIVE|MONTHLY|ANNUAL|QUARTERLY|TAXABLE|TAXFREE"
    r")\b", re.I)
# Coupons, dates in the NSDL compact form (17OT28), percentages, bare numbers.
_NUMERIC = re.compile(r"\b\d+(?:\.\d+)?%?\b|\b\d{2}[A-Z]{2}\d{2}\b", re.I)
# Series numerals. AN EXPLICIT LIST, because a character-class pattern for
# roman numerals also matches "IL", which turned IL&FS Financial Services into
# "&FS" and let it resolve to HDB Financial Services -- a defaulted borrower
# read as a live one.
_ROMAN = re.compile(
    r"\b(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|"
    r"XVIII|XIX|XX|XXI|XXII|XXIII|XXIV|XXV|XXVI|XXVII|XXVIII|XXIX|XXX)\b")
_PUNCT = re.compile(r"[^A-Z0-9& ]+")
_ISIN = re.compile(r"\b(IN[EFD0-9][0-9A-Z]{9})\b", re.I)

# Corporate suffixes. Removed from BOTH sides so "Ltd" against "Limited" is not
# a difference, but the distinctive part of the name is left intact. INDIA is
# NOT in this list, deliberately: stripping it reduces "Bank of India" to
# "BANK OF", which is very nearly contentless and collides with every other
# bank whose name ends the same way.
_SUFFIX = re.compile(
    r"\b(?:LIMITED|LTD|PRIVATE|PVT|COMPANY|CO|CORPORATION|CORP|"
    r"INCORPORATED|INC|LLP|PLC)\b", re.I)


# ── SENIORITY, AS THREE PLAIN STRINGS ───────────────────────────────────────
# NOT AN ENUM, DELIBERATELY. instruments.py already expresses every tier as a
# plain string constant, this value is stored in a TEXT column, and it crosses
# into JSON on the way to any caller. An enum would need converting at all
# three boundaries to buy type-checking on a field with three values, and the
# comparison `res.seniority == SUBORDINATED` reads the same either way.
SENIOR = "senior"
SUBORDINATED = "subordinated"
SENIORITY_UNKNOWN = "unknown"

# THE TIER IS NOT THE SENIORITY, WHICH THE FIRST VERSION OF THIS GOT WRONG. It
# returned classify()'s tier straight out, so a government bond came back with
# seniority "sovereign" -- a category error, because sovereign is a kind of
# BORROWER and seniority is a rank in a capital structure. The borrower is
# already reported separately.
_SENIORITY_BY_TIER = {
    _instruments.SUBORDINATED: SUBORDINATED,
    _instruments.CORPORATE: SENIOR,
    _instruments.SOVEREIGN: SENIOR,
    _instruments.STATE: SENIOR,
    _instruments.MONEY_MARKET_PAPER: SENIOR,
    _instruments.UNKNOWN: SENIORITY_UNKNOWN,
}


def seniority_of(tier):
    """The rank in the capital structure implied by a tier.

    Anything not in the table is UNKNOWN rather than SENIOR: a tier this code
    does not recognise is not evidence that the bond is senior.
    """
    return _SENIORITY_BY_TIER.get(tier, SENIORITY_UNKNOWN)


class Result(dict):
    """A dict, so it serialises and prints without ceremony."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as exc:
            raise AttributeError(k) from exc


def _result(**kw):
    base = dict(found=False, isin=None, issuer_name=None, issuer_key=None,
                rating=None, rating_rank=None, borrower_category=
                _borrower.DEFAULT, source="not_found", confidence=0.0,
                runner_up=None, margin=None, ambiguous=False,
                instrument_count=None, not_cooperating=None,
                seniority=SENIORITY_UNKNOWN, issuer_has_subordinated=None,
                issuer_subordinated_count=None)
    base.update(kw)
    return Result(base)


def normalise(name):
    """A company name reduced to the part that identifies it.

    Applied identically to the query and to the index, because a comparison
    between differently-cleaned strings is not a comparison.
    """
    s = " ".join(str(name or "").upper().split())
    s = _ISIN.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    s = _NUMERIC.sub(" ", s)
    s = _NOISE.sub(" ", s)
    s = _ROMAN.sub(" ", s)
    s = _SUFFIX.sub(" ", s)
    return " ".join(s.split())


class CreditLookup:
    """Both APIs, over one connection.

    The issuer index is built once on first use and held. It is a few thousand
    names; rebuilding it per call would make the fuzzy path unusable on a
    portfolio of a hundred lines.
    """

    def __init__(self, conn, min_score=MIN_SCORE,
                 ambiguity_margin=AMBIGUITY_MARGIN):
        self.conn = conn
        self.min_score = min_score
        self.ambiguity_margin = ambiguity_margin
        self._index = None      # [(normalised, issuer_key)]
        self._choices = None    # [normalised]
        self._seniority_ok = None

    # ── the index ──────────────────────────────────────────────────────────

    def _build_index(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT issuer_key, issuer_name "
                        "FROM india.issuer_current")
            rows = cur.fetchall()
        pairs, seen = [], set()
        for key, name in rows:
            for candidate in (name, key):
                n = normalise(candidate)
                if n and (n, key) not in seen:
                    seen.add((n, key))
                    pairs.append((n, key))
        self._index = pairs
        self._choices = [p[0] for p in pairs]

    @property
    def index(self):
        if self._index is None:
            self._build_index()
        return self._index

    # ── issuer-level facts ─────────────────────────────────────────────────

    @property
    def seniority_available(self):
        """Can the stored data distinguish senior from subordinated at all?

        THE GUARD AGAINST A CONFIDENT WRONG ANSWER. classify() can only reach
        SUBORDINATED if the description and maturity rules are in the tree. If
        they are not, every row was stored as CORPORATE and mapping that to
        "senior" would report a perpetual as senior paper -- which is worse
        than reporting nothing, because nothing prompts a question and "senior"
        does not.

        The test is on the DATA rather than on the code, because the table may
        have been written by an earlier run: if not one row in the current
        picture is subordinated, the rule was not running when it was written.
        Cached, because it cannot change inside one process.

        The false positive is a universe that genuinely contains no
        subordinated paper, which would then report every bond as unknown. On
        the NSDL active list that is 201 rows of 28,745, so it does not arise;
        and erring toward unknown is the direction that costs nothing.
        """
        if self._seniority_ok is None:
            with self.conn.cursor() as cur:
                cur.execute("SELECT EXISTS (SELECT 1 FROM "
                            "india.debt_security_current WHERE tier = %s)",
                            (_instruments.SUBORDINATED,))
                self._seniority_ok = bool(cur.fetchone()[0])
        return self._seniority_ok

    def _seniority(self, tier):
        if not self.seniority_available:
            return SENIORITY_UNKNOWN
        return seniority_of(tier)

    def _issuer(self, issuer_key):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT issuer_key, issuer_name, borrower_category, "
                "       worst_rating_grade, worst_rating_rank, "
                "       instrument_count, any_not_cooperating, "
                "       subordinated_count "
                "FROM india.issuer_current WHERE issuer_key = %s",
                (issuer_key,))
            return cur.fetchone()

    def _from_issuer(self, issuer_key, source, confidence, **extra):
        row = self._issuer(issuer_key)
        if not row:
            return _result(**extra)
        key, name, cat, grade, rank, count, nc, sub = row
        return _result(found=True, issuer_key=key, issuer_name=name,
                       rating=grade, rating_rank=rank,
                       borrower_category=cat or _borrower.DEFAULT,
                       source=source, confidence=confidence,
                       instrument_count=count, not_cooperating=nc,
                       issuer_subordinated_count=sub,
                       issuer_has_subordinated=bool(sub), **extra)

    # ── API 1: by ISIN ─────────────────────────────────────────────────────

    def by_isin(self, isin):
        """Rating and borrower category for one ISIN. Always returns a Result."""
        code = (isin or "").strip().upper()
        if not code:
            return _result()

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT isin, issuer_name, issuer_key, rating_grade, "
                "       rating_rank, borrower_category, not_cooperating, "
                "       tier "
                "FROM india.debt_security_current WHERE isin = %s",
                (code,))
            row = cur.fetchone()

        if row:
            found_isin, name, key, grade, rank, cat, nc, tier = row
            if grade is not None:
                return _result(found=True, isin=found_isin.strip(),
                               issuer_name=name, issuer_key=key, rating=grade,
                               rating_rank=rank,
                               borrower_category=cat or _borrower.DEFAULT,
                               source="isin", confidence=1.0,
                               not_cooperating=nc,
                               seniority=self._seniority(tier))
            # Stored but unrated: triangulate across the issuer's other paper.
            # The SENIORITY still comes from this instrument, not the issuer:
            # borrowing the issuer's rating is a judgement about the borrower,
            # but seniority is a fact about the bond and is already known.
            res = self._from_issuer(key, "issuer", 0.75, isin=code)
            if res["found"]:
                res["seniority"] = self._seniority(tier)
                return res
            return _result(found=True, isin=found_isin.strip(),
                           issuer_name=name, issuer_key=key,
                           borrower_category=cat or _borrower.DEFAULT,
                           source="isin", confidence=0.5,
                           seniority=self._seniority(tier))

        # Not stored. Characters 1-7 are the issuer and may well be known.
        if len(code) >= 7:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT issuer_key, COUNT(*) "
                    "FROM india.debt_security_current "
                    "WHERE LEFT(isin, 7) = %s GROUP BY issuer_key "
                    "ORDER BY COUNT(*) DESC LIMIT 1", (code[:7],))
                hit = cur.fetchone()
            if hit:
                return self._from_issuer(hit[0], "isin_prefix", 0.6,
                                         isin=code)
        return _result(isin=code)

    # ── API 2: by vague instrument name ────────────────────────────────────

    def by_name(self, name, limit=3):
        """Rating and borrower category for a holding line from a factsheet.

        An ISIN anywhere in the string wins outright -- factsheets often carry
        one, and an exact key should never lose to a fuzzy match on the same
        row.
        """
        raw = str(name or "")
        m = _ISIN.search(raw)
        if m:
            res = self.by_isin(m.group(1).upper())
            if res["found"]:
                return res

        query = normalise(raw)
        if not query:
            return _result()

        matches = process.extract(query, self.index, scorer=fuzz.QRatio,
                                  processor=lambda x: x[0] if isinstance(
                                      x, tuple) else x, limit=max(limit, 2))
        if not matches:
            return _result()

        (best_norm, best_key), best_score = matches[0][0], matches[0][1]
        runner, margin = None, None
        for cand, score, _ in matches[1:]:
            if cand[1] != best_key:
                runner, margin = cand[1], best_score - score
                break

        if best_score < self.min_score:
            return _result(runner_up=best_key, margin=None,
                           confidence=best_score / 100.0)

        res = self._from_issuer(
            best_key, "name", round(best_score / 100.0, 4),
            runner_up=runner, margin=margin,
            ambiguous=bool(margin is not None
                           and margin < self.ambiguity_margin))
        res["matched_on"] = best_norm
        return res

    def by_names(self, names):
        """Convenience for a whole portfolio. Same order as the input."""
        return [self.by_name(n) for n in names]
