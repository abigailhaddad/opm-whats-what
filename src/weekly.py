"""The weekly run: new items -> triage -> one Opus batch -> verify -> digest.

Steps
  1. New items = in data/items.json, not baseline, not yet digested.
     (--start/--end instead selects by publication date, for tests/backfill.)
  2. Triage: read in full vs one line.
  3. Fetch full text; pair each news release / CHCO memo with the Federal
     Register rule it describes (recent rule with the most title words in common).
  4. Count input tokens (free) and refuse to submit if the estimated batch cost
     exceeds --cap. The digest still goes out, listing everything as unread.
  5. One Message Batch (50% price): a read per document + a cross-check per pair.
  6. Verify every quote against its document and page.
  7. Write digests/<date>.md; mark items digested.

The GitHub issue (email) is created by the workflow from the digest file.
Usage: python src/weekly.py [--start 2026-10-19 --end 2026-10-25] [--cap 3] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming  # noqa: E402
from anthropic.types.messages.batch_create_params import Request  # noqa: E402

from fetch_text import document_text, own_summary  # noqa: E402
from synthesize import synthesize  # noqa: E402
from read import (PRICES, CrossCheck, build_crosscheck_request, build_request,  # noqa: E402
                  count_input_tokens, make_client, parse_message)
from triage import triage  # noqa: E402
from verify import check  # noqa: E402

STORE = ROOT / "data" / "items.json"
DIGESTS = ROOT / "digests"
MODEL, EFFORT = "claude-opus-5-5", "high"
EST_OUTPUT_PER_REQUEST = 12000
STATUS_RANK = {"opposite": 0, "absent": 1, "vague": 2, "in": 3}
STATUS_LABEL = {"opposite": "summary says the opposite", "absent": "not in the summary",
                "vague": "summary is vague", "in": "in the summary"}
STOP = set("the a an and of for to in on with by under from its is are opm rule final proposed office "
           "personnel management federal".split())


def anchor(title: str) -> str:
    """GitHub's heading anchor for '### [title](url)': lowercase, drop punctuation, spaces to hyphens."""
    return re.sub(r"[^a-z0-9 \-]", "", title.lower()).replace(" ", "-")


def words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z\-]{2,}", title.lower()) if w not in STOP}


def pick_items(store: dict, start: str | None, end: str | None) -> list[dict]:
    if start:
        return [v for v in store.values() if start <= (v.get("date") or "") <= end
                and v["source"] in ("fr", "chco", "news")]
    return [v for v in store.values() if not v.get("baseline") and not v.get("digested_at")]


def pair_companions(reads: list[dict], store: dict) -> list[tuple[dict, dict]]:
    """Pair each news release / CHCO memo with the FR rule it most likely describes:
    a Rule or Proposed Rule published in the 21 days before it, sharing >= 2
    significant title words, or whose title appears in the companion's title."""
    rules = [v for v in store.values() if v["source"] == "fr" and v.get("kind") in ("Rule", "Proposed Rule")]
    rules += [r for r in reads if r["source"] == "fr" and r.get("kind") in ("Rule", "Proposed Rule") and r not in rules]
    pairs = []
    for c in reads:
        if c["source"] not in ("news", "chco") or not c.get("date"):
            continue
        cd = date.fromisoformat(c["date"])
        best, best_score = None, 1
        for r in rules:
            if not r.get("date") or not (cd - timedelta(days=21) <= date.fromisoformat(r["date"]) <= cd):
                continue
            shared = words(c["title"]) & words(r["title"])
            # one shared word is enough if it's distinctive ("time-in-grade"); a wrong
            # pairing only costs one request, since the model checks describes_this_rule
            score = len(shared) + sum(1 for w in shared if "-" in w or len(w) >= 9)
            if score > best_score:
                best, best_score = r, score
        if best:
            pairs.append((c, best))
    return pairs


def poll(client, batch_id: str, max_hours: float = 5.0):
    deadline = time.time() + max_hours * 3600
    while True:
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            return b
        if time.time() > deadline:
            raise TimeoutError(f"batch {batch_id} not finished after {max_hours} h")
        print(f"  batch {b.processing_status}: {b.request_counts.processing} processing", flush=True)
        time.sleep(60)


