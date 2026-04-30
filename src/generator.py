from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import List, Optional

import anthropic

from ._json import parse_json_response
from .brand_voice import voice_block

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Diversity / dedup helpers (run pre- and post-LLM)
# ---------------------------------------------------------------------------

def _diversity_warning(articles: List[dict]) -> Optional[str]:
    """If any one source contributes >50% of articles, return a soft warning string
    to inject into the prompt so the editor balances coverage."""
    if not articles:
        return None
    counts = Counter((a.get("source") or "Unknown") for a in articles)
    top, n = counts.most_common(1)[0]
    if n / max(len(articles), 1) > 0.5 and len(articles) >= 4:
        return (
            f"Source balance: {n}/{len(articles)} of the candidate articles come from "
            f"{top}. Be careful not to over-represent any single outlet — frame the "
            f"narrative across sources where possible, and explicitly attribute "
            f"each fact to its outlet."
        )
    return None


def _dedupe_sections(sections: List[dict]) -> List[dict]:
    """Ensure no URL appears twice across sections (LLM occasionally repeats)."""
    seen: set[str] = set()
    cleaned: List[dict] = []
    for s in sections or []:
        kept = []
        for a in s.get("articles", []) or []:
            url = (a.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            kept.append(a)
        if kept:
            s = {**s, "articles": kept}
            cleaned.append(s)
    return cleaned

_EDITOR_PERSONA = (
    "You are the editor-in-chief of Warren's UK personal-finance newsletter "
    "read by professionals, business owners, and engaged retail investors. "
    "Your voice is authoritative, plain-spoken, lightly witty, and never sales-y. "
    "You return ONLY valid JSON. No markdown fences, no prose outside the JSON object."
)

# Backward-compat alias for any external callers that import SYSTEM_PROMPT.
SYSTEM_PROMPT = _EDITOR_PERSONA

USER_TEMPLATE = """Compose this edition of the UK personal-finance newsletter from the article records below.

Each input article carries: title, url, source, published_at, category, relevance_score,
a `summary` (2-3 sentences), `key_points` (3-5 bullets), and an `excerpt` (raw text from
the original article). USE the key_points and excerpt — do not just paraphrase the summary.

{diversity_note}{angle_note}
Today's edition date is: {today_human}. Use this when you need to refer to "today".

Return a JSON object that EXACTLY matches this schema (do not add or omit keys):
{{
  "subject_line": "string (compelling, <= 70 chars, no clickbait)",
  "preheader":    "string (preview text shown next to subject in inboxes, <= 110 chars)",
  "edition_label":"string (e.g. 'Issue 42 · Wed 22 Apr 2026') — leave empty string if you have no issue number; the system will fill the date",
  "intro": "string (1 short paragraph, 2-3 sentences, framing today's themes)",
  "editor_pick": {{
      "title":   "string (the single most important article title from the input)",
      "url":     "string (its url)",
      "why":     "string (1-2 sentences on why this is the must-read)"
  }},
  "sections": [
    {{
      "heading": "string (thematic grouping, e.g. 'Mortgages & Housing')",
      "summary": "string (1 sentence framing the section)",
      "articles": [
        {{
          "title":   "string",
          "url":     "string",
          "source":  "string (publisher / outlet)",
          "blurb":   "string (1-2 sentences plain summary)",
          "why_it_matters": "string (1 sentence on the practical implication for a UK reader)"
        }}
      ],
      "commentary": "string (1-2 sentences of editor commentary closing the section)"
    }}
  ],
  "closing":   "string (warm sign-off, 1-2 sentences)",
  "signature": "string (e.g. 'The Warren Editorial Desk')"
}}

Rules:
- Group related articles into 2-5 sections by theme. Use every relevant article exactly once.
- Pick the single highest-impact article as editor_pick. It MUST also appear in its themed section.
- Use UK English (organisation, favour, programme), £ for currency, UK institutions by name.
- "why_it_matters" is concrete: what changes for the reader's money.
- No emojis. No exclamation marks. No marketing fluff.

Article records (JSON):
{articles_json}
"""


def generate_newsletter(
    article_summaries: List[dict], client: anthropic.Anthropic, model: str,
    *, editor_angle: Optional[str] = None,
) -> Optional[dict]:
    if not article_summaries:
        return None

    today = datetime.now(timezone.utc)
    today_human = today.strftime("%a %d %B %Y")
    diversity_note = _diversity_warning(article_summaries) or ""
    if diversity_note:
        diversity_note = f"⚠ {diversity_note}\n"

    angle_note = ""
    if editor_angle and editor_angle.strip():
        angle_note = (
            "\n★ EDITOR'S ANGLE (priority framing — use this as the lens for the "
            f"whole edition, including the editor_pick): {editor_angle.strip()}\n"
        )

    prompt = USER_TEMPLATE.format(
        articles_json=json.dumps(article_summaries, ensure_ascii=False, indent=2),
        today_human=today_human,
        diversity_note=diversity_note,
        angle_note=angle_note,
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=6000,
            system=[
                {"type": "text",
                 "text": voice_block(include_past_replies=True, max_replies=6),
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": _EDITOR_PERSONA},
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        data = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.error("Newsletter generation failed: %s", exc)
        return None

    for key in ("subject_line", "intro", "sections", "closing"):
        if key not in data:
            log.error("Generated newsletter missing key '%s'", key)
            return None
    # soft defaults for new fields so older callers don't break
    data.setdefault("preheader", "")
    data.setdefault("edition_label", "")
    data.setdefault("editor_pick", None)
    data.setdefault("signature", "The Warren Editorial Desk")

    # --- Server-side post-processing -----------------------------------------
    # Always overwrite edition_label with a real date so the LLM can't guess it wrong.
    data["edition_label"] = today_human
    # Dedupe articles across sections (LLM occasionally repeats).
    data["sections"] = _dedupe_sections(data.get("sections", []))
    # Diversity metadata for downstream UI/formatter.
    data["_diversity_warning"] = _diversity_warning(article_summaries)
    return data
