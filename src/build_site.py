"""Build a static website and RSS feed from the digest markdown files.

Every digest this repo writes to digests/YYYY-MM-DD.md becomes a page; the
site also has an index page (newest first) and feed.xml (RSS 2.0, full text of
the newest --feed-items digests). The same file is used in each digest repo;
what differs (title, URLs) comes in as arguments from .github/workflows/site.yml.

Run locally:
    python src/build_site.py --out _site --title "..." --description "..." \
        --site-url https://REPO.abigailhaddad.com/ --repo-url https://github.com/abigailhaddad/REPO

Which files become pages:
  * only digests/<YYYY-MM-DD>.md. Anything else (test-*.md, proof/, data/,
    samples/, .json) is not published; the script prints what it skipped.
  * a digest whose first line is "NOTHING NEW" is skipped, the same rule the
    weekly workflow uses for not opening an issue.

The markdown is rendered with markdown-it-py (CommonMark plus tables, with
newlines as line breaks, so <details> blocks, tables and line breaks render
the way they do in the GitHub issue; bare emails/URLs are not auto-linked), then
sanitized with nh3, because digests quote titles and text from outside sources
and GitHub sanitizes issue bodies but a plain static page would not.

A digest's date is its first commit time in git (when it was published), or
noon UTC on the filename date if git has no record of it (e.g. not yet
committed). The checkout needs full history (fetch-depth: 0) for this.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import subprocess
import sys
from email.utils import format_datetime
from pathlib import Path

import nh3
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
DIGEST_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

# breaks=True: a single newline is a line break, as in GitHub issue bodies, so a page reads like the issue
MD = MarkdownIt("commonmark", {"html": True, "breaks": True}).enable(["table", "strikethrough"])

CSS = """
:root { --fg:#1b1b1b; --muted:#5c5c5c; --bg:#fff; --line:#ddd; --link:#0b57d0; }
@media (prefers-color-scheme: dark) { :root { --fg:#e8e8e8; --muted:#a8a8a8; --bg:#141414; --line:#333; --link:#8ab4f8; } }
body { font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--fg); background: var(--bg);
       max-width: 52rem; margin: 0 auto; padding: 1.5rem 16px 3rem; }
a { color: var(--link); }
nav, footer, .meta { color: var(--muted); font-size: .9rem; }
nav { margin-bottom: 1.5rem; } footer { margin-top: 3rem; border-top: 1px solid var(--line); padding-top: 1rem; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; font-size: .9rem; }
th, td { border: 1px solid var(--line); padding: .3rem .5rem; vertical-align: top; }
blockquote { margin-left: 0; padding-left: 1rem; border-left: 3px solid var(--line); color: var(--muted); }
details { margin: 1rem 0; } summary { cursor: pointer; font-weight: 600; }
ul.digests { list-style: none; padding: 0; } ul.digests li { margin: .4rem 0; }
"""


def render(markdown_text: str) -> str:
    raw = MD.render(markdown_text)
    clean = nh3.clean(
        raw,
        attributes={**nh3.ALLOWED_ATTRIBUTES, "th": {"style"}, "td": {"style"}},
        filter_style_properties={"text-align"},   # markdown-it writes table alignment as style
    )
    return clean.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")


def first_heading(markdown_text: str) -> str | None:
    for line in markdown_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def published_at(path: Path, day: str) -> dt.datetime:
    """First commit that added the file; falls back to noon UTC on the filename date."""
    try:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--follow", "--format=%cI", "--", path.name],
            cwd=path.resolve().parent, capture_output=True, text=True, check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        out = []
    if out:
        return dt.datetime.fromisoformat(out[-1]).astimezone(dt.timezone.utc)   # oldest add is last
    return dt.datetime.fromisoformat(day).replace(hour=12, tzinfo=dt.timezone.utc)


def collect(digests: Path) -> tuple[list[dict], list[str]]:
    """Digests to publish, newest first, and a list of 'path: reason' for everything skipped."""
    items, skipped = [], []
    for p in sorted(digests.rglob("*.md")):
        rel = p.relative_to(digests).as_posix()
        m = DIGEST_NAME.match(p.name)
        if p.parent != digests or not m:
            skipped.append(f"{rel}: not a digests/YYYY-MM-DD.md file")
            continue
        text = p.read_text(encoding="utf-8")
        if text.lstrip().startswith("NOTHING NEW"):
            skipped.append(f"{rel}: NOTHING NEW (no issue is opened for these either)")
            continue
        day = m.group(1)
        items.append({
            "day": day,
            "file": rel,
            "title": first_heading(text) or day,
            "html": render(text),
            "published": published_at(p, day),
        })
    items.sort(key=lambda d: d["day"], reverse=True)
    return items, skipped


def page(title: str, body: str, a: argparse.Namespace) -> str:
    e = html.escape
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="alternate" type="application/rss+xml" title="{e(a.title)}" href="feed.xml">
<style>{CSS}</style>
</head>
<body>
<nav><a href="index.html">{e(a.title)}</a> · <a href="feed.xml">RSS</a></nav>
{body}
<footer>{e(a.description)} Source and data: <a href="{e(a.repo_url)}">{e(a.repo_url.removeprefix("https://"))}</a>.
Subscribe: <a href="feed.xml">RSS feed</a>.</footer>
</body>
</html>
"""


def digest_page(d: dict, prev: dict | None, nxt: dict | None, a: argparse.Namespace) -> str:
    e = html.escape
    links = []
    if nxt:
        links.append(f'Newer: <a href="{nxt["day"]}.html">{e(nxt["title"])}</a>')
    if prev:
        links.append(f'Older: <a href="{prev["day"]}.html">{e(prev["title"])}</a>')
    src = f'{a.repo_url}/blob/main/digests/{d["file"]}'
    meta = (f'<p class="meta">Published {d["published"]:%Y-%m-%d}. '
            f'<a href="{e(src)}">Markdown source on GitHub</a>.</p>')
    nav = f'<p class="meta">{" · ".join(links)}</p>' if links else ""
    return page(d["title"], f"<article>\n{d['html']}\n</article>\n{meta}\n{nav}", a)


def index_page(items: list[dict], a: argparse.Namespace) -> str:
    e = html.escape
    rows = "\n".join(f'<li><a href="{d["day"]}.html">{e(d["title"])}</a></li>' for d in items)
    body = (f"<h1>{e(a.title)}</h1>\n<p>{e(a.description)}</p>\n"
            f'<p class="meta">{len(items)} digest{"s" if len(items) != 1 else ""}. '
            f'<a href="feed.xml">RSS feed</a>.</p>\n<ul class="digests">\n{rows}\n</ul>')
    return page(a.title, body, a)


def feed_xml(items: list[dict], a: argparse.Namespace) -> str:
    x = lambda s: html.escape(s, quote=False)   # noqa: E731  XML text escaping
    base = a.site_url.rstrip("/") + "/"
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{x(a.title)}</title>",
        f"<link>{x(base)}</link>",
        f"<description>{x(a.description)}</description>",
        "<language>en-us</language>",
        f'<atom:link href="{html.escape(base + "feed.xml")}" rel="self" type="application/rss+xml"/>',
    ]
    if items:   # newest digest's time, not "now", so an unchanged site builds byte-identical
        out.append(f"<lastBuildDate>{format_datetime(max(d['published'] for d in items), usegmt=True)}</lastBuildDate>")
    for d in items[: a.feed_items]:
        url = f"{base}{d['day']}.html"
        out += [
            "<item>",
            f"<title>{x(d['title'])}</title>",
            f"<link>{x(url)}</link>",
            f'<guid isPermaLink="true">{x(url)}</guid>',
            f"<pubDate>{format_datetime(d['published'], usegmt=True)}</pubDate>",
            f"<description>{x(d['html'])}</description>",
            "</item>",
        ]
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def build(a: argparse.Namespace) -> list[dict]:
    items, skipped = collect(Path(a.digests))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, d in enumerate(items):
        nxt = items[i - 1] if i > 0 else None
        prev = items[i + 1] if i + 1 < len(items) else None
        (out / f"{d['day']}.html").write_text(digest_page(d, prev, nxt, a), encoding="utf-8")
    (out / "index.html").write_text(index_page(items, a), encoding="utf-8")
    (out / "feed.xml").write_text(feed_xml(items, a), encoding="utf-8")
    print(f"Published {len(items)} digest(s) to {out}/; feed has {min(len(items), a.feed_items)}.")
    for s in skipped:
        print(f"  skipped {s}")
    return items


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--digests", default=str(ROOT / "digests"))
    p.add_argument("--out", default=str(ROOT / "_site"))
    p.add_argument("--title", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--site-url", required=True, help="public URL of the site, e.g. https://REPO.abigailhaddad.com/")
    p.add_argument("--repo-url", required=True, help="e.g. https://github.com/abigailhaddad/REPO")
    p.add_argument("--feed-items", type=int, default=20)
    return p.parse_args(argv)


if __name__ == "__main__":
    build(parse_args())
    sys.exit(0)