def render(today: str, reads: list[dict], one_lines: list[dict], results: dict, crosses: list,
           failures: list[str], cost: float, store: dict, capped: str | None) -> str:
    L = [f"# OPM what's what — {today}", ""]
    n_find = sum(len(r["findings"]["items"]) for r in results.values())
    L.append(f"{len(reads)} documents read in full, {len(one_lines)} listed in one line. "
             f"{n_find} findings, ranked by what each document's own summary leaves out. "
             f"Every quote is checked against the document; a ⚠️ marks one that didn't check out.")
    if capped:
        L += ["", f"**Not read this week:** {capped}"]
    # This week in brief: per document (longest first), its first two findings that
    # the document's own summary leaves out or contradicts, in the model's order.
    brief = []
    for it in sorted(reads, key=lambda it: -(it.get("pages") or 0)):
        r = results.get(it["url"])
        if not r:
            continue
        picks = [(i, v) for i, v in zip(r["findings"]["items"], r["verification"])
                 if i["summary_status"] in ("opposite", "absent") and v["status"] == "ok"]
        picks = picks[:2] if it.get("kind") in ("Rule", "Proposed Rule") else picks[:1]
        brief += [f"- {i['headline']} — [{it['title']}](#{anchor(it['title'])})" for i, _ in picks]
    flagged_brief = [(c, x) for c, r_, x in crosses if x["findings"]["describes_this_rule"] and x["findings"]["discrepancies"]]
    for c, x in flagged_brief:
        for d in x["findings"]["discrepancies"]:
            brief.insert(0, f"- **{c.get('kind')} conflicts with the rule on {d['topic'].lower()}** — [{c['title']}]({c['url']})")
    if brief:
        L += ["", "## This week in brief", ""] + brief[:12]
    # Open comment periods
    soon = sorted([v for v in store.values() if v["source"] == "fr" and v.get("comments_close")
                   and today <= v["comments_close"] <= (date.fromisoformat(today) + timedelta(days=45)).isoformat()],
                  key=lambda v: v["comments_close"])
    if soon:
        L += ["", "## Comment periods closing soon", ""]
        L += [f"- **{v['comments_close']}** — [{v['title']}]({v['url']})" for v in soon]
    # Cross-checks first: they are the most likely to be news
    flagged = [(c, r, x) for c, r, x in crosses if x["findings"]["describes_this_rule"]
               and (x["findings"]["discrepancies"] or x["findings"]["left_out"])]
    if flagged:
        L += ["", "## Press releases and memos vs. the rules they describe", ""]
        for c, r, x in flagged:
            f = x["findings"]
            L.append(f"**[{c['title']}]({c['url']})** ({c.get('kind')}, {c.get('date')}) vs. "
                     f"[{r['title']}]({r['url']}) ({r.get('date')})")
            for d, v in zip(f["discrepancies"], x["verification"]):
                warn = "" if tuple(v) == ("ok", "ok") else " ⚠️ quote not verified"
                L.append(f"- **Conflict — {d['topic']}.** {c.get('kind', 'Companion')}: “{d['companion_quote']}” "
                         f"(p. {d['companion_page']}) · Rule: “{d['rule_quote']}” (p. {d['rule_page']}){warn}")
            if f["left_out"]:
                k = c.get("kind") or "companion"
                k = k if k.startswith("CHCO") else k.lower()
                L.append(f"- **What the rule does that the {k} doesn't mention:**")
                L += [f"  - {x_.rstrip('.')}" for x_ in f["left_out"]]
            L.append("")
    # Documents
    order = sorted(reads, key=lambda it: (it["source"] != "fr", it.get("kind") not in ("Rule", "Proposed Rule"),
                                          it.get("date") or ""))
    L += ["## Documents", ""]
    for it in order:
        r = results.get(it["url"])
        meta = [it.get("kind") or it["source"], it.get("date") or "first seen " + it["first_seen"][:10]]
        if it.get("pages"):
            meta.append(f"{it['pages']} pp")
        if it.get("comments_close"):
            meta.append(f"comments close {it['comments_close']}")
        L += [f"### [{it['title']}]({it['url']})", "*" + " · ".join(meta) + "*", ""]
        if not r:
            L += ["_Not read (see notes at the end)._", ""]
            continue
        f = r["findings"]
        L += [f["plain_summary"], ""]
        items = sorted(zip(f["items"], r["verification"]), key=lambda iv: STATUS_RANK[iv[0]["summary_status"]])
        for itm, v in items:
            warn = "" if v["status"] == "ok" else f" ⚠️ quote {v['status'].replace('_', ' ')}"
            who = "" if itm["speaker"] == "OPM" else f" (quoted passage: {itm['speaker'].replace('_', ' ')})"
            L.append(f"- **{itm['headline']}** — _{STATUS_LABEL[itm['summary_status']]}_{who}. {itm['what_changes']} "
                     f"> “{itm['quote']}” (p. {itm['page']}){warn}".replace(" > ", "  \n  > "))
        if f["could_not_determine"]:
            L.append("- _The document doesn't say:_ " + "; ".join(f["could_not_determine"]))
        L.append("")
    if one_lines:
        L += ["## Also new (not read)", ""]
        L += [f"- [{it['title']}]({it['url']}) — {it.get('kind') or it['source']}, "
              f"{it.get('date') or 'first seen ' + it['first_seen'][:10]} ({triage(it)[1]})" for it in one_lines]
        L.append("")
    L += ["---", f"Model: {MODEL} (batch, effort {EFFORT}). Cost this run: ${cost:.2f}."]
    if failures:
        L += ["", "**Problems this run:**"] + [f"- {x}" for x in failures]
    return "\n".join(L) + "\n"


