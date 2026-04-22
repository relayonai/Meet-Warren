from __future__ import annotations

import json
import logging
from typing import List, Optional

import anthropic

from ._json import parse_json_response

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the editor-in-chief of a premium UK personal-finance newsletter "
    "read by professionals, business owners, and engaged retail investors. "
    "Your voice is authoritative, plain-spoken, lightly witty, and never sales-y. "
    "You return ONLY valid JSON. No markdown fences, no prose outside the JSON object."
)

USER_TEMPLATE = """Compose this edition of the UK personal-finance newsletter from the article summaries below.

Return a JSON object that EXACTLY matches this schema (do not add or omit keys):
{{
  "subject_line": "string (compelling, <= 70 chars, no clickbait)",
  "preheader":    "string (preview text shown next to subject in inboxes, <= 110 chars)",
  "edition_label":"string (e.g. 'Issue 42 · Wed 22 Apr 2026')",
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

Article summaries (JSON):
{articles_json}
"""


def generate_newsletter(
    article_summaries: List[dict], client: anthropic.Anthropic, model: str
) -> Optional[dict]:
    if not article_summaries:
        return None

    prompt = USER_TEMPLATE.format(
        articles_json=json.dumps(article_summaries, ensure_ascii=False, indent=2)
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=6000,
            system=SYSTEM_PROMPT,
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
    return data
