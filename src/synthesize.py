"""Group a week's documents into stories and pick each story's key findings.

Input: the week's verified read results and cross-checks. Output: stories, each
with a short headline, a 1-2 sentence takeaway, the documents in it, and up to
3 key finding IDs. The model can only POINT TO findings (IDs like "D3.F2") that
already passed quote verification; unknown or unverified IDs are dropped and
reported. The takeaway is the only new prose, and the prompt requires it to
rest on the selected findings.

One synchronous Opus call (small: summaries and headlines, not full texts).
"""
from __future__ import annotations

from pydantic import BaseModel

from read import SYSTEM, parse_message


class Story(BaseModel):
    headline: str
    takeaway: str
    doc_ids: list[str]
    key_findings: list[str]


class Stories(BaseModel):
    stories: list[Story]
    routine_doc_ids: list[str]


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["stories", "routine_doc_ids"],
    "properties": {
        "routine_doc_ids": {"type": "array", "items": {"type": "string"}},
        "stories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["headline", "takeaway", "doc_ids", "key_findings"],
            "properties": {"headline": {"type": "string"}, "takeaway": {"type": "string"},
                           "doc_ids": {"type": "array", "items": {"type": "string"}},
                           "key_findings": {"type": "array", "items": {"type": "string"}}}}},
    },
}

INSTRUCTIONS = """Below is everything OPM published this week that was read in full, with each document's plain summary and its verified findings (ID, headline, detail). Cross-checks compare a press release or memo with the rule it describes.

Organize this into stories for a busy federal-workforce policy analyst:
- A story is one policy change or topic. Put a rule together with its press release, its CHCO memo, and closely related notices. Every document belongs to exactly one story, or to routine_doc_ids if it is routine paperwork with nothing an analyst needs.
- headline: at most 12 words, plain English, saying what is happening (not a document title).
- takeaway: 1-2 sentences on what actually changes and for whom. Use only facts contained in the findings you select for this story; do not add anything else.
- key_findings: up to 3 finding IDs, most important to the analyst first, judged by consequence: who is affected and how much. Whether a document's own summary mentions a finding is not a reason to pick or skip it. Include a cross-check conflict if there is one (its ID starts with X).
- Size each story to its importance: a major rule gets up to 3 key findings; a minor item gets 1. Paperwork notices for forms or applications that don't change anyone's rights, benefits or obligations belong in routine_doc_ids, not in a story. Burden-hour estimates are rarely key findings.
- Order stories by importance to the analyst, most important first.

{payload}"""


def build_payload(reads: list[dict], results: dict, crosses: list) -> tuple[str, dict, dict]:
    """Return (payload text, doc_id -> item, finding_id -> (item, finding, verification))."""
    docs, fmap, lines = {}, {}, []
    for di, it in enumerate(reads):
        r = results.get(it["url"])
        if not r:
            continue
        did = f"D{di}"
        docs[did] = it
        lines.append(f"\n## {did}: {it['title']} ({it.get('kind')}, {it.get('date')})")
        lines.append(f"Plain summary: {r['findings']['plain_summary']}")
        for fi, (f, v) in enumerate(zip(r["findings"]["items"], r["verification"])):
            if v["status"] != "ok":
                continue  # only verified findings can be selected
            fid = f"{did}.F{fi}"
            fmap[fid] = (it, f, v)
            lines.append(f"- {fid} {f['headline']}: {f['what_changes']}")
    for xi, (c, rule, x) in enumerate(crosses):
        f = x["findings"]
        if not f["describes_this_rule"]:
            continue
        for k, (d, v) in enumerate(zip(f["discrepancies"], x["verification"])):
            if tuple(v) != ("ok", "ok"):
                continue
            fid = f"X{xi}.C{k}"
            fmap[fid] = (c, {"headline": f"{c.get('kind')} conflicts with the rule on {d['topic'].lower()}",
                             "conflict": d, "rule": rule}, ("ok", "ok"))
            lines.append(f"\n- {fid} CROSS-CHECK CONFLICT ({c['title']} vs. {rule['title']}): "
                         f"{d['topic']}: companion says {d['companion_says']}; rule says {d['rule_says']}")
    return INSTRUCTIONS.format(payload="\n".join(lines)), docs, fmap


def synthesize(client, reads, results, crosses, model="claude-opus-5-5", effort="medium"):
    payload, docs, fmap = build_payload(reads, results, crosses)
    with client.messages.stream(
        model=model, max_tokens=32000, system=SYSTEM,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": payload}],
    ) as s:
        msg = s.get_final_message()
    out = parse_message(msg, model, schema=Stories)
    st, problems = out["findings"], []
    placed = set()
    for story in st["stories"]:
        bad = [f for f in story["key_findings"] if f not in fmap]
        if bad:
            problems.append(f"story {story['headline']!r}: dropped unknown/unverified finding IDs {bad}")
        story["key_findings"] = [f for f in story["key_findings"] if f in fmap][:3]
        story["doc_ids"] = [d for d in story["doc_ids"] if d in docs]
        placed |= set(story["doc_ids"])
    placed |= {d for d in st["routine_doc_ids"] if d in docs}
    missing = [d for d in docs if d not in placed]
    if missing:  # never lose a document: give each one its own story
        problems.append(f"documents not placed by the model, shown on their own: {missing}")
        for d in missing:
            st["stories"].append({"headline": docs[d]["title"], "takeaway": "", "doc_ids": [d], "key_findings": []})
    return {"stories": st["stories"], "routine": [d for d in st["routine_doc_ids"] if d in docs],
            "docs": docs, "fmap": fmap, "problems": problems, "cost_usd": out["cost_usd"], "usage": out["usage"]}
