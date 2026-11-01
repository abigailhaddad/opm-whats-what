"""Run one week's documents through the reader on several models and compare.

Same documents, same instructions, same effort for every model. Reports per
model: findings, quotes verified (verbatim on the cited page), cost from the
API's own usage numbers, and wall time. Coverage (who caught what) is judged
afterwards from the saved outputs.

Before spending anything it counts input tokens with the free count_tokens
endpoint and refuses to run if the estimated cost exceeds --cap.

Usage: python explore/model_test.py --start 2026-10-19 --end 2026-10-25 [--cap 5]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import anthropic  # noqa: E402

from fetch_text import document_text, own_summary  # noqa: E402
from read import PRICES, build_request, count_input_tokens, make_client, read_document  # noqa: E402
from triage import triage  # noqa: E402
from verify import check  # noqa: E402

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
EST_OUTPUT_TOKENS_PER_DOC = 12000  # generous: findings + thinking at effort high


def week_items(start: str, end: str) -> list[dict]:
    items = []
    for r in json.load(open(ROOT / "data" / "fr_opm_12mo.json"))["results"]:
        if start <= r["publication_date"] <= end:
            items.append({"source": "fr", "url": r["html_url"], "title": r["title"], "date": r["publication_date"],
                          "kind": r["type"], "raw_text_url": r["raw_text_url"], "abstract": r.get("abstract"),
                          "agencies": [a.get("slug") for a in r.get("agencies") or []]})
    for v in json.load(open(ROOT / "data" / "items.json")).values():
        if v["source"] in ("chco", "news") and start <= (v.get("date") or "") <= end:
            items.append(v)
    return [it for it in items if triage(it)[0] == "read"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--cap", type=float, default=5.0, help="max estimated $ for the whole comparison")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--models", nargs="*", default=MODELS)
    args = ap.parse_args()

    client = make_client()
    out_dir = ROOT / "explore" / "model_test" / args.start
    out_dir.mkdir(parents=True, exist_ok=True)

    items = week_items(args.start, args.end)
    print(f"{len(items)} documents to read:")
    docs = []
    for it in items:
        text = document_text(it)
        docs.append((it, text, own_summary(it, text)))
        print(f"  {it['date']} {it.get('kind',''):14} {len(text.split()):>6} words  {it['title'][:70]}")

    # Free token count, then a hard cost guard before any paid call.
    est_total, reqs = 0.0, []
    for model in args.models:
        pin, pout = PRICES[model]
        m_in = 0
        for i, (it, text, summ) in enumerate(docs):
            req = build_request(it, text, summ, model, args.effort)
            n = count_input_tokens(client, req)
            m_in += n
            reqs.append((model, i, req))
        est = (m_in * pin + len(docs) * EST_OUTPUT_TOKENS_PER_DOC * pout) / 1e6
        est_total += est
        print(f"{model}: {m_in:,} input tokens counted; estimated cost ${est:.2f}")
    if est_total > args.cap:
        print(f"Estimated ${est_total:.2f} exceeds cap ${args.cap:.2f}; not running.")
        return 1

    results = {}

    def run(model, i, req):
        t0 = time.time()
        r = read_document(client, req)
        r["seconds"] = round(time.time() - t0, 1)
        return model, i, r

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(run, m, i, req) for m, i, req in reqs]
        for f in as_completed(futs):
            try:
                model, i, r = f.result()
            except Exception as e:  # report, don't hide
                print("ERROR:", repr(e)[:300])
                continue
            it, text, _ = docs[i]
            r["verification"] = check(text, r["findings"]["items"])
            r["doc"] = {"title": it["title"], "url": it["url"], "date": it.get("date"), "kind": it.get("kind")}
            results[(model, i)] = r
            (out_dir / f"{model}__{i}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False))
            ok = sum(v["status"] == "ok" for v in r["verification"])
            print(f"  done {model:18} doc{i} {len(r['verification'])} findings, {ok} verified, ${r['cost_usd']:.3f}, {r['seconds']}s")

    print("\nmodel                findings  verified  wrong page  not found   cost     max s")
    for model in args.models:
        rs = [r for (m, _), r in results.items() if m == model]
        v = [x["status"] for r in rs for x in r["verification"]]
        print(f"{model:20} {len(v):>8} {v.count('ok'):>9} {v.count('wrong_page'):>11} {v.count('not_found'):>10}"
              f"   ${sum(r['cost_usd'] for r in rs):.3f}  {max((r['seconds'] for r in rs), default=0):>7}"
              f"   ({len(rs)}/{len(docs)} docs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
