# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""nsdl_securities.py -- fetch the NSDL active securities list, and load it.

THE ENDPOINT, ESTABLISHED BY BISECTION RATHER THAN BY GUESSING:

    GET https://www.indiabondinfo.nsdl.com/bds-service/v1/public/bdsinfo/
        listofsecurities?type=Active

    NO COOKIES NEEDED. A request carrying no cookies at all returns 200 and
    2.9MB. The session cookies that appear in a browser capture are incidental.

    A REALISTIC USER-AGENT IS NEEDED. With 'User-Agent: Mozilla/5.0' the
    connection is RESET AT THE TLS LAYER -- OpenSSL SSL_read errno 104, no HTTP
    status at all. That is a load balancer dropping the connection, so it
    presents as a network fault rather than a rejection and will look like an
    outage to anyone debugging it later.

    HEAD IS NOT SUPPORTED. It returns 247 bytes of text/html under a 200. Any
    liveness check must be a GET.

    type= ACCEPTS ONLY 'Active'. Redeemed, All and Inactive each return 400.

THE SURVIVORSHIP PROBLEM, WHICH IS THE MOST IMPORTANT THING ON THIS PAGE. The
file contains only instruments that still exist. DHFL's paper is not in it.
Neither is IL&FS's. Every rating history built from this source is therefore
free of every instrument that actually defaulted -- the bias is in the source,
not in our code, and no parser fixes it.

What CAN be recovered is disappearance. From the first download onward, an
instrument present in one snapshot and absent from the next has either redeemed
or defaulted, and the two are distinguishable by whether its redemption date
had arrived. That is why load() records which ISINs went missing rather than
only what is present.

THERE IS NO AS-ON DATE. Not in the file, not in Content-Disposition, not in
Last-Modified. The fetch timestamp is all there is, which means a stale file
served by NSDL would be undetectable. Recorded here so nobody later reads
`as_on` as authoritative.

The byte length also varies between identical requests -- 2,919,397, 2,919,514
and 2,919,709 across three pulls minutes apart -- so the workbook is generated
per request and raw-byte hashing cannot establish identity. The content hash is
taken over the PARSED ROWS instead.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re

from fund_filters import instruments as _instruments
from fund_filters import ratings as _ratings

URL = ("https://www.indiabondinfo.nsdl.com/bds-service/v1/public/bdsinfo/"
       "listofsecurities")
BOOTSTRAP_URL = "https://www.indiabondinfo.nsdl.com/CBDServices/"

# The bare minimum that works. The User-Agent is not politeness -- a short one
# gets the connection reset before HTTP is spoken.
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Referer": BOOTSTRAP_URL,
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": ("Mozilla/5.0 (X11; Ubuntu; Linux x86_64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/150.0.0.0 Safari/537.36"),
}

EXPECTED_HEADERS = [
    "Sr. No.", "ISIN", "Name of Issuer", "Series", "Security Description",
    "Type of Instrument", "Mode of Issue", "Category of Issue",
    "Face Value(in Rs.)", "Issue Size(in Rs.)", "Date of Allotment",
    "Date of Redemption/Conversion", "Coupon Rate (%)", "Coupon Type",
    "Frequency of Interest Payment", "Credit Rating", "Business Sector",
    "Type of Issuer-Ownership", "Type of Issuer-Nature", "Instrument Status",
]

MIN_PLAUSIBLE_ROWS = 5000       # a real file has ~28,700; 68 bytes is a 400


class FetchError(RuntimeError):
    """Raised rather than returning a short body that parses to nothing."""


def fetch(dest, session=None, type_="Active", bootstrap=False, timeout=120):
    """Download the workbook. Returns (path, bytes_written).

    VALIDATES BY OPENING THE WORKBOOK, not by trusting the extension. A 400
    response is 68 bytes of JSON, and saved under a .xlsx name it would sail
    through every check that looks at the filename.
    """
    import requests

    s = session or requests.Session()
    if bootstrap:
        # Not required today. Kept because the NetScaler cookies come off the
        # landing page with no login, so if they ever start mattering the
        # sequence already works.
        s.get(BOOTSTRAP_URL, headers=HEADERS, timeout=timeout)

    r = s.get(URL, params={"type": type_}, headers=HEADERS, timeout=timeout)
    if r.status_code != 200:
        raise FetchError(f"{r.status_code} from NSDL: {r.text[:200]}")
    if len(r.content) < 100_000:
        raise FetchError(
            f"only {len(r.content)} bytes; a real workbook is about 2.9MB. "
            f"Body starts: {r.content[:200]!r}")
    with open(dest, "wb") as fh:
        fh.write(r.content)
    return dest, len(r.content)


