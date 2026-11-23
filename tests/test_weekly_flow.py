"""Flow tests for weekly.py and collect.py with a stubbed model client and stubbed fetching: nothing here
touches the network or a paid API."""
import datetime
import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import collect  # noqa: E402
import verify  # noqa: E402
import weekly  # noqa: E402

TODAY = datetime.date.today().isoformat()


def memo(letter):
    return {"source": "chco", "url": f"https://x/chcoc/latest-memos/{letter}", "title": f"Memo {letter}",
            "kind": "CHCO memo", "date": TODAY, "first_seen": TODAY + "T00:00:00+00:00"}


ROUTINE = {"source": "fr", "url": "https://fr/routine", "title": "Agency Information Collection Request: Form 123",
           "kind": "Notice", "date": TODAY, "first_seen": TODAY + "T00:00:00+00:00", "agencies": []}


def setup(monkeypatch, tmp_path, items, fetch=None, tokens=50_000_000):
    (tmp_path / "items.json").write_text(json.dumps({i["url"]: i for i in items}))
    monkeypatch.setattr(weekly, "STORE", tmp_path / "items.json")
    monkeypatch.setattr(weekly, "DIGESTS", tmp_path / "digests")
    monkeypatch.setattr(weekly, "ROOT", tmp_path)
    monkeypatch.setattr(weekly, "make_client", lambda: object())
    monkeypatch.setattr(weekly, "document_text", fetch or (lambda it: "[[Page 1]]\n" + "word " * 500))
    monkeypatch.setattr(weekly, "count_input_tokens", lambda c, q: tokens)
    monkeypatch.setattr(sys, "argv", ["weekly", "--cap", "3"])


def digested(tmp_path):
    return {v["title"]: bool(v.get("digested_at")) for v in json.loads((tmp_path / "items.json").read_text()).values()}


def test_items_that_were_not_read_stay_new_so_the_next_run_picks_them_up(monkeypatch, tmp_path):
    def fetch(it):
        if it["title"] == "Memo B":
            raise RuntimeError("HTTP 503")
        return "[[Page 1]]\n" + "word " * 500
    setup(monkeypatch, tmp_path, [memo("A"), memo("B"), ROUTINE], fetch=fetch)    # A hits the cost cap, B can't be fetched
    weekly.main()
    assert digested(tmp_path) == {"Memo A": False, "Memo B": False, ROUTINE["title"]: True}
    text = (tmp_path / "digests" / f"{TODAY}.md").read_text()
    assert "Memo A" in text and "Memo B" in text            # both are named as not read, with why


def test_a_same_day_rerun_with_nothing_new_does_not_overwrite_the_real_digest(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, [ROUTINE])
    weekly.main()
    out = tmp_path / "digests" / f"{TODAY}.md"
    real = out.read_text()
    assert not real.startswith("NOTHING NEW")
    weekly.main()                                           # everything is digested now
    assert out.read_text() == real


def test_verify_handles_an_empty_quote_and_a_quote_cited_on_its_second_page():
    assert verify.check("[[Page 1]]\n" + "word " * 60, [{"quote": "", "page": 1}])[0]["status"] == "not_found"
    doc = ("[[Page 1]]\nThe rule takes effect on January 1, 2027 for all employees.\n"
           "[[Page 2]]\nThe rule takes effect on January 1, 2027 for all employees.")
    q = "The rule takes effect on January 1, 2027 for all employees."
    assert verify.check(doc, [{"quote": q, "page": 2}])[0]["status"] == "ok"        # it IS on page 2
    assert verify.check(doc, [{"quote": q, "page": 1}])[0]["status"] == "ok"
    assert verify.check(doc, [{"quote": q, "page": 3}])[0]["status"] == "wrong_page"


def test_a_quote_that_crosses_a_page_marker_renders_as_one_markdown_block():
    # A model sometimes copies through a [[Page N]] marker despite being told not to
    # (verify.py then flags it as not_found). Rendered as-is, the quote's embedded blank
    # line used to end the markdown blockquote early and spill the rest of the quote, plus
    # the ⚠️ warning, out as a loose paragraph after the bullet.
    f = {"headline": "H", "what_changes": "W", "summary_status": "absent", "speaker": "OPM",
         "quote": "The tooling\n[[Page 3]]\n3 \n \nsupports triage.", "page": 3, "section": "x"}
    v = {"status": "not_found", "detail": "d"}
    md = weekly._finding_md(f, v, with_quote=True)
    assert "\n\n" not in md
    assert "[[Page" not in md
    assert "⚠️ quote not found" in md


