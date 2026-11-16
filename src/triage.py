"""Decide which new items get read in full and which get one line.

read      rules, proposed rules, non-routine FR notices, CHCO memos, news
          releases, Director's Desk and Secrets of OPM posts, new report documents
one_line  routine FR notices (information-collection paperwork, Privacy Act
          system notices, performance review boards, pay/actuarial tables,
          advisory-committee meetings) and new reports-section web pages

Rules are deliberately simple and keyed on fields the sources give us. Every
item still appears in the digest's "everything new" list; triage only decides
whether it gets read and written up.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlparse

ROUTINE_FR_NOTICE = re.compile(
    r"information collection|submission for (review|renewal|re-?instatement|reinstatement)"
    r"|privacy act of 1974|performance review board|pay schedules?|present value factors"
    r"|normal cost percentages|board of actuaries|advisory committee|closed meeting"
    r"|public meeting",
    re.I,
)


HEALTH_TRI_AGENCIES = {"treasury-department", "labor-department", "health-and-human-services-department",
                       "internal-revenue-service", "employee-benefits-security-administration",
                       "centers-for-medicare-medicaid-services"}


YEAR = re.compile(r"(?<!\d)(19[9]\d|20\d\d)(?!\d)")


def not_new_reason(item: dict) -> str | None:
    """Why an item we first saw recently is still not a new publication, or None if it may be new.

    The rule (also in the README):
      1. A reports document linked only from archive pages (a page whose path contains
         "archive", e.g. /about-us/reports-publications/agency-archive/). OPM adding old
         files to its archive is not OPM publishing something.
      2. A dated item (FR, CHCO, news) whose own date is more than 365 days before we
         first saw it.
      3. An undated item (reports) whose title and file name name no year later than
         two calendar years before the year we first saw it (first seen in 2026: the
         newest year named is 2024 or earlier). An item that names no year is kept.
    These items are recorded in data/items.json and listed at the bottom of the digest,
    but they are not sent to the model and not counted as new.
    """
    first = (item.get("first_seen") or "")[:10]
    if not first:
        return None
    pages = item.get("found_on_pages") or ([item["found_on"]] if item.get("found_on") else [])
    if item["source"] == "reports" and pages and all("archive" in urlparse(p).path.lower() for p in pages):
        return "linked only from an archive page"
    if item.get("date"):
        if date.fromisoformat(item["date"]) < date.fromisoformat(first) - timedelta(days=365):
            return f"dated {item['date']}, more than a year before it was first seen"
        return None
    years = [int(y) for y in YEAR.findall(f"{item.get('title') or ''} {urlparse(item['url']).path.rsplit('/', 1)[-1]}")]
    if years and max(years) <= int(first[:4]) - 2:
        return f"names no year after {max(years)}"
    return None


def triage(item: dict) -> tuple[str, str]:
    """Return (decision, reason)."""
    src, kind, title = item["source"], item.get("kind") or "", item.get("title") or ""
    if src == "fr":
        # Joint health-coverage rules (No Surprises Act, ACA) are led by the
        # Treasury/Labor/HHS tri-agencies; OPM signs on because of FEHB. The API
        # lists OPM first on every OPM document, so agency order can't identify
        # the lead; key on the co-signers instead. Joint rules with MSPB or OMB
        # are OPM-driven and still read.
        if set(item.get("agencies") or []) & HEALTH_TRI_AGENCIES:
            return "one_line", "joint health-coverage rule (Treasury/Labor/HHS)"
        if kind in ("Rule", "Proposed Rule"):
            return "read", kind
        # New collections (OMB number "3206-NEW") and new data-matching programs are
        # where new programs first surface (e.g. 2026-09-17 "Annual Employee Survey
        # (3206-NEW)"); renewals and re-established programs stay one-liners.
        if kind == "Notice" and (re.search(r"3206-NEW|\bNEW\)", title) or re.search(r"\bNew Matching Program", title)):
            return "read", "new information collection or matching program"
        if kind == "Notice" and ROUTINE_FR_NOTICE.search(title):
            return "one_line", "routine FR notice"
        return "read", f"FR {kind or 'document'} (not a routine notice type)"
    if src == "chco":
        return "read", "CHCO memo"
    if src == "news":
        return "read", item.get("kind", "news")
    if src == "reports":
        return ("read", "new report document") if item.get("kind") == "document" else ("one_line", "new reports page")
    return "read", "unknown source (read to be safe)"


if __name__ == "__main__":
    # Show how the last 12 months of FR documents would have been triaged.
    import collections
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "data/fr_opm_12mo.json"
    docs = json.load(open(path))["results"]
    c = collections.Counter()
    for d in docs:
        dec, why = triage({"source": "fr", "kind": d["type"], "title": d["title"],
                           "agencies": [a.get("slug") for a in d.get("agencies") or []]})
        c[(dec, why)] += 1
    for k, v in sorted(c.items(), key=lambda kv: -kv[1]):
        print(v, k)
    reads = [d for d in docs if triage({"source": "fr", "kind": d["type"], "title": d["title"],
                                        "agencies": [a.get("slug") for a in d.get("agencies") or []]})[0] == "read" and d["type"] == "Notice"]
    print("\nNotices that would be READ:")
    for d in reads:
        print("  ", d["publication_date"], d["title"][:100])
