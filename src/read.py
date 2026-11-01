"""Read one document in full and return findings as validated JSON.

One model call per document (streamed; structured JSON output). Each finding
says what the document does, whether the document's own summary covers it
(opposite / absent / vague / in), who is speaking in the quoted passage, and
a verbatim quote with its page, which verify.py checks afterwards.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent

# $ per million tokens, from the Claude API pricing table (cached 2026-10-06).
# Batch API is 50% of these. Haiku 5.5 is $0.50/$2.50 for prompts over 100K tokens.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def load_env() -> None:
    """Load ANTHROPIC_API_KEY / ANTHROPIC_WORKSPACE_ID from .env if present (local
    runs); CI sets them as environment variables."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID"):
                if line.startswith(name + "=") and not os.environ.get(name):
                    os.environ[name] = line.split("=", 1)[1].strip().strip('"').strip("'")


def make_client() -> anthropic.Anthropic:
    """Client for this project. The key in use isn't scoped to a workspace, so
    requests must name one via the anthropic-workspace-id header."""
    load_env()
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)


class Finding(BaseModel):
    headline: str
    what_changes: str
    summary_status: Literal["opposite", "absent", "vague", "in"]
    speaker: Literal["OPM", "commenter", "current_rule", "other_agency", "other"]
    quote: str
    page: int
    section: str


class Findings(BaseModel):
    plain_summary: str
    items: list[Finding]
    could_not_determine: list[str]


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["plain_summary", "items", "could_not_determine"],
    "properties": {
        "plain_summary": {"type": "string"},
        "could_not_determine": {"type": "array", "items": {"type": "string"}},
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["headline", "what_changes", "summary_status", "speaker", "quote", "page", "section"],
            "properties": {
                "headline": {"type": "string"},
                "what_changes": {"type": "string"},
                "summary_status": {"type": "string", "enum": ["opposite", "absent", "vague", "in"]},
                "speaker": {"type": "string", "enum": ["OPM", "commenter", "current_rule", "other_agency", "other"]},
                "quote": {"type": "string"},
                "page": {"type": "integer"},
                "section": {"type": "string"},
            }}},
    },
}

SYSTEM = """You read U.S. Office of Personnel Management (OPM) documents in full for a federal-workforce policy analyst who will not read them. Your job is to surface the substance that matters, especially what the document's own summary leaves out or describes vaguely.

Describe what the document says and does. Do not editorialize about whether a change is good or bad."""

INSTRUCTIONS = """Document type: {kind}
Title: {title}
Published: {date}

The document's own summary (what a reader skimming it would see):
<summary>
{summary}
</summary>

Read the entire document below, including any discussion of comments, cost analysis, and regulatory text. Then return the findings an analyst would most want, most important first. Use as many as the document warrants: up to 10 for a long rule, often 1-3 for a short notice or memo, and zero is fine for a routine document.

Prefer concrete specifics: who is affected and how, rights or protections added or removed, numbers (people affected, costs, comment counts), deadlines and effective dates, delegations of authority, provisions that appear only in the regulatory text, places where the summary or preamble describes a change more softly than the text, and what OPM declined to change.

For each finding:
- summary_status: "opposite" if the summary says or implies the reverse of what the text does; "absent" if the summary doesn't mention it; "vague" if the summary names the topic but not what actually changes; "in" if the summary states it.
- speaker: who is speaking in the quoted passage: "OPM" (OPM's own statement or regulatory text), "commenter" (a public commenter's argument or example, even if OPM responds to it), "current_rule" (text describing the rule being replaced), "other_agency", or "other". If OPM concedes a commenter's example, quote OPM's concession, not the commenter.
- quote: an EXACT verbatim quote of 1-3 sentences that supports the finding, copied character for character. Do not combine text from two places, and do not cross a [[Page N]] marker.
- page: the number in the last [[Page N]] marker before the quote.
- section: the CFR section or heading it falls under.

Also give a 2-sentence plain_summary, and list in could_not_determine anything an analyst would want that the document doesn't say (for example, how many people are affected).

<document>
{text}
</document>"""