def test_a_press_release_is_paired_with_a_rule_published_a_day_after_it():
    rule = {"source": "fr", "kind": "Rule", "title": "Elimination of Time-in-Grade Requirements", "date": "2026-10-21",
            "url": "https://fr/rule"}
    release = {"source": "news", "title": "OPM Finalizes Rule Removing Governmentwide Time-in-Grade Bar", "date": "2026-10-20",
               "url": "https://x/news"}
    assert [(c["url"], r["url"]) for c, r in weekly.pair_companions([release], {rule["url"]: rule})] == [("https://x/news", "https://fr/rule")]
    far = {**rule, "date": "2026-10-30", "url": "https://fr/far"}
    assert weekly.pair_companions([release], {far["url"]: far}) == []                # nine days after: too far


def test_collect_survives_one_failing_source_and_records_it(monkeypatch, tmp_path):
    def ok():
        return [{"source": "news", "url": "https://x/n1", "title": "N1", "kind": "News Release", "date": TODAY}]

    def boom():
        raise requests.ConnectionError("connection reset")
    monkeypatch.setattr(collect, "COLLECTORS", {"news": ok, "chco": boom})
    monkeypatch.setattr(collect, "STORE", tmp_path / "data" / "items.json")
    monkeypatch.setattr(collect, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["collect.py"])
    assert collect.main() == 0
    assert "https://x/n1" in json.loads((tmp_path / "data" / "items.json").read_text())
    errors = json.loads((tmp_path / "data" / "collect_errors.json").read_text())
    assert [e["source"] for e in errors] == ["chco"] and "connection reset" in errors[0]["error"]


def test_get_retries_timeouts_and_connection_errors(monkeypatch):
    calls = []

    class Ok:
        status_code, text = 200, "ok"

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) < 3:
            raise requests.Timeout("slow")
        return Ok()
    monkeypatch.setattr(collect.requests, "get", fake_get)
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    assert collect.get("https://x/").text == "ok" and len(calls) == 3
    monkeypatch.setattr(collect.requests, "get", lambda url, **kw: (_ for _ in ()).throw(requests.Timeout("down")))
    with pytest.raises(requests.Timeout):
        collect.get("https://x/")


# --------------------------------------------------------------------------- #
# Archived / old documents are recorded but not treated as new
# --------------------------------------------------------------------------- #
from triage import not_new_reason  # noqa: E402

ARCHIVE = "https://www.opm.gov/about-us/reports-publications/agency-archive/"
CURRENT = "https://www.opm.gov/about-us/reports-publications/agency-reports/"
SEEN = "2026-11-16T20:46:00+00:00"


def report(name, title, pages):
    return {"source": "reports", "kind": "document", "url": f"https://www.opm.gov/telework/{name}.pdf",
            "title": title, "found_on": pages[0], "found_on_pages": pages, "first_seen": SEEN}


def test_not_new_rule_on_the_telework_reports_seen_2026_11_16():
    # 2004-2025 reports newly linked from agency-archive: not new
    assert not_new_reason(report("2004-report-to-congress", "Status of Telework in the Federal Government, 2004", [ARCHIVE]))
    assert not_new_reason(report("s-2025", "Status of Telework in the Federal Government, 2025", [ARCHIVE]))
    # the FY2025 report (dated November 2026) linked from agency-reports: new, gets read
    assert not_new_reason(report("s-2026-pdf-file", "Status of Telework in the Federal Government, 2026", [CURRENT])) is None
    # linked from an archive page AND a current page: may be new
    assert not_new_reason(report("x", "Some report", [ARCHIVE, CURRENT])) is None
    # records from before found_on_pages existed fall back to found_on
    old_style = report("2005-report-to-congress", "Status of Telework, 2005", [ARCHIVE])
    del old_style["found_on_pages"]
    assert not_new_reason(old_style)


