# Exploration, 2026-11-01: can the buried substance of long OPM rules be surfaced reliably?

## Sources (all four are readable)

| Source | How | Volume |
|---|---|---|
| Federal Register, OPM | API `federalregister.gov/api/v1/documents.json?conditions[agencies][]=personnel-management-office` (raw text has `[[Page N]]` markers) | 119 docs in the 12 months to 2026-11-01: 59 notices (mostly information-collection paperwork), 37 rules, 23 proposed rules; ~995 pages; 11 docs ≥20 pp |
| CHCO Council memos | `opm.gov/chcoc/published-memos/` (chcoc.gov/transmittals redirects here): one page, ~2,360 memo links, dated | 46 dated 2026 |
| OPM news | `opm.gov/news/`: News Releases, From the Director's Desk, "Secrets of OPM" (director's blog) | not counted yet |
| Reports & publications | `opm.gov/about-us/reports-publications/`: undated hub of sub-pages; new items must be found by diffing the crawled tree | not counted yet |

## Test

Two long rules, each read end to end by a separate agent with no other context, asked for the 6-12 things an analyst would want that the abstract doesn't say, each with a verbatim quote and FR page:

- `2026-15665` Reduction in Force, final rule, 50 pp → `2026-15665.findings.json`
- `2026-19222` Employment in the Excepted Service, proposed rule, 23 pp, comments close 2026-11-17 → `2026-19222.findings.json`

`verify_quotes.py` checks each quote against the raw text (normalizing only whitespace, line breaks, quote and dash characters) and against the cited page. Self-tested on a real quote, a wrong page and a fabricated quote.

## Results

- **Quotes: 24 of 24 verbatim, on the cited page.**
- **Numbers spot-checked against the text, all correct:** RIF effective date (notices on/after 2026-09-02), 721 comments, +5/+3 veterans' points, 180-day abolishment, 26-point award example, $3.45M and $1.24M cost estimates, $154.76/hr labor rate.
- **One attribution error:** RIF item 2 says "OPM says outright" a 5/5/5 non-veteran outranks a 4/4/5 veteran with 40 years. The example is Commenter 0680's; OPM's response concedes the outcome ("may rank ahead … a feature"). Substance right, speaker wrong. A pipeline needs a check on *who says it* (OPM vs. a commenter vs. the current rule), not just whether the quote exists.
- **"New vs. carried over" needs the current CFR.** The excepted-service agent, unprompted, compared two sections to eCFR and found the reemployment exemption drops the current "same agency and commuting area" and priority-list conditions (§302.101(c)(9) → proposed (c)(12)), which the preamble calls removing a "redundant requirement". Confirmed independently against eCFR's current text. It also correctly flagged that the 2-year priority limit is *not* new. Diffing amended sections against eCFR looks like the strongest buried-change detector available.

## Implications for the build

1. Triage first: most FR notices are form renewals and get one line; rules, proposed rules and CHCO memos get read.
2. Long-doc extraction is good enough to build on, with three automatic checks: quote verbatim + page, speaker attribution, and (for rules) amended-section diff against eCFR.
3. Volume is small (a few substantive documents a week), so reading every rule in full is affordable.