def _flat(s: str) -> str:
    """Model text sometimes contains its own markdown list; keep each finding to one bullet."""
    s = re.sub(r"\s*\n\s*[-*]\s+", "; ", s)
    s = re.sub(r"[;,]\s*;", ";", re.sub(r"\s+", " ", s))
    return s.replace(":;", ":").strip()


def _finding_md(f: dict, v: dict, with_quote: bool) -> str:
    f = {**f, "what_changes": _flat(f["what_changes"]), "headline": _flat(f["headline"])}
    warn = "" if v["status"] == "ok" else f" ⚠️ quote {v['status'].replace('_', ' ')}"
    who = "" if f["speaker"] == "OPM" else f" (quoted passage: {f['speaker'].replace('_', ' ')})"
    # the summary-coverage label only appears in the collapsed full list, not in key findings
    label = f" _({STATUS_LABEL[f['summary_status']]})_" if with_quote else ""
    s = f"- **{f['headline']}**{label}{who}. {f['what_changes']}"
    if with_quote:
        s += f"  \n  > “{f['quote']}” (p. {f['page']}){warn}"
    else:
        s += f" (p. {f['page']}{', quote checked' if v['status'] == 'ok' else warn})"
    return s


def render_stories(today, syn, results, crosses, one_lines, failures, cost, store, capped) -> str:
    docs, fmap = syn["docs"], syn["fmap"]
    n_docs = len(docs)
    L = [f"# OPM what's what — {today}", ""]
    L.append(f"{n_docs} documents read in full, grouped into {len(syn['stories'])} stories. "
             f"Each story shows the key points; open “All findings” for everything else, with quotes. "
             f"Every quote has been checked against the document.")
    if capped:
        L += ["", f"**Not read this week:** {capped}"]
    soon = sorted([v for v in store.values() if v["source"] == "fr" and v.get("comments_close")
                   and today <= v["comments_close"] <= (date.fromisoformat(today) + timedelta(days=45)).isoformat()],
                  key=lambda v: v["comments_close"])
    if soon:
        L += ["", "**Comment periods closing soon:** " + "; ".join(
            f"[{v['title']}]({v['url']}) ({v['comments_close']})" for v in soon)]
    cross_by_doc = {}
    for c, rule, x in crosses:
        cross_by_doc.setdefault(c["url"], []).append((c, rule, x))
    for n, story in enumerate(syn["stories"], 1):
        its = [docs[d] for d in story["doc_ids"]]
        L += ["", f"## {n}. {story['headline']}", ""]
        if story["takeaway"]:
            L += [story["takeaway"], ""]
        L.append("*" + " · ".join(f"[{it['title']}]({it['url']}) ({it.get('kind')}, {it.get('date')})" for it in its) + "*")
        L.append("")
        for fid in story["key_findings"]:
            it, f, v = fmap[fid]
            if fid.startswith("X"):
                d = f["conflict"]
                L.append(f"- **{f['headline']}.** {it.get('kind')}: “{d['companion_quote']}” (p. {d['companion_page']}) · "
                         f"Rule: “{d['rule_quote']}” (p. {d['rule_page']})")
            else:
                L.append(_finding_md(f, v, with_quote=False))
        # Everything else for this story, collapsed
        body, total = [], 0
        for it in its:
            r = results.get(it["url"])
            if not r:
                continue
            total += len(r["findings"]["items"])
            body += [f"**{it['title']}** — {r['findings']['plain_summary']}", ""]
            for f, v in zip(r["findings"]["items"], r["verification"]):   # model's importance order
                body.append(_finding_md(f, v, with_quote=True))
            if r["findings"]["could_not_determine"]:
                body.append("- _The document doesn't say:_ " + "; ".join(r["findings"]["could_not_determine"]))
            for c, rule, x in cross_by_doc.get(it["url"], []):
                if x["findings"]["describes_this_rule"] and x["findings"]["left_out"]:
                    k = c.get("kind") or "companion"
                    k = k if k.startswith("CHCO") else k.lower()
                    body.append(f"- _What the rule does that the {k} doesn't mention:_")
                    body += [f"  - {s.rstrip('.')}" for s in x["findings"]["left_out"]]
            body.append("")
        if body:
            L += ["", f"<details><summary>All findings ({total}) and quotes</summary>", ""] + body + ["</details>"]
    routine = [docs[d] for d in syn["routine"]]
    if routine or one_lines:
        L += ["", "## Routine", ""]
        L += [f"- [{it['title']}]({it['url']}) — {it.get('kind')}, {it.get('date')}: "
              f"{results[it['url']]['findings']['plain_summary']}" for it in routine]
        L += [f"- [{it['title']}]({it['url']}) — {it.get('kind') or it['source']}, "
              f"{it.get('date') or 'first seen ' + it['first_seen'][:10]} ({triage(it)[1]}; not read)" for it in one_lines]
    L += ["", "---", f"Model: {MODEL} (batch, effort {EFFORT}); grouping by the same model. Cost this run: ${cost:.2f}."]
    probs = failures + syn.get("problems", [])
    if probs:
        L += ["", "**Problems this run:**"] + [f"- {x}" for x in probs]
    return "\n".join(L) + "\n"