def build_request(item: dict, text: str, summary: str, model: str, effort: str) -> dict:
    return dict(
        model=model,
        max_tokens=32000,
        system=SYSTEM,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": INSTRUCTIONS.format(
            kind=item.get("kind") or item["source"], title=item["title"], date=item.get("date") or "",
            summary=summary, text=text)}],
    )


def count_input_tokens(client: anthropic.Anthropic, req: dict) -> int:
    r = client.messages.count_tokens(model=req["model"], system=req["system"], messages=req["messages"],
                                     output_config=req["output_config"])
    return r.input_tokens


def parse_message(msg, model: str, schema=None, batch: bool = False) -> dict:
    """Validate a finished response. Raises on refusal, truncation or invalid JSON:
    nothing is silently dropped. Batch results are billed at half price."""
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"refusal: {getattr(msg, 'stop_details', None)}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("hit max_tokens; output truncated")
    text = "".join(b.text for b in msg.content if b.type == "text")
    parsed = (schema or Findings).model_validate(json.loads(text))
    pin, pout = PRICES[model]
    u = msg.usage
    cost = (u.input_tokens * pin + u.output_tokens * pout) / 1e6 * (0.5 if batch else 1.0)
    return {"findings": parsed.model_dump(), "model": model, "stop_reason": msg.stop_reason,
            "usage": {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}, "cost_usd": round(cost, 4)}


def read_document(client: anthropic.Anthropic, req: dict) -> dict:
    """Run one read synchronously (used by the model comparison)."""
    with client.messages.stream(**req) as stream:
        msg = stream.get_final_message()
    return parse_message(msg, req["model"])


# --------------------------------------------------------------------------- #
# Cross-check: a press release or CHCO memo against the rule it describes.
# --------------------------------------------------------------------------- #
class Discrepancy(BaseModel):
    topic: str
    companion_says: str
    companion_quote: str
    companion_page: int
    rule_says: str
    rule_quote: str
    rule_page: int


class CrossCheck(BaseModel):
    describes_this_rule: bool
    discrepancies: list[Discrepancy]
    left_out: list[str]


CROSS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["describes_this_rule", "discrepancies", "left_out"],
    "properties": {
        "describes_this_rule": {"type": "boolean"},
        "left_out": {"type": "array", "items": {"type": "string"}},
        "discrepancies": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["topic", "companion_says", "companion_quote", "companion_page",
                         "rule_says", "rule_quote", "rule_page"],
            "properties": {k: {"type": "integer" if k.endswith("_page") else "string"}
                           for k in ["topic", "companion_says", "companion_quote", "companion_page",
                                     "rule_says", "rule_quote", "rule_page"]}}},
    },
}

CROSS_INSTRUCTIONS = """Below are two OPM documents: a {ckind} ("companion") and a Federal Register rule it may describe.

First decide whether the companion is actually about this rule (describes_this_rule). If not, return no discrepancies.

If it is, list every factual discrepancy between them: dates (effective dates, deadlines), numbers, who is covered, what is required or allowed. For each, give an EXACT verbatim quote from each document (1-2 sentences, copied character for character, not crossing a [[Page N]] marker) and the page from the last [[Page N]] marker before it. Only list real conflicts, not differences in emphasis.

Then list in left_out (one line each, at most 6) the most consequential things the rule does that the companion does not mention.

<companion title="{ctitle}">
{ctext}
</companion>

<rule title="{rtitle}">
{rtext}
</rule>"""


def build_crosscheck_request(companion: dict, ctext: str, rule: dict, rtext: str, model: str, effort: str) -> dict:
    return dict(
        model=model, max_tokens=32000, system=SYSTEM,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": CROSS_SCHEMA}},
        messages=[{"role": "user", "content": CROSS_INSTRUCTIONS.format(
            ckind=companion.get("kind") or companion["source"], ctitle=companion["title"], ctext=ctext,
            rtitle=rule["title"], rtext=rtext)}],
    )
