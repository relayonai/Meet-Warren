"""Dedicated readability + flow revision pass for generated blog posts.

Runs BEFORE the quality revision loop. Targets the two signals most
consistently below floor: Flesch reading ease and transition word density.

Public API:
- run_readability_pass(post, *, client, model, progress_cb=None) -> dict
  Returns {final_post, improved, before_flesch, after_flesch, changes}
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

import anthropic

from ._json import parse_json_response
from .blog_quality import quick_score
from .brand_voice import voice_block

log = logging.getLogger(__name__)

_READABILITY_PERSONA = (
    "You are a UK copy editor specialising in personal finance writing. "
    "Your only job in this pass is to improve sentence rhythm and flow — "
    "do NOT change facts, statistics, structure, headings, or argument. "
    "You return ONLY valid JSON. No prose outside the object."
)

_GOOD_SENTENCE = (
    "GOOD (18 words): 'Mortgage rates have fallen steadily this year, "
    "but the gap between best buys and SVRs remains wide.'"
)
_BAD_SENTENCE = (
    "BAD (47 words): 'While the Bank of England's decision to maintain "
    "its base rate at 4.75% in March was widely anticipated by markets "
    "following the stronger-than-expected services inflation data released "
    "in February, it nonetheless disappointed homeowners hoping for relief.' "
    "→ Split into two sentences."
)
_GOOD_TRANSITION = (
    "GOOD: 'Inflation fell to 2.6% in March (ONS, 2026). However, wage "
    "growth remained elevated at 5.6%, keeping the MPC cautious. As a "
    "result, further rate cuts look unlikely before August.'"
)
_BAD_TRANSITION = (
    "BAD: 'Inflation fell to 2.6% in March. Wage growth remained elevated "
    "at 5.6%. Rate cuts look unlikely before August.' — no transitions, "
    "reads as a disconnected list of facts."
)

_READABILITY_TEMPLATE = """Revise this blog post to fix ONLY sentence length and flow.

RULES — change nothing else:

1. SENTENCE LENGTH: Split any sentence over 30 words into two sentences.
   No sentence may exceed 40 words. Average target: 15-20 words.
   {good_sentence}
   {bad_sentence}

2. TRANSITION WORDS: 20-30% of sentences must begin with or contain a
   transition word/phrase. Add transitions where natural.
   Allowed: However, Therefore, As a result, Meanwhile, By contrast,
   Notably, That said, In practice, For example, Crucially, Beyond this,
   On balance, In turn, Importantly, Nevertheless, By comparison.
   Banned: Furthermore, Moreover, Leverage, Delve, Navigate the landscape.
   {good_transition}
   {bad_transition}

3. DO NOT CHANGE: facts, £ figures, percentages, citations, inline
   attribution like "(ONS, 2026)", argument, section order, headings,
   key_takeaways, faqs, sources_cited, title, meta_description, byline,
   seo_tags, visual_elements, hero_image_prompt.

Return the SAME JSON shape as the input. Only intro/sections/conclusion
text may change. Add a "_readability_changes" key listing short bullets
describing each change made (max 10 bullets).

ORIGINAL POST (JSON):
{post_json}
"""


def run_readability_pass(
    post: dict,
    *,
    client: anthropic.Anthropic,
    model: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """Run the readability + flow pass.

    Returns the improved post if Flesch or transition score improved,
    otherwise returns the original unchanged.

    Return shape:
        {
          "final_post":    dict,
          "improved":      bool,
          "before_flesch": float,
          "after_flesch":  float,
          "before_transitions": float,
          "after_transitions":  float,
          "changes":       list[str],
        }
    """
    from .exporters import to_markdown

    def _md(p: dict) -> str:
        return to_markdown(p, kind="blog")

    def _cb(msg: str) -> None:
        if progress_cb:
            progress_cb(f"readability pass: {msg}")

    _cb("scoring original")
    before_score = quick_score(_md(post), suffix=".md")
    raw = before_score.get("raw") or {}
    before_flesch = float(raw.get("readability", {}).get("flesch_reading_ease", 0))
    before_transitions = float(raw.get("transition_words", {}).get("transition_pct", 0))

    _no_change = {
        "final_post": post,
        "improved": False,
        "before_flesch": before_flesch,
        "after_flesch": before_flesch,
        "before_transitions": before_transitions,
        "after_transitions": before_transitions,
        "changes": [],
    }

    # Skip if already meets both targets
    if before_flesch >= 60 and before_transitions >= 20:
        log.info(
            "Readability pass skipped — Flesch %.1f, transitions %.1f%% both OK",
            before_flesch, before_transitions,
        )
        return _no_change

    _cb("revising sentences and flow")
    prompt = _READABILITY_TEMPLATE.format(
        good_sentence=_GOOD_SENTENCE,
        bad_sentence=_BAD_SENTENCE,
        good_transition=_GOOD_TRANSITION,
        bad_transition=_BAD_TRANSITION,
        post_json=json.dumps(post, ensure_ascii=False, indent=2),
    )

    try:
        max_tok = min(16000, max(6000, len(json.dumps(post)) // 3 + 2000))
        resp = client.messages.create(
            model=model,
            max_tokens=max_tok,
            system=[
                {
                    "type": "text",
                    "text": voice_block(include_past_replies=False, max_replies=0),
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": _READABILITY_PERSONA},
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        revised = parse_json_response(resp.content[0].text)
    except Exception as exc:
        log.warning("Readability pass LLM call failed: %s", exc)
        return _no_change

    if not isinstance(revised, dict) or "intro" not in revised:
        log.warning("Readability pass returned invalid structure — discarding.")
        return _no_change

    changes = revised.pop("_readability_changes", [])

    # Merge: only prose text fields from revised; everything else from original.
    _TEXT_KEYS = {"intro", "sections", "conclusion"}
    merged = {**post}
    for k in _TEXT_KEYS:
        if k in revised:
            merged[k] = revised[k]

    # Server-controlled and metadata fields always stay original.
    _LOCKED = (
        "published_iso", "published_human", "reading_time_minutes",
        "_diversity_warning", "_outline", "_output_basename",
        "title", "subtitle", "meta_description", "byline",
        "seo_tags", "key_takeaways", "faqs", "sources_cited",
        "visual_elements", "hero_image_prompt",
    )
    for k in _LOCKED:
        if k in post:
            merged[k] = post[k]

    _cb("re-scoring revised draft")
    after_score = quick_score(_md(merged), suffix=".md")
    raw_after = after_score.get("raw") or {}
    after_flesch = float(raw_after.get("readability", {}).get("flesch_reading_ease", 0))
    after_transitions = float(raw_after.get("transition_words", {}).get("transition_pct", 0))

    improved = after_flesch > before_flesch or after_transitions > before_transitions
    if not improved:
        log.info(
            "Readability pass did not improve scores (Flesch %.1f→%.1f, "
            "transitions %.1f%%→%.1f%%) — keeping original.",
            before_flesch, after_flesch, before_transitions, after_transitions,
        )
        return {**_no_change, "after_flesch": after_flesch,
                "after_transitions": after_transitions, "changes": changes}

    log.info(
        "Readability pass improved: Flesch %.1f→%.1f, transitions %.1f%%→%.1f%%",
        before_flesch, after_flesch, before_transitions, after_transitions,
    )
    return {
        "final_post": merged,
        "improved": True,
        "before_flesch": before_flesch,
        "after_flesch": after_flesch,
        "before_transitions": before_transitions,
        "after_transitions": after_transitions,
        "changes": changes,
    }
