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
