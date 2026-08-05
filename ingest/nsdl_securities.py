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
        tier = _instruments.classify(None, isin)["tier"]
        out.append(dict(
            isin=isin, issuer=issuer, issuer_key=canonical_issuer(issuer),
            tier=tier,
            description=_norm(r[idx.get("Security Description", -1)])
            if "Security Description" in idx else "",
            instrument_type=_norm(r[idx["Type of Instrument"]])
            if "Type of Instrument" in idx else "",
            sector=_norm(r[idx["Business Sector"]])
            if "Business Sector" in idx else "",
            ownership=_norm(r[idx["Type of Issuer-Ownership"]])
            if "Type of Issuer-Ownership" in idx else "",
            status=_norm(r[idx["Instrument Status"]])
            if "Instrument Status" in idx else "",
            allotment=_date(r[idx["Date of Allotment"]])
            if "Date of Allotment" in idx else None,
            redemption=_date(r[idx.get("Date of Redemption/Conversion", -1)])
            if "Date of Redemption/Conversion" in idx else None,
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
