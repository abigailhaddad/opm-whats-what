# opm-whats-what

This is my attempt to keep up with the documents OPM puts out that I otherwise wouldn't hear about. Every Monday I pull from the Federal Register, CHCO Council memos, OPM's news page, and its reports and publications, and have Claude write summaries and findings (Sonnet 5.5 reads each document; Opus 5.5 groups the findings into stories). A script then checks that each quote is in the original text, on the page cited. It doesn't check that the findings are right, so read the source. Unofficial; not affiliated with OPM.

Each digest is also published at https://opm-whats-what.abigailhaddad.com/, with an RSS feed at https://opm-whats-what.abigailhaddad.com/feed.xml (built by `src/build_site.py`, deployed to Cloudflare Pages by `.github/workflows/site.yml`).

## Sources

| Source | Collected from |
|---|---|
| Federal Register (OPM documents) | `federalregister.gov/api/v1/documents.json`, agency `personnel-management-office`, 30-day lookback |
| CHCO Council memos | `opm.gov/chcoc/published-memos/` |
| OPM news | `opm.gov/news/` (news releases, Director's Desk, Secrets of OPM) |
| Reports & publications | `opm.gov/about-us/reports-publications/` and its sub-pages |

## How it works

A GitHub Action runs it Monday mornings (11:00 UTC).

1. `collect.py` lists what's on those sources and stores it in `data/items.json`. Everything that existed on 2026-11-01 is the baseline and never gets reported.
2. `triage.py` sorts the new items. Items that are new to us but not newly published are set aside, recorded and listed at the bottom of the digest, but not read: a reports document linked only from an archive page (e.g. `agency-archive/`), a dated item whose date is more than a year before we first saw it, or an undated item whose title and file name name no year later than two years before the year we first saw it. Rules, proposed rules, memos, news and report documents get read in full. Routine Federal Register paperwork notices get a line.
3. `weekly.py` sends the full texts to Claude Sonnet 5.5 in one batch, capped at $3 a run, then has Claude Opus 5.5 group the findings into stories. Each finding is tagged by whether the document's own summary covers it. News releases and memos are paired with the rule they describe and checked against it.
4. `verify.py` checks each quote against the text and page. Then `audit.py` re-fetches the sources, extracts the text a second way, and rechecks every quote, page and number, with results in `digests/proof/`. If a quote can't be confirmed the run is marked failed, but the digest still goes out.
5. The digest goes in `digests/` and gets posted as a GitHub issue. Nothing new, no issue.

Not built: comparing amended rules against the current CFR. `explore/` has early tests.

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
