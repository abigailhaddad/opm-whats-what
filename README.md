# opm-whats-what

A weekly "what's what" for everything OPM publishes, going forward from 2026-11-01. It surfaces the substance buried in long rules and memos, especially what the document's own summary leaves out. Unofficial; not affiliated with OPM.

## Sources

| Source | Collected from |
|---|---|
| Federal Register (OPM documents) | `federalregister.gov/api/v1/documents.json`, agency `personnel-management-office`, 30-day lookback each run |
| CHCO Council memos | `opm.gov/chcoc/published-memos/` |
| OPM news | `opm.gov/news/`: News Releases, From the Director's Desk, Secrets of OPM |
| Reports & publications | `opm.gov/about-us/reports-publications/` and every sub-page under it (crawled) |

## How it works

1. `src/collect.py` records every listed item in `data/items.json`, keyed by URL, with `first_seen`. The first run for each source (2026-11-01) is the **baseline**: those 2,742 items are marked `baseline: true` and never reported. After that, "new" means first seen since the last run, which also works for sources that don't date their items.
2. `src/triage.py` decides what gets read in full (rules, proposed rules, new information collections and matching programs, CHCO memos, news and blog posts, new report documents) and what gets one line (routine Federal Register paperwork notices, new web pages). Backtest on Nov 2025–Oct 2026 Federal Register documents: 71 read, 48 one line.
3. *(next)* Reading: each document is read in full. Findings are ranked by whether the document's own summary covers them (says the opposite > absent > vague > headline), and each one is checked automatically: verbatim quote on the cited page, the right speaker (OPM vs. a commenter vs. the current rule), and for rules, changed sections diffed against the current CFR on eCFR.
4. *(next)* Output: a weekly web page plus a GitHub issue, which reaches you by email.

`explore/` has the 2026-11-01 feasibility test on the RIF final rule and the excepted-service proposed rule (24 of 24 quotes verbatim on the cited page; see `explore/README.md`).

## Running

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/collect.py
.venv/bin/python src/triage.py      # backtest on data/fr_opm_12mo.json
```
