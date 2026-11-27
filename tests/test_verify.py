import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from verify import check

DOC = "[[Page 1]]\nOPM may give a 3.8-\npercent increase to agricultural\ninspectors.\n[[Page 2]]\nSecond page text here."

def test_hyphen_linebreak_with_space_in_quote():
    r = check(DOC, [{"quote": "a 3.8- percent increase to agricultural inspectors", "page": 1}])
    assert r[0]["status"] == "ok"

def test_wrong_page_and_fabricated():
    items = [{"quote": "Second page text here", "page": 1}, {"quote": "never said this anywhere in it", "page": 1}]
    r = check(DOC, items)
    # a verbatim quote on another page is corrected to that page; a fabricated one still fails
    assert r[0]["status"] == "ok" and "corrected" in r[0]["detail"] and items[0]["page"] == 2
    assert r[1]["status"] == "not_found"


def test_quote_with_copied_page_marker_matches():
    # 2026-11-23: the model copied "[[Page 3]]" and the printed page number into a quote that runs across pages.
    raw = ("[[Page 2]]\nClassification specialists should lead, working with industrial-organizational\n"
           "psychologists, HR advisors, and program subject-matter experts. The tooling\n[[Page 3]]\n3 \n \n"
           "supports triage and prioritization; it does not set series, title, or grade.")
    quote = ("Classification specialists should lead, working with industrial-organizational \npsychologists, HR "
             "advisors, and program subject-matter experts. The tooling\n[[Page 3]]\n3 \n \nsupports triage and "
             "prioritization; it does not set series, title, or grade.")
    assert check(raw, [{"quote": quote, "page": 2}])[0]["status"] == "ok"


def test_marker_removal_does_not_rescue_a_wrong_quote():
    raw = "[[Page 2]]\nThe tooling\n[[Page 3]]\n3\nsupports triage."
    assert check(raw, [{"quote": "The tooling [[Page 3]] 3 sets the grade.", "page": 2}])[0]["status"] == "not_found"


def test_verbatim_quote_on_another_page_gets_its_page_corrected():
    raw = "[[Page 75168]]\nSomething else entirely here.\n[[Page 75169]]\nOPM declines to withdraw the rule."
    it = {"quote": "OPM declines to withdraw the rule.", "page": 75168}
    r = check(raw, [it])[0]
    assert r["status"] == "ok" and "corrected" in r["detail"] and it["page"] == 75169


def test_audit_joins_pages_without_a_pdf_footer_page_number():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    import audit
    s = audit.Source.__new__(audit.Source)
    s.pages = {2: "experts. The tooling \n\n2 \n\n \n \n\x0c", 3: "supports triage and prioritization; it does not set series."}
    s.loose = {p: audit.loose(t) for p, t in s.pages.items()}
    s.loose_join = {p: audit.loose(audit.strip_page_number(t))[0] for p, t in s.pages.items()}
    q = "experts. The tooling\n[[Page 3]]\n3 \n \nsupports triage and prioritization; it does not set series."
    assert s.find(q, 2)[0] == "ok"
    assert s.find("experts. The tooling [[Page 3]] 3 sets the grade of every position here.", 2)[0] == "not_found"
