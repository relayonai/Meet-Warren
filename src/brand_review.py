"""Brand voice reviewer for Warren-generated content.

Audits a drafted blog post or newsletter against Warren's brand voice,
terminology, and messaging consistency using the Answer Machine KB as
the authoritative source of brand truth.

Public API:
    review_brand_voice(content, kb, client, model) -> dict

Returns:
    {
      "grade":   "pass" | "warn" | "fail",
      "issues":  [{"severity": "critical"|"warning"|"suggestion",
                   "field":    str,   # e.g. "tone", "terminology", "cta"
                   "finding":  str,
                   "suggestion": str}],
      "summary": str,   # one-sentence overall assessment
      "elapsed_seconds": float,
    }
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import anthropic

from ._json import parse_json_response
from .answer_machine import KnowledgeBase, load_knowledge_base

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_STATIC = """\
You are a brand voice editor for Meet Warren — a UK AI-powered financial \
planning workspace. Your job is to audit generated content (blog posts and \
newsletters) against Warren's brand voice, terminology, and messaging \
principles.

ABSOLUTE RULES you enforce:
1. Warren is NOT a financial adviser. Content must NEVER imply it gives \
regulated financial advice.
2. UK English throughout (organisation, favour, programme, £).
3. Tone: calm, confident, motivated, judgement-free, lightly witty. \
Never preachy, never jargon-heavy, never breathless fintech hype.
4. We are democratising personal finance — accessible, human-first, \
not elitist or condescending.
5. "Teach a person to fish" philosophy — clarity and tools, not \
prescriptive recommendations.
6. No crypto / speculative assets promotion. No performance promises.
7. Comparisons (IFA vs Warren = dentist vs toothpaste) are encouraged \
where they aid clarity.
8. Internal links only to meetwarren.co.uk and its subpages; never \
external CTAs to competitors.

Your output is a structured JSON audit — not a rewrite.
"""

_REVIEW_TEMPLATE = """\
Audit the CONTENT below against Warren's brand voice and messaging \
principles. Use the BRAND CONTEXT block to calibrate your judgement.

For each issue you find, classify it as:
- "critical": directly contradicts a brand rule or compliance boundary \
  (e.g. sounds like financial advice, uses banned phrasing, wrong English \
  variant, promotes speculative assets)
- "warning": tone drift, inconsistent terminology, weak CTA, style that \
  would confuse or alienate the audience
- "suggestion": minor polish — word choice, rhythm, engagement \
  improvements that are optional but worth flagging

Return ONLY a JSON object matching this schema exactly:
{{
  "issues": [
    {{
      "severity":   "critical" | "warning" | "suggestion",
      "field":      "<short category: tone | terminology | advice_boundary | cta | structure | uk_english | messaging>",
      "finding":    "<what is wrong — quote <=15 words from the content where possible>",
      "suggestion": "<concrete fix in <=25 words>"
    }}
  ],
  "summary": "<one sentence: overall verdict and the single most important issue>"
}}

If you find no issues, return {{"issues": [], "summary": "Content aligns well with Warren brand voice."}}

BRAND CONTEXT:
{brand_context}

CONTENT (kind={kind}, word count ≈{word_count}):
{content_excerpt}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_brand_context(kb: KnowledgeBase) -> str:
    """Compact KB block suitable for inclusion in the user prompt."""
    parts: list[str] = []

    parts.append("=== BRAND NARRATIVE ===\n")
    parts.append((kb.brand_narrative or "")[:3000])
    parts.append("\n\n")

    if kb.brand_voice_principles:
        parts.append("=== VOICE PRINCIPLES ===\n")
        for p in kb.brand_voice_principles:
            parts.append(f"- {p}\n")
        parts.append("\n")

    if kb.comment_examples:
        parts.append("=== EXEMPLAR REPLIES (Warren's voice in the wild) ===\n")
        for c in kb.comment_examples[:8]:   # top 8 is plenty for calibration
            parts.append(
                f"[{c.platform}/{c.sentiment}]\n"
                f"  Q: {c.comment[:180]}\n"
                f"  A: {c.response[:220]}\n\n"
            )

    return "".join(parts)


def _word_count(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# Grade derivation
# ---------------------------------------------------------------------------

def _derive_grade(issues: list[dict]) -> str:
    criticals = sum(1 for i in issues if i.get("severity") == "critical")
    warnings  = sum(1 for i in issues if i.get("severity") == "warning")
    if criticals >= 2:
        return "fail"
    if criticals == 1 or warnings >= 3:
        return "warn"
    return "pass"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def review_brand_voice(
    content: str,
    *,
    kb: Optional[KnowledgeBase] = None,
    client: anthropic.Anthropic,
    model: str = "claude-sonnet-4-5",
    kind: str = "blog",
    max_tokens: int = 2000,
) -> dict:
    """Audit content against Warren brand voice.

    Args:
        content:   Raw HTML or plain text of the generated piece.
        kb:        Loaded KnowledgeBase (loaded fresh if None).
        client:    Anthropic client.
        model:     Model to use.
        kind:      "blog" or "newsletter".
        max_tokens: Cap on response tokens.

    Returns:
        {grade, issues, summary, elapsed_seconds}
    """
    started = time.time()

    if not content or not content.strip():
        return {
            "grade": "pass",
            "issues": [],
            "summary": "No content to review.",
            "elapsed_seconds": 0.0,
        }

    kb = kb or load_knowledge_base()
    brand_context = _build_brand_context(kb)

    # Strip HTML tags for the content excerpt sent to the model.
    import re
    plain = re.sub(r"<[^>]+>", " ", content)
    plain = re.sub(r"\s+", " ", plain).strip()
    excerpt = plain[:6000]

    prompt = _REVIEW_TEMPLATE.format(
        brand_context=brand_context,
        kind=kind,
        word_count=_word_count(plain),
        content_excerpt=excerpt,
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                # Static brand rules — not cached (short enough to be cheap).
                {"type": "text", "text": _SYSTEM_STATIC},
                # The KB block is the expensive part; cache it.
                {"type": "text", "text": brand_context,
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text if resp.content else "{}"
        data = parse_json_response(raw)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.error("review_brand_voice failed: %s", exc)
        return {
            "grade": "warn",
            "issues": [],
            "summary": f"Brand review call failed: {exc}",
            "elapsed_seconds": round(time.time() - started, 2),
            "error": str(exc),
        }

    issues  = data.get("issues") or []
    summary = data.get("summary") or "Review complete."
    grade   = _derive_grade(issues)

    return {
        "grade":           grade,
        "issues":          issues,
        "summary":         summary,
        "elapsed_seconds": round(time.time() - started, 2),
    }
