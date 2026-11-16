import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from audit import loose


def test_section_sign_matches_sec():
    fr_text = "complies with the requirements of Sec.  875.110(d), which requires OPM"
    fr_pdf = "complies with the requirements of § 875.110(d), which requires OPM"
    assert loose(fr_text)[0] == loose(fr_pdf)[0]


def test_double_section_sign_matches_secs():
    assert loose("under Secs. 875.110 and 875.111")[0] == loose("under §§ 875.110 and 875.111")[0]


def test_indexes_point_into_original():
    s = "see § 1.2"
    text, idx = loose(s)
    assert text == "seesec12"
    assert [s[i] for i in idx] == list("see§§§12")
