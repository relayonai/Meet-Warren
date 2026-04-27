"""Compliance enforcer — revise content to fix the issues raised by the analyzer.

Two layers of enforcement:
1. Deterministic substitution for known banned phrases / terms / disclaimer insertion.
   Fast, free, and doesn't risk regressing other content.
2. LLM revision for principle-level issues (advice boundary, suitability tone, etc.)
   where structural rewriting is needed.
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

import anthropic

from .rulebook import Rulebook, load_rulebook

log = logging.getLogger(__name__)


REVISER_SYSTEM = (
    "You are a senior UK financial-marketing copy editor enforcing the Meet Warren "
    "Marketing Compliance Guidebook. You revise copy to be compliant while preserving "
    "meaning, structure, and HTML formatting."
)

# We use a delimited block format instead of JSON because blog HTML is often
# 8–15 KB; embedding raw HTML inside a JSON string forces the model to escape
# every quote/newline, which frequently truncates and produces "Unterminated
# string" parse errors. Delimiters are robust at any size.
REVISED_OPEN  = "<<<REVISED_CONTENT_START>>>"
REVISED_CLOSE = "<<<REVISED_CONTENT_END>>>"
CHANGES_OPEN  = "<<<CHANGES_JSON_START>>>"
CHANGES_CLOSE = "<<<CHANGES_JSON_END>>>"

REVISER_TEMPLATE = f"""Revise the CONTENT below to satisfy every COMPLIANCE SUGGESTION.

Constraints:
- Preserve the existing HTML/Markdown structure (tags, headings, links, lists). Do not strip styling.
- Keep tone and length comparable to the original.
- Apply every suggestion. Where a suggestion provides a replacement, use it.
- Use UK English.
- Do not introduce new factual claims, names, or numbers.
- If a 'not financial advice' disclaimer is missing, insert it near the foot of the content
  (in the existing footer if there is one).

Output format — VERY IMPORTANT, follow EXACTLY:
1. First, the full revised content between these markers (raw HTML, no escaping):

{REVISED_OPEN}
<full revised content goes here>
{REVISED_CLOSE}

2. Then, a JSON array of short bullets describing each change you made:

{CHANGES_OPEN}
["change 1", "change 2", "..."]
{CHANGES_CLOSE}

Do NOT wrap in code fences. Do NOT add commentary outside the markers.

COMPLIANCE SUGGESTIONS (apply ALL):
{{suggestions_json}}

