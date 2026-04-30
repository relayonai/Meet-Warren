"""Single source of truth for Warren's brand voice.

Both the long-form generators (blog + newsletter) and the Answer Machine
read from the same Brand Narrative + curated past replies, so the tone
across every customer-facing surface stays consistent.

Public API:
- voice_block(*, include_past_replies=True, max_replies=6) -> str
  Returns a system-prompt-ready text block describing Warren's voice
  + a small set of exemplar past replies.
- WARREN_VOICE_SYSTEM_PRELUDE: short fixed prelude for system prompts.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from .answer_machine.kb import load_knowledge_base

log = logging.getLogger(__name__)


# Short prelude prepended to every long-form generator system prompt. Keep
# it tight — this is what the LLM sees first, before the rest of the spec.
WARREN_VOICE_SYSTEM_PRELUDE = (
    "You write for Meet Warren — an AI-powered UK personal-finance planning "
    "workspace. Your voice is calm, confident, motivated, lightly witty, and "
    "judgement-free. You replace the gatekept, jargon-heavy world of personal "
    "finance with clarity. You never give regulated financial advice; you give "
    "information, scenarios, and tools so people make their own decisions with "
    "confidence. You use UK English (organisation, favour, programme), £ for "
    "currency, and reference UK institutions by name (HMRC, FCA, Bank of "
    "England, ONS). Your edge is 'AI enablers': human ingenuity wrapped around "
    "ultra-compliant AI — not generic chatbot output."
)


def _format_past_replies(comments: list, max_replies: int) -> str:
    """Pick a small, varied selection of exemplar replies — different sentiments
    and platforms — so the LLM gets a breadth of voice samples without the
    prompt ballooning."""
    if not comments:
        return ""
    by_sentiment: dict[str, list] = {}
    for c in comments:
        by_sentiment.setdefault((c.sentiment or "misc").lower(), []).append(c)
    # Round-robin one from each sentiment bucket until we hit max_replies.
    picked = []
    while len(picked) < max_replies and any(by_sentiment.values()):
        for key in list(by_sentiment.keys()):
            if not by_sentiment[key]:
                continue
            picked.append(by_sentiment[key].pop(0))
            if len(picked) >= max_replies:
                break

    parts = ["\n--- VOICE EXEMPLARS (verified past Warren replies — match this register) ---"]
    for c in picked:
        parts.append(f"\n[{c.platform}/{c.sentiment}/{'DM' if c.is_dm else 'Comment'}]")
        parts.append(f"Incoming: {c.comment[:300]}")
        parts.append(f"Warren: {c.response[:600]}")
    return "\n".join(parts)


@lru_cache(maxsize=4)
def voice_block(*, include_past_replies: bool = True, max_replies: int = 6) -> str:
    """System-prompt-ready voice block. Cached so we don't re-parse the KB
    on every generation; Answer Machine's own append/delete callbacks bust
    its cache via load_knowledge_base() which is mtime-checked, so the LRU
    here is safe across long-lived processes."""
    try:
        kb = load_knowledge_base()
    except Exception as e:
        log.warning("Could not load brand KB; falling back to prelude only: %s", e)
        return WARREN_VOICE_SYSTEM_PRELUDE

    parts = [WARREN_VOICE_SYSTEM_PRELUDE]

    if kb.brand_narrative:
        parts.append(
            "\n\n--- BRAND NARRATIVE (full source — internal voice & positioning doc) ---\n"
            + kb.brand_narrative.strip()
        )

    if kb.brand_voice_principles:
        parts.append("\n\n--- DISTILLED VOICE PRINCIPLES ---")
        for p in kb.brand_voice_principles:
            parts.append(f"- {p}")

    if include_past_replies and kb.comment_examples:
        parts.append(_format_past_replies(kb.comment_examples, max_replies))

    return "\n".join(parts)


def invalidate_voice_cache() -> None:
    """Call this if the brand KB has been edited and the cache should refresh.
    Currently unused by any caller; the LRU is small enough that process
    restart on dashboard reload handles staleness in practice."""
    voice_block.cache_clear()