def test_not_new_rule_by_year_and_by_date():
    r = report("x", "Annual report 2024", [CURRENT])
    assert not_new_reason(r) == "names no year after 2024"                   # two years before 2026
    assert not_new_reason({**r, "title": "Annual report 2025"}) is None      # one year before: may be new
    assert not_new_reason({**r, "title": "Annual report", "url": "https://www.opm.gov/a/b.pdf"}) is None   # no year
    assert not_new_reason({**r, "title": "Data 2019-2025"}) is None           # newest year named counts
    m = {**memo("Z"), "first_seen": SEEN}
    assert not_new_reason({**m, "date": "2026-11-10"}) is None
    assert not_new_reason({**m, "date": "2025-11-16"}) is None               # exactly 365 days: kept
    assert "more than a year" in not_new_reason({**m, "date": "2025-11-15"})
    assert not_new_reason({**m, "date": "2026-11-10", "title": "2004 memo"}) is None   # a date beats a year in the title


def test_archived_reports_are_not_sent_to_the_model_and_are_listed_once(monkeypatch, tmp_path):
    archived = report("2004-report-to-congress", "Status of Telework in the Federal Government, 2004", [ARCHIVE])
    sent = []
    setup(monkeypatch, tmp_path, [memo("A"), archived])
    monkeypatch.setattr(weekly, "count_input_tokens", lambda c, q: sent.append(q) or 50_000_000)
    weekly.main()
    assert len(sent) == 1 and "Memo A" in sent[0]["messages"][0]["content"]          # only the memo was costed
    assert sent[0]["model"] == "claude-sonnet-5-5"                                   # reads run on Sonnet
    store = json.loads((tmp_path / "items.json").read_text())
    assert store[archived["url"]]["not_new"] == "linked only from an archive page"
    assert store[archived["url"]]["digested_at"]                                     # listed once, never again
    text = (tmp_path / "digests" / f"{TODAY}.md").read_text()
    assert "Not treated as new (1)" in text and "2004" in text
    assert "Not read (cost cap exceeded): [Status of Telework" not in text
    assert "0 new documents" in text or "new document" in text


def test_digest_date_from_the_environment_names_the_file(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, [ROUTINE])
    monkeypatch.setenv("DIGEST_DATE", "2026-11-16")
    weekly.main()
    assert (tmp_path / "digests" / "2026-11-16.md").exists()
    monkeypatch.setenv("DIGEST_DATE", "11/16/2026")
    with pytest.raises(ValueError):
        weekly.main()


def test_grouping_runs_on_opus(monkeypatch, tmp_path):
    seen = {}

    def fake_synthesize(client, reads, results, crosses, model, effort):
        seen["model"], seen["effort"] = model, effort
        return {"stories": [], "routine": [], "problems": [], "cost_usd": 0.0}
    monkeypatch.setattr(weekly, "synthesize", fake_synthesize)
    monkeypatch.setattr(weekly, "make_client", lambda: object())
    raw = {"results": {"https://x/a": {"findings": {"plain_summary": "s", "items": [], "could_not_determine": []},
                                       "verification": []}}}
    weekly.synthesis(raw, [memo("a") | {"url": "https://x/a"}], [])
    assert seen == {"model": "claude-opus-5-5", "effort": "medium"}


def test_collect_reports_records_every_page_that_links_a_document(monkeypatch):
    root = collect.REPORTS_ROOT
    pages = {
        root: f'<main><h1>R</h1><a href="{CURRENT}">cur</a><a href="{ARCHIVE}">arc</a></main>',
        CURRENT: '<main><h1>Cur</h1><a href="/telework/new.pdf">New</a><a href="/telework/both.pdf">Both</a></main>',
        ARCHIVE: '<main><h1>Arc</h1><a href="/telework/old.pdf">Old</a><a href="/telework/both.pdf">Both</a></main>',
    }

    class R:
        def __init__(self, t):
            self.text = t
    monkeypatch.setattr(collect, "get", lambda url, **kw: R(pages[url]))
    docs = {d["url"].rsplit("/", 1)[-1]: d for d in collect.collect_reports() if d["kind"] == "document"}
    assert docs["old.pdf"]["found_on_pages"] == [ARCHIVE]
    assert docs["both.pdf"]["found_on_pages"] == [CURRENT, ARCHIVE]
    assert not_new_reason({**docs["old.pdf"], "first_seen": SEEN})
    assert not_new_reason({**docs["both.pdf"], "first_seen": SEEN}) is None
    assert not_new_reason({**docs["new.pdf"], "first_seen": SEEN}) is None