CONTENT:
{{content}}
"""


def _parse_revised_response(text: str, fallback: str) -> tuple[str, list[str]]:
    """Parse the delimited LLM response. Returns (revised_content, changes_made)."""
    revised = fallback
    changes: list[str] = []
    try:
        if REVISED_OPEN in text and REVISED_CLOSE in text:
            start = text.index(REVISED_OPEN) + len(REVISED_OPEN)
            end   = text.index(REVISED_CLOSE, start)
            candidate = text[start:end].strip()
            # Reject trivially empty / clearly truncated payloads
            if len(candidate) >= max(64, int(len(fallback) * 0.5)):
                revised = candidate
            else:
                log.warning("Revised content looked truncated (%d chars vs %d original); "
                            "keeping original.", len(candidate), len(fallback))
        else:
            log.warning("Reviser output missing content markers; keeping original.")
    except Exception as e:
        log.warning("Could not extract revised content: %s", e)

    try:
        if CHANGES_OPEN in text and CHANGES_CLOSE in text:
            cs = text.index(CHANGES_OPEN) + len(CHANGES_OPEN)
            ce = text.index(CHANGES_CLOSE, cs)
            arr = json.loads(text[cs:ce].strip())
            if isinstance(arr, list):
                changes = [str(c) for c in arr]
    except Exception as e:
        log.warning("Could not parse changes_made JSON: %s", e)

    return revised, changes


# ---------------------------------------------------------------------------
# Layer 1 — deterministic substitutions
# ---------------------------------------------------------------------------

def _substitute_banned(content: str, rb: Rulebook) -> tuple[str, list[str]]:
    """Apply word-boundary substitutions for banned phrases/terms with known replacements."""
    revised = content
    changes: list[str] = []
    for rule in rb.hard_rules:
        if rule.kind not in ("banned_phrase", "banned_term"):
            continue
        if not rule.suggested_replacement:
            continue
        pattern = re.compile(r"\b" + re.escape(rule.pattern) + r"\b", flags=re.I)
        if pattern.search(revised):
            revised = pattern.sub(rule.suggested_replacement, revised)
            changes.append(f"Replaced '{rule.pattern}' with '{rule.suggested_replacement}' (§{rule.section}).")
    return revised, changes


def _ensure_disclaimer(content: str, rb: Rulebook) -> tuple[str, list[str]]:
    """If the content lacks a disclaimer, append one inside the footer (or at the end)."""
    plain_low = re.sub(r"<[^>]+>", " ", content).lower()
    if any(f in plain_low for f in (
        "not financial advice",
        "does not provide financial advice",
        "scenarios only, not advice",
    )):
        return content, []

    disclaimer = (
        rb.canonical_disclaimers[0]
        if rb.canonical_disclaimers
        else "Warren is not financial advice. It helps you explore scenarios to support your decisions."
    )
    block = (
        f'<div style="margin-top:18px;padding:14px 16px;background:#fdf6e3;'
        f'border-left:4px solid #c9a227;border-radius:6px;font-size:12px;color:#5a6478;">'
        f'<strong>Important · </strong>{disclaimer}</div>'
    )

    # Try to insert just before </footer>; else before </body>; else append.
    for marker in ("</footer>", "</body>"):
        if marker in content:
            revised = content.replace(marker, block + "\n" + marker, 1)
            return revised, [f"Inserted required disclaimer above {marker} (§2.5.2)."]
    return content + "\n" + block, ["Appended required disclaimer to end of content (§2.5.2)."]


# ---------------------------------------------------------------------------
# Layer 2 — LLM revision for principle-level issues
# ---------------------------------------------------------------------------

def _llm_revise(
    content: str,
    suggestions: list[dict],
    client: anthropic.Anthropic,
    model: str,
) -> tuple[str, list[str]]:
    if not suggestions:
        return content, []
    # Size the output budget generously: revised content is roughly the same
    # size as the input, plus the changes JSON. 1 token ≈ ~3.5 chars of HTML,
    # so allocate ~content_len/3 + 2000 buffer, capped to model's max.
    max_tok = min(32000, max(8000, len(content) // 3 + 2000))
    prompt = REVISER_TEMPLATE.format(
        suggestions_json=json.dumps(suggestions, indent=2, ensure_ascii=False),
        content=content,
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tok,
            system=REVISER_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
    except Exception as exc:
        log.error("LLM revision call failed: %s", exc)
        return content, []
    revised, changes = _parse_revised_response(text, fallback=content)
    return revised, changes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def revise_content(
    content: str,
    analysis: dict,
    *,
    client: Optional[anthropic.Anthropic] = None,
    model: str = "claude-sonnet-4-5",
    rulebook: Optional[Rulebook] = None,
) -> dict:
    """Apply deterministic + LLM revisions. Returns {revised, changes:[...]}."""
    rb = rulebook or load_rulebook()
    suggestions: List[dict] = analysis.get("improvement_suggestions", [])

    revised = content
    changes: list[str] = []

    # Layer 1: deterministic substitutions for language / topic categories
    if any(s.get("category") in ("language", "topic") for s in suggestions):
        revised, ch = _substitute_banned(revised, rb)
        changes.extend(ch)

    # Layer 1: disclaimer insertion if needed
    if any(s.get("category") == "disclaimer" for s in suggestions):
        revised, ch = _ensure_disclaimer(revised, rb)
        changes.extend(ch)

    # Layer 2: anything left over (principle-level, or hard rules without a known replacement)
    leftover = [s for s in suggestions if s.get("category") in ("principle",) or
                (s.get("category") in ("language", "topic") and not _was_handled(s, changes))]
    if leftover and client is not None:
        revised, ch = _llm_revise(revised, leftover, client, model)
        changes.extend(ch)

    return {"revised_content": revised, "changes_made": changes}


def _was_handled(suggestion: dict, change_log: list[str]) -> bool:
    """Heuristic: did a deterministic substitution already cover this suggestion?"""
    target = (suggestion.get("evidence") or "").lower()
    if not target:
        return False
    return any(target[:30] in c.lower() for c in change_log if "Replaced" in c)
