"""Independent audit of a digest: re-check every quote, page and number against the source.

Why a second pass: verify.py runs inside the pipeline on the text the pipeline fetched.
This re-fetches each source and extracts text a different way, so a bug in fetching or
extraction can't hide itself:

  Federal Register   the document's PDF, page by page (pdfminer), FR page = start_page + index
                     (the pipeline reads the raw-text endpoint)
  PDFs (CHCO etc.)   pdfminer (the pipeline uses pypdf)
  HTML (news)        all visible text on the page (the pipeline reads only <main>)

Quotes are matched on letters and digits only (case, spacing, hyphens, quote marks and line
breaks ignored), so this pass is looser than verify.py on formatting and cannot raise a false
alarm over whitespace. Every number in a finding's headline or summary is looked up in the
source; numbers not found are listed for a human (they may be derived, e.g. 1.038 x 1.0277).

Writes digests/proof/<date>.md: per finding, our quote and page next to the source's own
text and a link. Usage: python src/audit.py digests/2026-11-06.json [--append-to digests/2026-11-06.md]
Exit 1 if any quote disagrees, so the workflow step is visibly red.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "opm-whats-what/0.1 (weekly OPM publications tracker; github.com/abigailhaddad)"}
NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def get(url: str) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    time.sleep(0.7)
    return r


def pdf_pages(content: bytes) -> list[str]:
    from pdfminer.high_level import extract_text
    import pdfminer.pdfpage as pp
    n = sum(1 for _ in pp.PDFPage.get_pages(io.BytesIO(content)))
    return [extract_text(io.BytesIO(content), page_numbers=[i]) for i in range(n)]


def source_pages(item: dict) -> tuple[dict[int, str], str]:
    """{page number: text}, and a label for how it was extracted."""
    if item["source"] == "fr":
        num = re.search(r"/documents/\d{4}/\d{2}/\d{2}/([^/]+)/", item["url"]).group(1)
        meta = get(f"https://www.federalregister.gov/api/v1/documents/{num}.json?fields[]=pdf_url&fields[]=start_page").json()
        pages = pdf_pages(get(meta["pdf_url"]).content)
        return {meta["start_page"] + i: t for i, t in enumerate(pages)}, "Federal Register PDF, pdfminer"
    r = get(item["url"])
    if item["url"].lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
        return {i + 1: t for i, t in enumerate(pdf_pages(r.content))}, "PDF, pdfminer"
    return {1: BeautifulSoup(r.text, "html.parser").get_text("\n")}, "HTML, whole page text"


def loose(s: str) -> tuple[str, list[int]]:
    """Letters and digits only, lowercased, plus each kept character's index in s."""
    keep = [(c.lower(), i) for i, c in enumerate(s) if c.isalnum()]
    return "".join(c for c, _ in keep), [i for _, i in keep]


class Source:
    def __init__(self, item: dict):
        self.item = item
        self.pages, self.how = source_pages(item)
        self.loose = {p: loose(t) for p, t in self.pages.items()}

    def find(self, quote: str, page: int | None):
        """-> (status, found_page, excerpt). status: ok | wrong_page | not_found | too_short"""
        q, _ = loose(quote)
        if len(q) < 30:
            return "too_short", None, ""
        hits = [(p, lt.find(q)) for p, (lt, _) in self.loose.items()]
        hits = [(p, i) for p, i in hits if i >= 0]
        if not hits:
            # a quote that runs across a page break: join each page to the next
            nums = sorted(self.loose)
            for a, b in zip(nums, nums[1:]):
                joined = self.loose[a][0] + self.loose[b][0]
                i = joined.find(q)
                if i >= 0 and i < len(self.loose[a][0]):
                    hits = [(a, i)]
                    break
        if not hits:
            return "not_found", None, ""
        p, i = next(((p, i) for p, i in hits if p == page), hits[0])
        text, idx = self.pages[p], self.loose[p][1]
        a = max(0, idx[i] - 150)
        b = min(len(text), idx[min(i + len(q), len(idx)) - 1] + 150)
        excerpt = re.sub(r"\s+", " ", text[a:b]).strip()
        return ("ok" if p == page or page in (p, p + 1) else "wrong_page"), p, excerpt

    def has_number(self, tok: str) -> bool:
        digits = tok.strip("$%").replace(",", "")
        pat = re.compile(r"(?<![\d.])" + re.escape(digits) + r"(?!\d)")   # whole number: 3.8 must not match 13.85
        return any(pat.search(t.replace(",", "")) for t in self.pages.values())


