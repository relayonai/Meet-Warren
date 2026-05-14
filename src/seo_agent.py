"""SEO/AEO Brief — Pass 1 of the blog generation pipeline.

Analyses selected article summaries and returns a JSON contract consumed
by Passes 2 (outline) and 3 (draft) as a priority directive.

Public API:
    generate_seo_brief(article_summaries, client, model, *, editor_angle=None) -> dict | None
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import anthropic

from ._json import parse_json_response

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are an SEO and AEO (Answer Engine Optimisation) strategist for Warren, "
    "a UK personal finance publisher. You analyse article data and produce a concise "
    "JSON brief that shapes a long-form blog post to rank on Google and be cited by "
    "AI answer engines (Perplexity, ChatGPT Search, Google AI Overviews). "
    "Return ONLY valid JSON. No prose, no markdown fences."
)

_TEMPLATE = """Analyse the article records below and produce a SEO/AEO brief for a UK personal-finance blog post.

{angle_note}

Return ONLY a JSON object matching this schema exactly:
{{
  "primary_keyword": "string (main search term, 2-5 words, UK English, e.g. 'ISA allowance 2026')",
  "semantic_keywords": ["string"],
  "target_h1": "string (40-60 chars, primary keyword front-loaded)",
  "faq_seeds": [
    "string (real question a UK reader would search, e.g. 'How much can I put in an ISA in 2026?')"
  ],
  "aeo_signals": {{
    "answer_first_targets": [
      "string (section heading that needs a direct 1-sentence answer opener)"
    ],
    "speakable_candidates": [
      "string (short self-contained statement for voice/AI extraction, <= 25 words)"
    ],
    "citation_stats": [
      "string (specific stat + source, e.g. 'Inflation fell to 2.6% (ONS, Mar 2026)')"
    ]
  }},
  "schema_flags": ["FAQPage", "Speakable"],
  "meta_description_brief": "string (150-160 chars, one stat, ends with value prop)"
}}

Rules:
- primary_keyword: highest-traffic term this post should rank for
- semantic_keywords: 4-7 related LSI terms (no repeats of primary)
- faq_seeds: 3-4 questions phrased exactly as a UK reader would type into Google
- aeo_signals.answer_first_targets: 2-3 section headings needing answer-first treatment
- aeo_signals.speakable_candidates: 2-3 short statements ideal for voice search
- aeo_signals.citation_stats: 2-4 specific stats with inline attribution
- schema_flags: include "FAQPage" if faq_seeds >= 3, always include "Speakable"

Article records:
{articles_json}
"""


def generate_seo_brief(
    article_summaries: list[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    editor_angle: Optional[str] = None,
) -> Optional[dict]:
    """Generate an SEO/AEO brief from article summaries.

    Returns None on failure — the pipeline degrades gracefully.
    """
    if not article_summaries:
        return None

    angle_note = ""
    if editor_angle and editor_angle.strip():
        angle_note = (
            f"★ EDITOR'S ANGLE (brief must serve this framing): "
            f"{editor_angle.strip()}"
        )

    prompt = _TEMPLATE.format(
        angle_note=angle_note,
        articles_json=json.dumps(article_summaries[:10], ensure_ascii=False, indent=2),
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        brief = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.warning("SEO brief generation failed (will proceed without): %s", exc)
        return None

    if not isinstance(brief, dict) or "primary_keyword" not in brief:
        log.warning("SEO brief missing required keys — skipping.")
        return None
    return brief
