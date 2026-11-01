"""Collect everything OPM publishes from four sources into data/items.json.

One record per item, keyed by URL. `first_seen` is set the first time a run sees
an item and never changed, so "new this week" = first_seen within the week,
even for sources that don't date their items (the Secrets of OPM blog index,
the reports-page tree).

Going forward only: the first run for a source records everything currently
listed with `baseline: true`. Baseline items exist only so later runs know
they aren't new; they are never digested or shown.

Sources:
  fr        Federal Register documents with OPM as an agency (API)
  chco      CHCO Council memos (opm.gov/chcoc/published-memos/)
  news      OPM News Releases, From the Director's Desk, Secrets of OPM
  reports   opm.gov/about-us/reports-publications/ and its sub-pages (crawled)

Usage:
  python src/collect.py                 # all sources
  python src/collect.py --only fr chco  # some sources
  python src/collect.py --since 2026-10-01   # FR lookback (default: 30 days)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "items.json"
UA = {"User-Agent": "opm-whats-what/0.1 (weekly OPM publications tracker; github.com/abigailhaddad)"}
OPM = "https://www.opm.gov"
DATE_RE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})")


def get(url: str, **kw) -> requests.Response:
    for attempt in range(4):
        r = requests.get(url, headers=UA, timeout=90, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(0.7)  # polite
        return r
    r.raise_for_status()
    return r


def parse_date(text: str) -> str | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    return datetime.strptime(" ".join(m.groups()), "%B %d %Y").date().isoformat()


# --------------------------------------------------------------------------- #
# Federal Register
# --------------------------------------------------------------------------- #
FR_FIELDS = ["document_number", "type", "title", "publication_date", "comments_close_on",
             "effective_on", "page_length", "html_url", "raw_text_url", "pdf_url",
             "abstract", "cfr_references", "regulation_id_numbers", "significant", "docket_ids"]


def collect_fr(since: str) -> list[dict]:
    out, page = [], 1
    while True:
        params = [("conditions[agencies][]", "personnel-management-office"),
                  ("conditions[publication_date][gte]", since),
                  ("per_page", "200"), ("page", str(page)), ("order", "newest")]
        params += [("fields[]", f) for f in FR_FIELDS]
        j = get("https://www.federalregister.gov/api/v1/documents.json", params=params).json()
        for d in j.get("results", []):
            out.append({
                "source": "fr", "url": d["html_url"], "title": d["title"],
                "date": d["publication_date"], "kind": d["type"],
                "doc_number": d["document_number"], "pages": d.get("page_length"),
                "comments_close": d.get("comments_close_on"), "effective": d.get("effective_on"),
                "abstract": d.get("abstract"), "raw_text_url": d.get("raw_text_url"),
                "pdf_url": d.get("pdf_url"), "cfr_references": d.get("cfr_references"),
                "rin": d.get("regulation_id_numbers"), "significant": d.get("significant"),
                "docket_ids": d.get("docket_ids"),
            })
        if not j.get("next_page_url"):
            break
        page += 1
    if len(out) != j.get("count", len(out)):
        raise SystemExit(f"FR: got {len(out)} documents but API count is {j.get('count')}")
    return out


# --------------------------------------------------------------------------- #
# CHCO memos
# --------------------------------------------------------------------------- #
def collect_chco() -> list[dict]:
    url = f"{OPM}/chcoc/published-memos/"
    soup = BeautifulSoup(get(url).text, "html.parser")
    out, seen = [], set()
    # recent memos live under /chcoc/latest-memos/, older ones under /chcoc/transmittals/<year>/
    for a in soup.select('a[href*="/chcoc/latest-memos/"], a[href*="/chcoc/transmittals/"]'):
        href = urljoin(url, a["href"])
        title = a.get_text(" ", strip=True)
        if not title or href in seen:
            continue
        seen.add(href)
        # the memo's date is in the surrounding list item / row
        ctx = a.find_parent(["li", "tr", "article", "div"])
        out.append({"source": "chco", "url": href, "title": title, "kind": "CHCO memo",
                    "date": parse_date(ctx.get_text(" ", strip=True) if ctx else "")})
    if len(out) < 500:  # the page lists ~2,360 memos; far fewer means the layout changed
        raise SystemExit(f"CHCO: only {len(out)} memos parsed from {url}; page layout may have changed")
    return out


# --------------------------------------------------------------------------- #
# OPM news
# --------------------------------------------------------------------------- #
NEWS_STREAMS = {"news-releases": "News release", "from-the-director-s-desk": "Director's Desk",
                "secrets-of-opm": "Secrets of OPM"}


def collect_news() -> list[dict]:
    out = []
    for slug, kind in NEWS_STREAMS.items():
        url = f"{OPM}/news/{slug}/"
        soup = BeautifulSoup(get(url).text, "html.parser")
        main = soup.find("main") or soup
        seen = {}
        for a in main.select(f'a[href*="/news/{slug}/"]'):
            href = urljoin(url, a["href"]).split("#")[0]
            if href.rstrip("/") == url.rstrip("/"):
                continue
            title = a.get_text(" ", strip=True)
            ctx = a.find_parent(["li", "article", "div"])
            d = parse_date(ctx.get_text(" ", strip=True)) if ctx else None
            # keep the longest title seen for a URL (index pages repeat links with
            # short anchor text like "read more" or a phrase inside a post blurb)
            if href not in seen or len(title) > len(seen[href]["title"]):
                seen[href] = {"source": "news", "url": href, "title": title, "kind": kind, "date": d}
        if not seen:
            raise SystemExit(f"news: no items parsed from {url}")
        out += list(seen.values())
    return out


# --------------------------------------------------------------------------- #
# Reports & publications tree
# --------------------------------------------------------------------------- #
REPORTS_ROOT = f"{OPM}/about-us/reports-publications/"
DOC_EXT = re.compile(r"\.(pdf|xlsx?|csv|docx?|zip)(\?|$)", re.I)


def collect_reports(max_pages: int = 400) -> list[dict]:
    """Crawl every page under /about-us/reports-publications/ and record each
    sub-page and each document linked from one. Undated: novelty = first_seen."""
    queue, visited, out = [REPORTS_ROOT], set(), {}
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            soup = BeautifulSoup(get(url).text, "html.parser")
        except requests.HTTPError as e:
            out[url] = {"source": "reports", "url": url, "title": "", "kind": "page",
                        "error": str(e)[:120]}
            continue
        title = (soup.find("h1") or soup.find("title"))
        if url != REPORTS_ROOT:
            out[url] = {"source": "reports", "url": url, "kind": "page",
                        "title": title.get_text(" ", strip=True) if title else url}
        main = soup.find("main") or soup
        for a in main.find_all("a", href=True):
            href = urljoin(url, a["href"]).split("#")[0]
            p = urlparse(href)
            if DOC_EXT.search(p.path) and p.netloc.endswith("opm.gov"):
                out.setdefault(href, {"source": "reports", "url": href, "kind": "document",
                                      "title": a.get_text(" ", strip=True) or p.path.rsplit("/", 1)[-1],
                                      "found_on": url})
            elif (p.netloc == "www.opm.gov" and p.path.startswith("/about-us/reports-publications/")
                  and href not in visited):
                queue.append(href if href.endswith("/") else href + "/")
    if len(visited) >= max_pages:
        print(f"reports: stopped at max_pages={max_pages}; {len(queue)} pages not crawled", file=sys.stderr)
    return list(out.values())


# --------------------------------------------------------------------------- #
COLLECTORS = {"fr": None, "chco": collect_chco, "news": collect_news, "reports": collect_reports}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", choices=list(COLLECTORS))
    ap.add_argument("--since", default=(date.today() - timedelta(days=30)).isoformat(),
                    help="Federal Register lookback start date")
    args = ap.parse_args()

    store = json.loads(STORE.read_text()) if STORE.exists() else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for name in args.only or COLLECTORS:
        items = collect_fr(args.since) if name == "fr" else COLLECTORS[name]()
        first_run = not any(v["source"] == name for v in store.values())
        new = 0
        for it in items:
            prev = store.get(it["url"])
            it["first_seen"] = prev["first_seen"] if prev else now
            it["last_seen"] = now
            if prev is None:
                it["baseline"] = first_run
                new += not first_run
            store[it["url"]] = {**(prev or {}), **it}
        print(f"{name}: {len(items)} listed, " + ("recorded as baseline (first run)" if first_run else f"{new} new"))
    STORE.parent.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True))
    print(f"store: {len(store)} items -> {STORE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