def audit(raw: dict, items_by_url: dict) -> tuple[str, dict]:
    L = ["# Audit proof", "",
         "Each finding's quote and page, as the model gave them, next to the text of the same page "
         "re-extracted from the source by a different method. Quotes are matched on letters and digits only. "
         "Numbers listed as 'not found in source' may be derived (e.g. a compounded percentage) and need a human look.", ""]
    stats = dict(quotes=0, ok=0, wrong_page=0, not_found=0, too_short=0, nums_unmatched=0, pipeline_disagree=0, errors=0)

    def row(src: Source, label: str, quote: str, page, pipeline: str | None):
        status, found, excerpt = src.find(quote, page)
        stats["quotes"] += 1
        stats[status] += 1
        if pipeline is not None and (pipeline == "ok") != (status == "ok"):
            stats["pipeline_disagree"] += 1
        mark = {"ok": "✅", "too_short": "➖"}.get(status, "❌")
        L.extend([f"- {mark} **{label}** — cited p. {page}; audit: {status}" + (f" (found p. {found})" if found and found != page else "")
                  + (f"; pipeline said: {pipeline}" if pipeline else ""),
                  f"  - ours: “{quote.strip()}”",
                  f"  - source p. {found if found else '?'}: “{excerpt}”" if excerpt else "  - source: no match"])

    for url, res in raw["results"].items():
        item = items_by_url[url]
        L += ["", f"## [{item['title']}]({url})"]
        try:
            src = Source(item)
        except Exception as e:  # never silently skipped
            stats["errors"] += 1
            L.append(f"- ❌ could not re-fetch the source for audit: {type(e).__name__}: {e}")
            continue
        L.append(f"Source re-extracted as: {src.how}; {len(src.pages)} page(s).")
        for f, v in zip(res["findings"]["items"], res["verification"]):
            row(src, f["headline"][:80], f["quote"], f["page"], v["status"])
            missing = sorted({t for t in NUM.findall(f["headline"] + " " + f["what_changes"])
                              if len(t.strip("$%")) >= 2 and not src.has_number(t)})
            if missing:
                stats["nums_unmatched"] += len(missing)
                L.append(f"  - numbers not found in source: {', '.join(missing)}")
    for c_url, r_url, x in raw["crosses"]:
        for d, (vc, vr) in zip(x["findings"]["discrepancies"], x.get("verification", [])):
            for url, q, pg, v in ((c_url, d["companion_quote"], d["companion_page"], vc), (r_url, d["rule_quote"], d["rule_page"], vr)):
                L += ["", f"## Cross-check: {d['topic']} — {url}"]
                try:
                    row(Source(items_by_url[url]), d["topic"][:80], q, pg, v)
                except Exception as e:
                    stats["errors"] += 1
                    L.append(f"- ❌ could not re-fetch: {e}")
    return "\n".join(L) + "\n", stats


def summary_line(s: dict) -> str:
    bad = s["wrong_page"] + s["not_found"] + s["errors"]
    return (f"Independent audit: {s['ok']} of {s['quotes']} quotes confirmed on the cited page by a second extraction"
            f"{'' if not bad else f'; {bad} NOT confirmed (see proof file)'}"
            f"{'' if not s['too_short'] else f'; {s['too_short']} too short to check'}"
            f"{'' if not s['nums_unmatched'] else f'; {s['nums_unmatched']} numbers not found verbatim in the source (derived or unchecked)'}.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("digest_json")
    ap.add_argument("--append-to")
    a = ap.parse_args()
    path = Path(a.digest_json).resolve()
    raw = json.loads(path.read_text())
    items = json.loads((ROOT / "data" / "items.json").read_text())
    by_url = {v["url"]: v for v in items.values()}
    if not raw["results"] and not raw["crosses"]:
        print("nothing to audit")
        return 0
    text, stats = audit(raw, by_url)
    line = summary_line(stats)
    out = path.parent / "proof" / (path.stem + ".md")
    out.parent.mkdir(exist_ok=True)
    out.write_text(f"{line}\n\n" + text, encoding="utf-8")
    print(line, f"\nproof: {out.relative_to(ROOT)}", stats)
    if a.append_to:
        with open(Path(a.append_to).resolve(), "a", encoding="utf-8") as fh:
            fh.write(f"\n{line} Proof: [{out.name}](proof/{out.name})\n")
    return 1 if (stats["wrong_page"] or stats["not_found"] or stats["errors"]) else 0


if __name__ == "__main__":
    sys.exit(main())
