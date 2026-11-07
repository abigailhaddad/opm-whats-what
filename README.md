# opm-whats-what

A weekly digest of what OPM publishes, going forward from 2026-11-01. It surfaces what long rules and memos bury, especially what a document's own summary leaves out.

**Unofficial; not affiliated with OPM. The summaries and findings are written by an AI model (Claude Opus 5.5). Every quote is machine-checked against the source document and page, but the model's judgments (what a document "leaves out", who is speaking) are not. Read the source before relying on anything here.**

## Sources

| Source | Collected from |
|---|---|
| Federal Register (OPM documents) | `federalregister.gov/api/v1/documents.json`, agency `personnel-management-office`, 30-day lookback |
| CHCO Council memos | `opm.gov/chcoc/published-memos/` |
| OPM news | `opm.gov/news/` (news releases, Director's Desk, Secrets of OPM) |
| Reports & publications | `opm.gov/about-us/reports-publications/` and its sub-pages |

## How it works

Runs every Monday at 11:00 UTC (`.github/workflows/weekly.yml`).

1. **Collect** (`src/collect.py`): record every listed item in `data/items.json`. The 2026-11-01 run is the baseline and is never reported; "new" means first seen since.
2. **Triage** (`src/triage.py`): rules, proposed rules, CHCO memos, news and new report documents are read in full; routine Federal Register paperwork notices get one line.
3. **Read** (`src/weekly.py`): one Claude Opus 5.5 batch, capped at $3 per run. Each finding is tagged by whether the document's own summary covers it, and each news release or memo is paired with the rule it describes and cross-checked.
4. **Verify** (`src/verify.py`): each quote is checked against the document text and the cited page; failures are marked ⚠️.
5. **Audit** (`src/audit.py`): re-fetches every source, extracts text a different way, re-confirms each quote and page, and looks up every number in a finding in the source. Writes `digests/proof/<date>.md` and appends a one-line result to the digest. If any quote isn't confirmed, the run is marked failed (the digest still goes out).
6. **Publish**: the digest is written to `digests/<date>.md` and posted as a GitHub issue. A week with nothing new posts nothing.

Not built: diffing amended rules against the current CFR on eCFR. `explore/` has the earlier feasibility tests.

## Running

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/collect.py
ANTHROPIC_API_KEY=... .venv/bin/python src/weekly.py --dry-run   # token count and cost; submits nothing
.venv/bin/python src/audit.py digests/<date>.json
.venv/bin/pip install pytest && .venv/bin/python -m pytest tests
```

## License

MIT (see `LICENSE`). Quoted source material comes from U.S. government publications.