def _norm(v):
    return " ".join(str(v).split()) if v is not None else ""


def _date(v):
    """NSDL writes dd-mm-yyyy. Returns a date or None."""
    t = _norm(v)
    if not t:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return _dt.datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def _number(v):
    t = _norm(v).replace(",", "").rstrip("%")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None



_SUFFIX_RE = re.compile(
    r"\b(LIMITED|LTD|PRIVATE|PVT|CORPORATION|CORP|COMPANY|CO|INCORPORATED|"
    r"INC|AND|&)\b", re.I)
_PUNCT_RE = re.compile(r"[^A-Z0-9 ]")


def canonical_issuer(name):
    """A stable key for one borrower.

    THE HARD PART OF THIS WHOLE PIPELINE. 'DEWAN HOUSING FIN CORP LTD', 'DHFL'
    and 'Dewan Housing Finance Corporation Limited' are one company and three
    strings, and exposures do not aggregate until they are one row. This
    normalises the easy half -- case, punctuation and corporate suffixes -- and
    deliberately does NOT attempt fuzzy matching, because a wrong merge
    understates concentration in exactly the direction that hurts and would be
    invisible afterwards. Everything it cannot resolve stays separate and shows
    up as a distinct issuer for a human to look at.
    """
    up = _norm(name).upper()
    up = _PUNCT_RE.sub(" ", up)
    up = _SUFFIX_RE.sub(" ", up)
    return " ".join(up.split())


