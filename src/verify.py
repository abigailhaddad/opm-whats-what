"""Check each finding's quote against the document text and the cited page.

Normalizes only whitespace, line breaks, hyphens at line breaks, curly vs
straight quotes, and dash variants. Page = the last [[Page N]] marker before
the quote's position (a quote ending on the next page also counts).
Same logic as explore/verify_quotes.py, which was tested on a real quote, a
wrong page and a fabricated quote.
"""
from __future__ import annotations

import difflib
import re


def norm(s: str) -> str:
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.replace("``", '"').replace("''", '"')
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"-\s*\n\s*", "-", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"-\s+", "-", s)   # "3.8- percent" (model copied a line-break gap) == "3.8-percent"
    return s.strip()


class PagedText:
    def __init__(self, raw: str):
        pieces, pages, page = [], [], None
        for chunk in re.split(r"(\[\[Page \d+\]\])", raw):
            m = re.fullmatch(r"\[\[Page (\d+)\]\]", chunk)
            if m:
                page = int(m.group(1))
                continue
            n = norm(chunk)
            if n:
                pieces.append(n)
                pages.append(page)
        self.starts, off = [], 0
        for p in pieces:
            self.starts.append(off)
            off += len(p) + 1
        # Text before the first [[Page N]] marker is on page N-1 (FR raw text puts
        # the marker where a new page begins; the document starts on the page before).
        first = next((p for p in pages if p is not None), None)
        self.pages = [p if p is not None else (first - 1 if first else None) for p in pages]
        self.full = " ".join(pieces)

    def page_of(self, pos: int):
        i = max(k for k, s in enumerate(self.starts) if s <= pos)
        return self.pages[i]


def check(raw_text: str, items: list[dict]) -> list[dict]:
    """Return one result per item: status in {ok, wrong_page, not_found} and detail."""
    doc = PagedText(raw_text)
    out = []
    for it in items:
        q = norm(it.get("quote") or "")
        if not q:
            out.append({"status": "not_found", "detail": "empty quote"})
            continue
        pos = doc.full.find(q)
        if pos >= 0:
            first = None
            while pos >= 0:   # the same passage can appear on two pages: right if ANY occurrence is on the cited page
                a, b = doc.page_of(pos), doc.page_of(pos + len(q) - 1)
                first = first or a
                if it.get("page") in (a, b):
                    out.append({"status": "ok"})
                    break
                pos = doc.full.find(q, pos + 1)
            else:
                out.append({"status": "wrong_page", "detail": f"cited {it.get('page')}, found on {first}"})
            continue
        best, step = 0.0, max(20, len(q) // 4)
        for s in range(0, max(1, len(doc.full) - len(q)), step):
            r = difflib.SequenceMatcher(None, q, doc.full[s:s + len(q)]).quick_ratio()
            if r > best:
                best, pos_best = r, s
        if best:
            best = difflib.SequenceMatcher(None, q, doc.full[pos_best:pos_best + len(q)]).ratio()
        out.append({"status": "not_found", "detail": f"closest passage similarity {best:.2f}"})
    return out