def synthesis(raw: dict, reads: list[dict], crosses: list) -> dict:
    """Run the grouping call once per week and cache it in raw["synthesis"]; later
    re-renders reuse it. Read items with no result are not part of any story."""
    from synthesize import build_payload
    read_items = [it for it in reads if it["url"] in raw["results"]]
    _, docs, fmap = build_payload(read_items, raw["results"], crosses)
    if "synthesis" not in raw:
        if not read_items:
            raw["synthesis"] = {"stories": [], "routine": [], "problems": [], "cost_usd": 0.0}
        else:
            s = synthesize(make_client(), read_items, raw["results"], crosses)
            raw["synthesis"] = {k: s[k] for k in ("stories", "routine", "problems", "cost_usd")}
        raw["synthesis"]["doc_urls"] = {d: it["url"] for d, it in docs.items()}
    elif raw["synthesis"].get("doc_urls") != {d: it["url"] for d, it in docs.items()}:
        raise SystemExit("saved grouping refers to documents in a different order; delete raw['synthesis'] and rerun")
    return {**raw["synthesis"], "docs": docs, "fmap": fmap}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--cap", type=float, default=3.0, help="max estimated $ (batch prices) per run")
    ap.add_argument("--dry-run", action="store_true", help="count tokens and stop before the batch")
    ap.add_argument("--from-json", help="re-render a digest from a saved <digest>.json (no model calls)")
    args = ap.parse_args()
    today = date.today().isoformat()

    store = json.loads(STORE.read_text())
    items = pick_items(store, args.start, args.end)
    reads = [it for it in items if triage(it)[0] == "read"]
    one_lines = [it for it in items if triage(it)[0] == "one_line"]
    print(f"{len(items)} new items: {len(reads)} to read, {len(one_lines)} one line")

    failures, texts = [], {}
    for it in reads:
        try:
            texts[it["url"]] = document_text(it)
        except Exception as e:  # listed in the digest, never silently dropped
            failures.append(f"Couldn't fetch [{it['title']}]({it['url']}): {e}")
    pairs = [(c, r) for c, r in pair_companions(reads, store) if c["url"] in texts]
    for c, r in pairs:
        if r["url"] not in texts:
            try:
                texts[r["url"]] = document_text(r)
            except Exception as e:
                failures.append(f"Couldn't fetch rule for cross-check [{r['title']}]({r['url']}): {e}")
    pairs = [(c, r) for c, r in pairs if r["url"] in texts]
    print(f"cross-checks: {len(pairs)}", *[f"\n  {c['title'][:60]} <-> {r['title'][:60]}" for c, r in pairs])

    if args.from_json:
        raw = json.loads(Path(args.from_json).read_text())
        by_url = {**{it["url"]: it for it in reads}, **{v["url"]: v for v in store.values()}}
        crosses = [(by_url[c], by_url[r], x) for c, r, x in raw["crosses"]]
        syn = synthesis(raw, reads, crosses)
        Path(args.from_json).write_text(json.dumps(raw, indent=1, ensure_ascii=False))
        md = render_stories(today, syn, raw["results"], crosses, one_lines, raw["failures"],
                            raw["cost_usd"] + raw["synthesis"]["cost_usd"], store, None)
        Path(args.from_json).with_suffix(".md").write_text(md, encoding="utf-8")
        print("re-rendered", args.from_json)
        return 0

    client = make_client()
    reqs = {}
    for i, it in enumerate([it for it in reads if it["url"] in texts]):
        reqs[f"read-{i}"] = ("read", it, build_request(it, texts[it["url"]], own_summary(it, texts[it["url"]]), MODEL, EFFORT))
    for j, (c, r) in enumerate(pairs):
        reqs[f"cross-{j}"] = ("cross", (c, r), build_crosscheck_request(c, texts[c["url"]], r, texts[r["url"]], MODEL, EFFORT))

    n_in = sum(count_input_tokens(client, q) for _, _, q in reqs.values())
    pin, pout = PRICES[MODEL]
    est = (n_in * pin + len(reqs) * EST_OUTPUT_PER_REQUEST * pout) / 1e6 * 0.5
    print(f"{len(reqs)} requests, {n_in:,} input tokens; estimated batch cost ${est:.2f} (cap ${args.cap:.2f})")
    capped = None
    if est > args.cap:
        capped = (f"the estimated cost (${est:.2f} for {n_in:,} input tokens) exceeded this run's ${args.cap:.2f} cap, "
                  f"so nothing was sent to the model. Raise the cap and rerun to read these.")
    if args.dry_run:
        return 0

    results, crosses, cost = {}, [], 0.0
    if reqs and not capped:
        batch = client.messages.batches.create(requests=[
            Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**q)) for cid, (_, _, q) in reqs.items()])
        print("batch", batch.id, flush=True)
        poll(client, batch.id)
        for res in client.messages.batches.results(batch.id):
            kind, obj, q = reqs[res.custom_id]
            title = obj["title"] if kind == "read" else f"cross-check {obj[0]['title']}"
            if res.result.type != "succeeded":
                failures.append(f"Model request for {title!r} {res.result.type}")
                continue
            try:
                r = parse_message(res.result.message, MODEL, schema=None if kind == "read" else CrossCheck, batch=True)
            except Exception as e:
                failures.append(f"Model output for {title!r} unusable: {e}")
                continue
            cost += r["cost_usd"]
            if kind == "read":
                r["verification"] = check(texts[obj["url"]], r["findings"]["items"])
                results[obj["url"]] = r
            else:
                c, rule = obj
                ds = r["findings"]["discrepancies"]
                vc = check(texts[c["url"]], [{"quote": d["companion_quote"], "page": d["companion_page"]} for d in ds])
                vr = check(texts[rule["url"]], [{"quote": d["rule_quote"], "page": d["rule_page"]} for d in ds])
                r["verification"] = [(a["status"], b["status"]) for a, b in zip(vc, vr)]
                crosses.append((c, rule, r))

    raw = {"results": results, "crosses": [(c["url"], r["url"], x) for c, r, x in crosses],
           "failures": failures, "cost_usd": round(cost, 4)}
    syn = synthesis(raw, reads, crosses)
    md = render_stories(today, syn, results, crosses, one_lines, failures,
                        cost + raw["synthesis"]["cost_usd"], store, capped)
    DIGESTS.mkdir(exist_ok=True)
    out = DIGESTS / (f"{today}.md" if not args.start else f"test-{args.start}.md")
    out.write_text(md, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(raw, indent=1, ensure_ascii=False))
    if not args.start:  # tests never mark items digested
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for it in items:
            store[it["url"]]["digested_at"] = now
        STORE.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True))
    print(f"wrote {out.relative_to(ROOT)}; cost ${cost:.2f}; problems: {len(failures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
