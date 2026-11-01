"""Get a document's full text with page markers, so quotes can be checked by page.

Federal Register: the raw-text endpoint already has `[[Page N]]` markers (FR
page numbers). PDFs (CHCO memos, reports): one `[[Page N]]` per PDF page.
HTML (news): a single `[[Page 1]]`.

Text is cached under data/raw/ (gitignored) so reruns and model comparisons
read the identical text.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "raw"
UA = {"User-Agent": "opm-whats-what/0.1 (weekly OPM publications tracker; github.com/abigailhaddad)"}


def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    time.sleep(0.7)
    return r


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    pages = PdfReader(io.BytesIO(content)).pages
    return "\n".join(f"[[Page {i}]]\n{(p.extract_text() or '').strip()}" for i, p in enumerate(pages, 1))


def document_text(item: dict) -> str:
    """Full text of an item, with [[Page N]] markers. Raises on failure (no silent skips)."""
    key = hashlib.sha1(item["url"].encode()).hexdigest()[:16]
    path = CACHE / f"{key}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    if item["source"] == "fr":
        text = _get(item["raw_text_url"]).text
        if text.lstrip().lower().startswith("<html"):   # raw text comes wrapped in <html><pre>
            soup = BeautifulSoup(text, "html.parser")
            text = (soup.find("pre") or soup).get_text()
    else:
        r = _get(item["url"])
        if item["url"].lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
            text = _pdf_text(r.content)
        else:
            soup = BeautifulSoup(r.text, "html.parser")
            main = soup.find("main") or soup
            for tag in main(["script", "style", "nav"]):
                tag.decompose()
            body = main.get_text("\n", strip=True)
            h1 = main.find("h1")
            if h1 and h1.get_text(strip=True) in body:   # drop breadcrumb / skip-link boilerplate
                body = body[body.find(h1.get_text(strip=True)):]
            text = "[[Page 1]]\n" + body
    text = text.replace("\x00", "")  # FR raw text contains NUL bytes
    if item["source"] == "fr" and "[[Page " not in text:
        # one-page FR documents come without a page marker; add the FR start page
        num = re.search(r"/documents/\d{4}/\d{2}/\d{2}/([^/]+)/", item["url"]).group(1)
        start = _get(f"https://www.federalregister.gov/api/v1/documents/{num}.json?fields[]=start_page").json()["start_page"]
        text = f"[[Page {start}]]\n" + text
    if len(text.split()) < 50:
        raise ValueError(f"suspiciously short text ({len(text.split())} words) for {item['url']}")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def own_summary(item: dict, text: str) -> str:
    """The document's own summary, which findings are ranked against.
    FR: the SUMMARY section. Others: title plus the opening ~120 words."""
    if item["source"] == "fr":
        flat = re.sub(r"\s+", " ", re.sub(r"\[\[Page \d+\]\]", "", text))
        m = re.search(r"SUMMARY:(.*?)(?:DATES:|ADDRESSES:|FOR FURTHER INFORMATION)", flat)
        if m:
            return m.group(1).strip()
        return item.get("abstract") or item["title"]
    body = re.sub(r"\s+", " ", re.sub(r"\[\[Page \d+\]\]", "", text))
    return f"{item['title']}. " + " ".join(body.split()[:120])
