import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import build_site  # noqa: E402

SITE = "https://example.github.io/repo/"


def args(digests, out, **kw):
    return build_site.parse_args([
        "--digests", str(digests), "--out", str(out), "--title", "Test digest",
        "--description", "A test.", "--site-url", SITE, "--repo-url", "https://github.com/x/repo",
        *sum(([f"--{k.replace('_', '-')}", str(v)] for k, v in kw.items()), []),
    ])


def make_digests(tmp_path):
    d = tmp_path / "digests"
    (d / "proof").mkdir(parents=True)
    (d / "2026-11-02.md").write_text(
        "# Week of Nov 2\n\nIntro & <script>alert(1)</script> text.\n\n"
        "| Docket | Posted |\n|---|---:|\n| [A-1](https://www.regulations.gov/docket/A-1) | 1,234 |\n\n"
        "<details><summary>All findings</summary>\n\n- **bold** item\n\n</details>\n\n"
        '[bad](javascript:alert(1)) <a href="https://x.test" onclick="evil()">x</a>\n',
        encoding="utf-8")
    (d / "2026-10-26.md").write_text("# Week of Oct 26\n\nOlder.\n", encoding="utf-8")
    (d / "2026-11-09.md").write_text("NOTHING NEW\n\n# Nothing\n", encoding="utf-8")
    (d / "test-2026-10-19.md").write_text("# test run\n", encoding="utf-8")
    (d / "proof" / "2026-11-02.md").write_text("# proof\n", encoding="utf-8")
    (d / "2026-11-02.json").write_text("{}", encoding="utf-8")
    return d


def test_pages_feed_and_exclusions(tmp_path, capsys):
    out = tmp_path / "_site"
    items = build_site.build(args(make_digests(tmp_path), out))

    assert [i["day"] for i in items] == ["2026-11-02", "2026-10-26"]
    assert sorted(p.name for p in out.iterdir()) == [
        "2026-10-26.html", "2026-11-02.html", "feed.xml", "index.html"]
    printed = capsys.readouterr().out
    for skipped in ("2026-11-09.md: NOTHING NEW", "test-2026-10-19.md", "proof/2026-11-02.md"):
        assert skipped in printed

    page = (out / "2026-11-02.html").read_text(encoding="utf-8")
    assert "<script>" not in page and "onclick" not in page
    assert 'href="javascript' not in page          # the link is left as plain text, not a link
    assert "<details><summary>All findings</summary>" in page
    assert "<strong>bold</strong>" in page          # markdown inside <details> is rendered, as on GitHub
    assert '<td style="text-align:right">1,234</td>' in page
    assert 'Older: <a href="2026-10-26.html">' in page

    root = ET.parse(out / "feed.xml").getroot()
    assert root.tag == "rss" and root.get("version") == "2.0"
    got = [(i.findtext("title"), i.findtext("link"), i.findtext("guid")) for i in root.iter("item")]
    assert got == [
        ("Week of Nov 2", SITE + "2026-11-02.html", SITE + "2026-11-02.html"),
        ("Week of Oct 26", SITE + "2026-10-26.html", SITE + "2026-10-26.html"),
    ]
    first = next(root.iter("item")).findtext("description")
    assert "<table>" in first and "<script>" not in first
    # not committed to git, so dates fall back to noon UTC on the filename date
    assert next(root.iter("item")).findtext("pubDate") == "Mon, 02 Nov 2026 12:00:00 GMT"


def test_feed_items_limit_and_empty(tmp_path):
    d = make_digests(tmp_path)
    build_site.build(args(d, tmp_path / "a", feed_items=1))
    assert len(list(ET.parse(tmp_path / "a" / "feed.xml").getroot().iter("item"))) == 1
    # index still lists every digest
    assert "2026-10-26.html" in (tmp_path / "a" / "index.html").read_text(encoding="utf-8")

    empty = tmp_path / "empty"
    empty.mkdir()
    build_site.build(args(empty, tmp_path / "b"))
    assert list(ET.parse(tmp_path / "b" / "feed.xml").getroot().iter("item")) == []
