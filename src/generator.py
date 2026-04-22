from __future__ import annotations

import json
import logging
from typing import List, Optional

import anthropic

from ._json import parse_json_response

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the editor of a UK personal-finance newsletter. "
    "You return ONLY valid JSON. No markdown fences, no prose outside the JSON object."
)

USER_TEMPLATE = """Compose today's UK personal-finance newsletter from the article summaries below.

Return a JSON object that exactly matches this schema:
{{
  "subject_line": "string (compelling, <= 80 chars)",
  "intro": "string (2-3 sentences setting up today's themes)",
  "sections": [
    {{
      "heading": "string (a thematic grouping, e.g. 'Mortgages & Housing')",
      "articles": [
        {{"title": "string", "url": "string", "blurb": "string (1-2 sentences)"}}
      ],
      "commentary": "string (1-2 sentences of editor commentary)"
    }}
  ],
  "closing": "string (warm sign-off, 1-2 sentences)"
}}

Group related articles into 2-5 sections. Use every relevant article exactly once.

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
            max_tokens=4096,
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
    return data