def parse_workbook(path, sheet=None):
    """Read the workbook into dicts, one per instrument.

    Raises on an unexpected header row rather than mapping columns by position:
    NSDL could reorder or insert a column and a positional reader would load
    coupon rates into the rating field without complaint.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    header = [_norm(h) for h in next(rows)]
    idx = {h: i for i, h in enumerate(header) if h}

    missing = [h for h in ("ISIN", "Name of Issuer", "Credit Rating")
               if h not in idx]
    if missing:
        raise ValueError(f"unexpected header: missing {missing}. "
                         f"Got {header[:8]}")

    out = []
    for r in rows:
        isin = _norm(r[idx["ISIN"]]) if idx.get("ISIN") is not None else ""
        if not isin:
            continue
        raw_rating = _norm(r[idx["Credit Rating"]]) \
            if "Credit Rating" in idx else ""
        parsed = _ratings.split_multi(raw_rating)
        worst = _ratings.worst(parsed)
        issuer = _norm(r[idx["Name of Issuer"]])
        description = _norm(r[idx["Security Description"]]) \
            if "Security Description" in idx else ""
        instrument_type = _norm(r[idx["Type of Instrument"]]) \
            if "Type of Instrument" in idx else ""
        # SENIORITY COMES OUT OF THIS FILE'S OWN TWO TEXT COLUMNS. There is no
        # SECTYPE here, so without them classify() can only reach CORPORATE and
        # every perpetual in the outstanding universe is sized at the general
        # cap on a 45% recovery assumption. Subordination is invisible in a
        # return series -- an AT1 that never triggers moves like senior paper
        # -- so the words are the only place the risk shows up in advance.
        redemption = _date(r[idx["Date of Redemption/Conversion"]]) \
            if "Date of Redemption/Conversion" in idx else None
        # THE UNION OF TWO INCOMPLETE TESTS. The words miss 67 perpetuals that
        # carry no seniority term at all; the sentinel misses 9 whose issuers
        # filed a call date instead, plus every genuinely dated Tier 2 bond.
        tier = _instruments.classify(
            None, isin, f"{instrument_type} {description}",
            maturity=redemption)["tier"]
        out.append(dict(
            isin=isin, issuer=issuer, issuer_key=canonical_issuer(issuer),
            tier=tier,
            description=description,
            instrument_type=instrument_type,
            sector=_norm(r[idx["Business Sector"]])
            if "Business Sector" in idx else "",
            ownership=_norm(r[idx["Type of Issuer-Ownership"]])
            if "Type of Issuer-Ownership" in idx else "",
            status=_norm(r[idx["Instrument Status"]])
            if "Instrument Status" in idx else "",
            allotment=_date(r[idx["Date of Allotment"]])
            if "Date of Allotment" in idx else None,
            redemption=redemption,
            coupon=_number(r[idx["Coupon Rate (%)"]])
            if "Coupon Rate (%)" in idx else None,
            face_value=_number(r[idx["Face Value(in Rs.)"]])
            if "Face Value(in Rs.)" in idx else None,
            issue_size=_number(r[idx["Issue Size(in Rs.)"]])
            if "Issue Size(in Rs.)" in idx else None,
            raw_rating=raw_rating, ratings=parsed,
            worst_grade=worst["grade"] if worst else None,
            worst_rank=worst["rank"] if worst else None,
            enhanced=any(p["enhanced"] for p in parsed),
            not_cooperating=_ratings.any_not_cooperating(parsed),
        ))
    if len(out) < MIN_PLAUSIBLE_ROWS:
        raise ValueError(f"only {len(out)} rows; expected at least "
                         f"{MIN_PLAUSIBLE_ROWS}")
    return out


def seniority_audit(records):
    """The two seniority tests, scored against each other.

    THE EARLIER VERSION OF THIS FUNCTION ASKED A DEAD QUESTION. It looked for
    corporate rows with NO redemption date, on the reasoning that a perpetual
    has no maturity. Not one row in the file has an empty date -- the
    perpetuals carry the sentinel 31-12-9999 -- so that check returned zero
    every time and looked like a clean bill of health. It has been removed
    rather than kept as a decoration.

    WHAT REPLACES IT IS A REAL CROSS-TAB, because there are now two
    independent tests and neither is complete:

        both              the easy cases
        sentinel_only     perpetuals with no seniority word anywhere. 67 of
                          them, including SBI, REC, Bank of India and Power
                          Finance -- unreachable from any marker table.
        words_only        rows whose issuer filed a call date instead of the
                          sentinel, PLUS genuinely dated subordination (Tata
                          Capital Tier II, DCB Tier 2, IIFL Tier 2, SMFG
                          sub-debt) which no maturity test can ever reach.

    Read `words_only`. It is small, and it is the list where a false positive
    would hide: a perpetual appearing there is a filing quirk, but a senior
    bond appearing there is a marker firing on something it should not.

    `far_dated` collects every redemption year beyond 2100 that is NOT the
    adopted sentinel. Those rows are unexplained and are reported rather than
    absorbed -- 2903 and 3034 look far more like typos for 2033 and 2034 than
    like a convention.
    """
    by_tier, markers, types = {}, {}, {}
    both, sentinel_only, words_only, far_dated = [], [], [], []
    for r in records:
        tier = r.get("tier")
        by_tier[tier] = by_tier.get(tier, 0) + 1
        it = r.get("instrument_type") or "(blank)"
        d = types.setdefault(it, dict(count=0, subordinated=0))
        d["count"] += 1
        if tier == _instruments.SUBORDINATED:
            d["subordinated"] += 1

        marker = _instruments.subordinated_text(
            f"{r.get('instrument_type') or ''} {r.get('description') or ''}")
        red = r.get("redemption")
        sentinel = _instruments.is_perpetual_maturity(red)
        if marker:
            markers[marker] = markers.get(marker, 0) + 1
        if sentinel:
            markers["(no maturity)"] = markers.get("(no maturity)", 0) + 1

        if marker and sentinel:
            both.append(r)
        elif sentinel:
            sentinel_only.append(r)
        elif marker:
            words_only.append(r)

        if red is not None and red.year > 2100 and not sentinel:
            far_dated.append(r)

    return dict(
        by_tier=dict(sorted(by_tier.items(), key=lambda kv: -kv[1])),
        markers=dict(sorted(markers.items(), key=lambda kv: -kv[1])),
        instrument_types=dict(sorted(types.items(),
                                     key=lambda kv: -kv[1]["count"])),
        both=both, sentinel_only=sentinel_only, words_only=words_only,
        far_dated=far_dated)


def format_seniority_audit(audit, sample=25):
    """One page to read before trusting the subordinated cap."""
    def rows(rs, n):
        return [f"  {r.get('isin')}  {(r.get('description') or '')[:70]}"
                for r in rs[:n]]

    b, s, w = audit["both"], audit["sentinel_only"], audit["words_only"]
    lines = ["seniority: the maturity sentinel and the description, together",
             "-" * 70, "tier"]
    for t, n in audit["by_tier"].items():
        lines.append(f"  {t:<22}{n:>8,}")
    lines += ["", "what identified it"]
    lines.append(f"  both tests               {len(b):>8,}")
    lines.append(f"  sentinel only            {len(s):>8,}   "
                 f"<- no seniority word anywhere")
    lines.append(f"  words only               {len(w):>8,}   "
                 f"<- call date filed, or dated subordination")
    lines.append(f"  union                    {len(b) + len(s) + len(w):>8,}")
    lines += ["", "which word fired"]
    for m, n in audit["markers"].items():
        lines.append(f"  {m:<22}{n:>8,}")
    lines += ["", "Type of Instrument (count / of which subordinated)"]
    for it, d in list(audit["instrument_types"].items())[:40]:
        lines.append(f"  {it[:44]:<46}{d['count']:>8,} {d['subordinated']:>8,}")
    lines += ["", f"SENTINEL ONLY -- {len(s):,} perpetuals no marker table "
                  f"could reach"] + rows(s, sample)
    lines += ["", f"WORDS ONLY -- {len(w):,}. Read these: a perpetual here is "
                  f"a filing quirk, a SENIOR bond here is a false positive"
              ] + rows(w, sample)
    if audit["far_dated"]:
        lines += ["", f"UNEXPLAINED FAR DATES -- {len(audit['far_dated']):,} "
                      f"rows beyond 2100 that are not the adopted sentinel"]
        lines += [f"  {r.get('isin')}  {r.get('redemption')}  "
                  f"{(r.get('description') or '')[:52]}"
                  for r in audit["far_dated"][:sample]]
    return "\n".join(lines)


def content_hash(records):
    """Hash over the PARSED ROWS, not the raw bytes.

    The workbook is regenerated per request -- three pulls minutes apart gave
    2,919,397, 2,919,514 and 2,919,709 bytes -- so byte hashing cannot tell a
    genuinely changed file from the same data serialised twice.
    """
    h = hashlib.sha256()
    for r in sorted(records, key=lambda x: x["isin"]):
        # CANONICAL PARSED PAIRS, NOT THE RAW STRING. The server emits the
        # agencies within a cell in nondeterministic order, so two pulls
        # minutes apart differed on 1,802 of 8,665 rated rows while the actual
        # ratings were identical. Hashing raw_rating meant every snapshot
        # looked changed and the idempotent re-ingest the schema was built for
        # would never once have fired.
        pairs = ";".join(sorted(
            f"{p['agency'] or 'unattributed'}={p['grade'] or ''}"
            for p in r["ratings"]))
        h.update(f"{r['isin']}|{pairs}|{r['status']}\n".encode())
    return h.hexdigest()


def summarise(records):
    tiers, sectors = {}, {}
    rated = sum(1 for r in records if r["worst_grade"])
    for r in records:
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
        sectors[r["sector"][:40]] = sectors.get(r["sector"][:40], 0) + 1
    return dict(
        rows=len(records), rated=rated,
        rated_pct=rated / len(records) * 100 if records else 0.0,
        issuers=len({r["issuer_key"] for r in records}),
        enhanced=sum(1 for r in records if r["enhanced"]),
        not_cooperating=sum(1 for r in records if r["not_cooperating"]),
        tiers=tiers,
        sectors=dict(sorted(sectors.items(), key=lambda kv: -kv[1])[:8]))
