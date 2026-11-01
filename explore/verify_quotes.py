"""Check every quote in a findings JSON against the Federal Register raw text.

For each item: is the quote in the document (after normalizing only whitespace,
line wraps, hyphenation across line breaks, curly vs straight quotes, and dash
variants), and is it on the page the item cites? Page = the last [[Page N]]
marker before the quote's position.

Usage: python verify_quotes.py <raw_text.txt> <findings.json>
"""
import difflib
import json
import re
import sys


def norm(s: str) -> str:
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"-\s*\n\s*", "-", s)          # hyphen at a line break: keep the hyphen, drop the break
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def main(raw_path: str, findings_path: str) -> int:
    raw = open(raw_path, encoding="utf-8").read()
    # Build the normalized text plus a map from normalized offset -> FR page.
    pieces, page_at = [], []
    page = None
    for chunk in re.split(r"(\[\[Page \d+\]\])", raw):
        m = re.fullmatch(r"\[\[Page (\d+)\]\]", chunk)
        if m:
            page = int(m.group(1))
            continue
        n = norm(chunk)
        if n:
            pieces.append(n)
            page_at.append(page)
    # joined with single spaces; record each piece's start offset
    starts, text, off = [], [], 0
    for p in pieces:
        starts.append(off)
        text.append(p)
        off += len(p) + 1
    full = " ".join(text)

    def page_of(pos: int):
        i = max(k for k, s in enumerate(starts) if s <= pos)
        return page_at[i]

    items = json.load(open(findings_path))["items"]
    n_ok = n_wrong_page = n_missing = 0
    for i, it in enumerate(items, 1):
        q = norm(it["quote"])
        pos = full.find(q)
        cited = int(it.get("page")) if str(it.get("page", "")).isdigit() else None
        if pos >= 0:
            actual = page_of(pos)
            end_page = page_of(pos + len(q) - 1)
            on_page = cited in (actual, end_page)
            status = "OK" if on_page else f"WRONG PAGE (cited {cited}, found on {actual})"
            n_ok += on_page
            n_wrong_page += not on_page
        else:
            # nearest window, to tell a typo from a fabrication
            best, best_r = None, 0.0
            step = max(20, len(q) // 4)
            for s in range(0, max(1, len(full) - len(q)), step):
                r = difflib.SequenceMatcher(None, q, full[s:s + len(q)]).quick_ratio()
                if r > best_r:
                    best_r, best = r, s
            if best is not None:
                r = difflib.SequenceMatcher(None, q, full[best:best + len(q)]).ratio()
                best_r = r
            status = f"NOT FOUND verbatim (closest passage similarity {best_r:.2f})"
            n_missing += 1
        print(f"{i:2}. [{status}] {it['headline'][:90]}")
    print(f"\n{len(items)} quotes: {n_ok} verbatim on cited page, {n_wrong_page} verbatim but wrong page, {n_missing} not found verbatim")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
