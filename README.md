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
2. `src/triage.py` decides what gets read in full (rules, proposed rules, new information collections and matching programs, CHCO memos, news and blog posts, new report documents) and what gets one line (routine Federal Register paperwork notices, joint Treasury/Labor/HHS health-coverage rules, new web pages). Backtest on the 119 Federal Register documents from the 12 months to 2026-11-01: 70 read, 49 one line.
3. `src/weekly.py` fetches the full text of each document to be read and sends them to Claude Opus 5.5 in one Message Batch (half price). The run stops short of submitting if the estimated cost exceeds the cap (default $3); the digest then lists everything as unread. Each finding is tagged by whether the document's own summary covers it (says the opposite > absent > vague > in the summary) and by who is speaking (OPM, a commenter, the current rule). Each news release or CHCO memo is paired with the Federal Register rule it likely describes, and the model checks the two for discrepancies.
4. Every finding's quote is checked against the document text and the cited page (`src/verify.py`); a quote that fails is marked ⚠️ in the digest. The speaker tag is the model's own and is not independently checked.
5. The digest is written to `digests/<date>.md`, and the GitHub Actions workflow (`.github/workflows/weekly.yml`, Mondays 11:00 UTC) commits it and opens an issue so it arrives by email. A week with nothing new opens no issue.

Not built: diffing amended rule sections against the current CFR on eCFR. The feasibility test found it promising (see `explore/README.md`).

`explore/` has the 2026-11-01 feasibility test on the RIF final rule and the excepted-service proposed rule (24 of 24 quotes verbatim on the cited page; see `explore/README.md`).

## Running

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/collect.py
.venv/bin/python src/triage.py      # backtest on data/fr_opm_12mo.json
ANTHROPIC_API_KEY=... .venv/bin/python src/weekly.py --dry-run   # count tokens and cost; submits nothing
```
