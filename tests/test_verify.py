import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from verify import check

DOC = "[[Page 1]]\nOPM may give a 3.8-\npercent increase to agricultural\ninspectors.\n[[Page 2]]\nSecond page text here."

def test_hyphen_linebreak_with_space_in_quote():
    r = check(DOC, [{"quote": "a 3.8- percent increase to agricultural inspectors", "page": 1}])
    assert r[0]["status"] == "ok"

def test_wrong_page_and_fabricated():
    r = check(DOC, [{"quote": "Second page text here", "page": 1}, {"quote": "never said this anywhere in it", "page": 1}])
    assert r[0]["status"] == "wrong_page" and r[1]["status"] == "not_found"
